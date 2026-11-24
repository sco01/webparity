# SPDX-License-Identifier: BSD-2-Clause
"""Alerts: rules evaluated every minute, recorded in the ``alerts`` table, and sent to a webhook and by email.

Rules (each fires at most one alert at a time):

``low_rate``
    Fewer visits in the last hour than ``alerts.min_visits_per_hour``. Not while the last hour overlaps the quiet
    hours (``worker.quiet_hours``) or a nightly run, not in the first hour after the first visit ever, and not
    before any visit.
``nightly_failed``
    The latest nightly run (``runs`` row of kind ``nightly``) ended with a status other than ``ok``, or there is no
    successful nightly run in the last 26 hours. A run still ``running`` is not a failure by itself.
``probe_down``
    A WebDriver endpoint (``alerts.probe_endpoint``, or ``endpoints.ladybird`` and ``endpoints.chromium`` when that
    is empty) does not answer ``GET /status`` within 5 seconds, twice in a row 5 seconds apart. The worker starts a
    new Ladybird WebDriver for every visit, so one refused connection is often just the gap between two visits
    (2026-11-24: three one-minute alerts in 30 hours, each a second after a visit ended).

Channels: ``alerts.webhook`` gets a POST with JSON ``{rule, state, message, time}`` (state ``firing`` or
``resolved``); ``alerts.email`` (``to``, ``from``, ``smtp`` as ``host:port``) gets a plain text mail;
``alerts.pushover`` sends a Pushover notification, with the application token and user key read from
``credentials`` (a shell-style file with ``PUSHOVER_TOKEN=`` and ``PUSHOVER_USER=`` lines, kept out of the
configuration so it can stay private). All are optional.
Notifications go out when an alert starts and when it resolves, never repeated while it keeps firing.
"""

from __future__ import annotations

import json
import logging
import smtplib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage
from pathlib import Path

from webparity import metrics

log = logging.getLogger(__name__)

RULES = ("low_rate", "nightly_failed", "probe_down")
NIGHTLY_MAX_AGE = 26 * 3600
NIGHTLY_HUNG = 6 * 3600
PROBE_TIMEOUT = 5.0
PROBE_RETRY_DELAY = 5.0
INTERVAL = 60.0


@dataclass
class Alert:
    rule: str
    message: str
    state: str = "firing"  # or "resolved"
    time: str = field(default_factory=lambda: metrics.ts(time.time()))

    def to_dict(self) -> dict:
        return asdict(self)


# --- rules ---------------------------------------------------------------------------------------------------------


def _minutes(hhmm: str) -> int:
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


def quiet_ranges(config: dict) -> list[tuple[int, int]]:
    """``worker.quiet_hours`` (a string or a list of ``HH:MM-HH:MM``) as (start, end) minutes of the day."""
    value = (config.get("worker") or {}).get("quiet_hours") or []
    if isinstance(value, str):
        value = [v for v in value.split(",") if v.strip()]
    out = []
    for item in value:
        try:
            start, end = item.split("-")
            out.append((_minutes(start), _minutes(end)))
        except ValueError:
            log.warning("ignoring bad quiet_hours entry %r", item)
    return out


def overlaps_quiet_hours(config: dict, start: float, end: float) -> bool:
    """Whether the local time span [start, end] (seconds since the epoch, at most a day long) touches quiet hours."""
    lt = time.localtime(start)
    a = lt.tm_hour * 60 + lt.tm_min + lt.tm_sec / 60
    b = a + (end - start) / 60
    for qs, qe in quiet_ranges(config):
        if qe <= qs:  # across midnight
            qe += 24 * 60
        for shift in (-24 * 60, 0, 24 * 60):
            if a < qe + shift and qs + shift < b:
                return True
    return False


def nightly_overlaps(db, start: float, end: float) -> bool:
    """Whether a nightly run was going on at some point in [start, end]: it takes the CPU (canary, regression sites,
    verification), so the workers pause above ``worker.max_load`` and fewer visits are expected."""
    fmt = "%Y-%m-%d %H:%M:%S"
    a, b = time.strftime(fmt, time.localtime(start)), time.strftime(fmt, time.localtime(end))
    stale = time.strftime(fmt, time.localtime(end - NIGHTLY_HUNG))  # a run left "running" this long counts no more
    try:
        row = db.one(
            "SELECT 1 FROM runs WHERE kind = 'nightly' AND started <= ?"
            " AND (finished >= ? OR (finished IS NULL AND started >= ?)) LIMIT 1",
            (b, a, stale),
        )
    except Exception:  # an old database without runs
        return False
    return row is not None


def check_low_rate(config: dict, db, now: float) -> Alert | None:
    minimum = int((config.get("alerts") or {}).get("min_visits_per_hour") or 0)
    if minimum <= 0:
        return None
    first = metrics.first_visit(db)
    if first is None or now - first < 3600:
        return None
    if overlaps_quiet_hours(config, now - 3600, now) or nightly_overlaps(db, now - 3600, now):
        return None
    count = metrics.visits_last_hour(db, now)
    if count >= minimum:
        return None
    return Alert("low_rate", f"{count} visits in the last hour (minimum {minimum})")


def check_nightly(config: dict, db, now: float) -> Alert | None:
    latest = metrics.latest_nightly(db)
    if latest is not None and latest["status"] not in ("ok", "running"):
        return Alert(
            "nightly_failed",
            f"the latest nightly run (#{latest['id']}, started {latest['started']}) ended with status"
            f" {latest['status'] or 'none'}",
        )
    last_ok = metrics.last_successful_nightly(db)
    if last_ok is None:
        return Alert("nightly_failed", "no successful nightly run recorded")
    if now - last_ok > NIGHTLY_MAX_AGE:
        hours = (now - last_ok) / 3600
        return Alert("nightly_failed", f"no successful nightly run in the last 26 hours (last {hours:.0f} h ago)")
    return None


def probe_endpoints(config: dict) -> list[str]:
    value = (config.get("alerts") or {}).get("probe_endpoint") or ""
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not value:
        endpoints = config.get("endpoints") or {}
        value = [endpoints[n] for n in ("ladybird", "chromium") if endpoints.get(n)]
    return [v for v in value if v.startswith(("http://", "https://"))]


def probe(endpoint: str, timeout: float = PROBE_TIMEOUT) -> str | None:
    """None when ``endpoint/status`` answers 200 within the timeout, otherwise why not."""
    url = endpoint.rstrip("/") + "/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return f"HTTP {response.status}"
            response.read(65536)
            return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:  # every failure means down
        return str(getattr(e, "reason", None) or e) or type(e).__name__


def check_probe(config: dict, db, now: float, prober: Callable[[str], str | None] = probe) -> Alert | None:
    endpoints = probe_endpoints(config)
    results: dict[str, str | None] = {}
    threads = []

    def run(endpoint: str) -> None:
        result = prober(endpoint)
        if result is not None:
            time.sleep(PROBE_RETRY_DELAY)
            result = prober(endpoint)
        results[endpoint] = result

    for endpoint in endpoints:
        t = threading.Thread(target=run, args=(endpoint,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(2 * PROBE_TIMEOUT + PROBE_RETRY_DELAY + 2)
    down = [f"{e} ({results.get(e) or 'no answer'})" for e in endpoints if e not in results or results[e] is not None]
    if not down:
        return None
    return Alert("probe_down", "WebDriver /status not answering: " + ", ".join(down))


def evaluate(config: dict, db, now: float | None = None, prober: Callable[[str], str | None] = probe) -> list[Alert]:
    """The alerts firing now."""
    now = time.time() if now is None else now
    out = []
    for check in (check_low_rate, check_nightly):
        alert = check(config, db, now)
        if alert:
            out.append(alert)
    alert = check_probe(config, db, now, prober)
    if alert:
        out.append(alert)
    for alert in out:
        alert.time = metrics.ts(now)
    return out


# --- state ---------------------------------------------------------------------------------------------------------


def step(
    config: dict,
    db,
    now: float | None = None,
    prober: Callable[[str], str | None] = probe,
    notifier: Callable[[dict, list[Alert]], None] | None = None,
) -> list[Alert]:
    """Evaluates the rules once, records starts and resolves in ``alerts``, notifies them; returns the changes."""
    now = time.time() if now is None else now
    stamp = metrics.ts(now)
    notifier = notifier or notify
    firing = {a.rule: a for a in evaluate(config, db, now, prober)}
    open_rows = {r["rule"]: r for r in db.q("SELECT * FROM alerts WHERE resolved IS NULL ORDER BY id")}
    changes: list[Alert] = []
    for rule, alert in firing.items():
        if rule not in open_rows:
            db.x("INSERT INTO alerts (rule, started, message) VALUES (?, ?, ?)", (rule, stamp, alert.message))
            changes.append(alert)
    for rule, row in open_rows.items():
        if rule not in firing:
            db.x("UPDATE alerts SET resolved = ? WHERE id = ?", (stamp, row["id"]))
            changes.append(Alert(rule, f"resolved: {row['message'] or rule}", "resolved", stamp))
    if changes:
        try:
            notifier(config, changes)
        except Exception:  # a broken channel must not stop the loop
            log.exception("notifying alerts failed")
        for alert in changes:
            db.x(
                "UPDATE alerts SET notified = ? WHERE id = (SELECT MAX(id) FROM alerts WHERE rule = ?)",
                (stamp, alert.rule),
            )
    return changes


def run_loop(config: dict, db=None, interval: float = INTERVAL, stop: threading.Event | None = None) -> None:
    """Evaluates the rules every ``interval`` seconds until ``stop`` is set (or forever)."""
    from webparity.db import DB

    db = db or DB(Path(config["data_dir"]) / "webparity.db")
    stop = stop or threading.Event()
    while not stop.is_set():
        started = time.time()
        try:
            for change in step(config, db):
                log.warning("alert %s %s: %s", change.rule, change.state, change.message)
        except Exception:
            log.exception("evaluating alerts failed")
        stop.wait(max(1.0, interval - (time.time() - started)))


# --- channels ------------------------------------------------------------------------------------------------------


def send_webhook(url: str, alert: Alert, timeout: float = 10.0) -> None:
    body = json.dumps(alert.to_dict(), ensure_ascii=False).encode()
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json", "User-Agent": "webparity-alerts"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read(65536)


def send_email(email: dict, alerts: list[Alert], timeout: float = 30.0) -> None:
    host, _, port = email["smtp"].rpartition(":")
    if not host:
        host, port = email["smtp"], "25"
    message = EmailMessage()
    message["From"] = email.get("from") or "webparity@localhost"
    message["To"] = email["to"]
    states = ", ".join(f"{a.rule} {a.state}" for a in alerts)
    message["Subject"] = f"[webparity] {states}"
    message.set_content("\n".join(f"{a.time}  {a.rule}  {a.state}\n    {a.message}\n" for a in alerts))
    with smtplib.SMTP(host, int(port or 25), timeout=timeout) as smtp:
        smtp.send_message(message)


PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def read_credentials(path: str) -> dict[str, str]:
    """``KEY=value`` lines of a shell-style file (quotes stripped, comments and other lines ignored)."""
    out = {}
    for line in Path(path).read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.replace("_", "").isalnum() and not key.startswith("#"):
            out[key] = value.strip().strip("'\"")
    return out


def send_pushover(settings: dict, title: str, message: str, priority: int | None = None, timeout: float = 20.0) -> None:
    creds = read_credentials(settings["credentials"])
    app = str(settings.get("app") or "").upper().replace("-", "_")
    token = creds.get(f"PUSHOVER_TOKEN_{app}") if app else None
    body = urllib.parse.urlencode(
        {
            "token": token or creds["PUSHOVER_TOKEN"],
            "user": creds["PUSHOVER_USER"],
            "title": title[:250],
            "message": message[:1024] or "(empty)",
            "priority": str(settings.get("priority", 0) if priority is None else priority),
        }
    ).encode()
    request = urllib.request.Request(PUSHOVER_URL, data=body, method="POST", headers={"User-Agent": "webparity-alerts"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if json.loads(response.read(65536) or b"{}").get("status") != 1:
            raise RuntimeError("Pushover did not accept the notification")


def send_text(config: dict, title: str, message: str, priority: int = -1) -> None:
    """A plain report (the nightly summary) to the Pushover channel, if configured; failures are logged."""
    pushover = (config.get("alerts") or {}).get("pushover") or {}
    if pushover.get("credentials"):
        try:
            send_pushover(pushover, title, message, priority)
        except Exception:
            log.exception("Pushover report failed")


def notify(config: dict, alerts: list[Alert]) -> None:
    """Sends alert changes to every configured channel; a failing channel is logged, not raised."""
    settings = config.get("alerts") or {}
    if settings.get("webhook"):
        for alert in alerts:
            try:
                send_webhook(settings["webhook"], alert)
            except Exception:
                log.exception("webhook for alert %s failed", alert.rule)
    email = settings.get("email") or {}
    if email.get("to") and email.get("smtp") and alerts:
        try:
            send_email(email, alerts)
        except Exception:
            log.exception("alert email failed")
    pushover = settings.get("pushover") or {}
    if pushover.get("credentials") and alerts:
        states = ", ".join(f"{a.rule} {a.state}" for a in alerts)
        try:
            send_pushover(pushover, f"webparity: {states}", "\n".join(a.message for a in alerts))
        except Exception:
            log.exception("Pushover alert failed")
