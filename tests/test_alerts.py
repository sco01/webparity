# SPDX-License-Identifier: BSD-2-Clause
import copy
import json
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from webparity import alerts, metrics
from webparity.config import DEFAULTS
from webparity.db import DB


def local(h, m=0):
    return time.mktime((2026, 11, 21, h, m, 0, 0, 0, -1))


NOW = local(12)
UP = lambda endpoint: None  # noqa: E731 - every endpoint answers


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "webparity.db")


@pytest.fixture
def config(tmp_path):
    c = copy.deepcopy(DEFAULTS)
    c["data_dir"] = str(tmp_path)
    c["alerts"]["min_visits_per_hour"] = 10
    c["worker"]["quiet_hours"] = ["01:00-02:30"]
    return c


def add_visits(db, when, n, prefix="v"):
    for i in range(n):
        db.x(
            "INSERT INTO visits (id, site, url, started, worker, status)"
            " VALUES (?, 1, 'https://a.example/', ?, 'w', 'ok')",
            (f"{prefix}{when}-{i}", metrics.ts(when)),
        )


def add_nightly(db, started, status, finished=None):
    db.x(
        "INSERT INTO runs (kind, started, finished, status) VALUES ('nightly', ?, ?, ?)",
        (metrics.ts(started), metrics.ts(finished) if finished else None, status),
    )


def rules(found):
    return sorted(a.rule for a in found)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, config, changes):
        self.calls.append([(a.rule, a.state) for a in changes])


# --- low_rate ------------------------------------------------------------------------------------------------------


def test_low_rate_fires_and_resolves(config, db):
    add_nightly(db, NOW - 7200, "ok", NOW - 6600)
    add_visits(db, NOW - 3 * 3600, 20, "old")
    add_visits(db, NOW - 600, 5)
    notified = Recorder()

    changes = alerts.step(config, db, NOW, UP, notified)
    assert [(a.rule, a.state) for a in changes] == [("low_rate", "firing")]
    assert "5 visits in the last hour (minimum 10)" in changes[0].message
    row = db.one("SELECT * FROM alerts WHERE rule = 'low_rate'")
    assert row["started"] == metrics.ts(NOW) and row["resolved"] is None and row["notified"] == metrics.ts(NOW)

    # Still firing a minute later: recorded once, no repeated notification
    assert alerts.step(config, db, NOW + 60, UP, notified) == []
    assert notified.calls == [[("low_rate", "firing")]]

    add_visits(db, NOW + 100, 10, "more")
    changes = alerts.step(config, db, NOW + 120, UP, notified)
    assert [(a.rule, a.state) for a in changes] == [("low_rate", "resolved")]
    assert notified.calls[-1] == [("low_rate", "resolved")]
    assert db.one("SELECT resolved FROM alerts WHERE rule = 'low_rate'")[0] == metrics.ts(NOW + 120)
    assert metrics.alerts_open(db) == 0


def test_low_rate_not_in_quiet_hours(config, db):
    add_visits(db, local(0) - 6 * 3600, 20)
    for when in (local(1, 30), local(2, 0), local(3, 15)):  # in quiet hours, or the last hour overlaps them
        assert alerts.check_low_rate(config, db, when) is None
    assert alerts.check_low_rate(config, db, local(3, 31)).rule == "low_rate"
    assert alerts.check_low_rate(config, db, local(0, 59)).rule == "low_rate"
    assert alerts.check_low_rate(config, db, local(0, 1)) is not None


def test_low_rate_not_during_or_just_after_a_nightly_run(config, db):
    add_visits(db, local(0) - 6 * 3600, 20)
    add_nightly(db, local(2, 45), "ok", local(3, 21))
    for when in (local(3, 0), local(3, 40), local(4, 20)):  # the last hour overlaps the run
        assert alerts.check_low_rate(config, db, when) is None
    assert alerts.check_low_rate(config, db, local(4, 22)).rule == "low_rate"
    db.x("UPDATE runs SET finished = NULL, status = 'running'")
    assert alerts.check_low_rate(config, db, local(6, 0)) is None  # still running


def test_quiet_hours_as_string_and_across_midnight(config):
    config["worker"]["quiet_hours"] = "23:30-00:30"
    assert alerts.overlaps_quiet_hours(config, local(0, 0), local(0, 10))
    assert alerts.overlaps_quiet_hours(config, local(22, 45), local(23, 45))
    assert not alerts.overlaps_quiet_hours(config, local(1, 0), local(2, 0))


def test_low_rate_not_in_first_hour_or_before_any_visit(config, db):
    assert alerts.check_low_rate(config, db, NOW) is None
    add_visits(db, NOW - 1800, 1)
    assert alerts.check_low_rate(config, db, NOW) is None
    assert alerts.check_low_rate(config, db, NOW + 1801).rule == "low_rate"


# --- nightly_failed ------------------------------------------------------------------------------------------------


def test_nightly_failed_fires_and_resolves(config, db):
    add_visits(db, NOW - 3 * 3600, 1, "a")
    config["alerts"]["min_visits_per_hour"] = 0
    notified = Recorder()

    changes = alerts.step(config, db, NOW, UP, notified)
    assert rules(changes) == ["nightly_failed"]
    assert "no successful nightly run" in changes[0].message

    add_nightly(db, NOW - 3600, "ok", NOW - 1800)
    assert [(a.rule, a.state) for a in alerts.step(config, db, NOW, UP, notified)] == [("nightly_failed", "resolved")]

    add_nightly(db, NOW + 60, "failed", NOW + 120)
    changes = alerts.step(config, db, NOW + 180, UP, notified)
    assert [(a.rule, a.state) for a in changes] == [("nightly_failed", "firing")]
    assert "status failed" in changes[0].message
    assert len(notified.calls) == 3
    assert db.one("SELECT COUNT(*) FROM alerts WHERE rule = 'nightly_failed'")[0] == 2


def test_nightly_too_old_and_running(config, db):
    add_nightly(db, NOW - 30 * 3600, "ok", NOW - 29 * 3600)
    alert = alerts.check_nightly(config, db, NOW)
    assert alert and "26 hours" in alert.message
    add_nightly(db, NOW - 600, "running")  # running is not a failure, but the last success is still too old
    assert alerts.check_nightly(config, db, NOW) is not None
    add_nightly(db, NOW - 20 * 3600, "ok", NOW - 19 * 3600)
    assert alerts.check_nightly(config, db, NOW) is None


# --- probe_down ----------------------------------------------------------------------------------------------------


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"value": {"ready": True}}).encode()
        self.send_response(200 if self.path == "/status" else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def status_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def test_probe_down_fires_and_resolves(config, db, status_server):
    add_nightly(db, NOW - 3600, "ok", NOW - 1800)
    config["alerts"]["min_visits_per_hour"] = 0
    down = closed_port()
    config["alerts"]["probe_endpoint"] = f"{status_server}, {down}"
    notified = Recorder()

    changes = alerts.step(config, db, NOW, notifier=notified)
    assert rules(changes) == ["probe_down"]
    assert down in changes[0].message and status_server not in changes[0].message

    config["alerts"]["probe_endpoint"] = status_server
    changes = alerts.step(config, db, NOW + 60, notifier=notified)
    assert [(a.rule, a.state) for a in changes] == [("probe_down", "resolved")]
    assert notified.calls == [[("probe_down", "firing")], [("probe_down", "resolved")]]


def test_probe_endpoints_default_to_browsers(config):
    config["endpoints"] = {"ladybird": "http://lb:9616", "chromium": "http://cr:9515", "firefox": "ws://ff:9222"}
    assert alerts.probe_endpoints(config) == ["http://lb:9616", "http://cr:9515"]
    config["alerts"]["probe_endpoint"] = ["http://x:1"]
    assert alerts.probe_endpoints(config) == ["http://x:1"]


def test_probe_reports_http_errors(status_server):
    assert alerts.probe(status_server) is None
    assert alerts.probe(status_server + "/nope") == "HTTP 404"
    assert alerts.probe(closed_port(), timeout=2) is not None


# --- all three at once, and the channels ---------------------------------------------------------------------------


def test_evaluate_all_three(config, db):
    add_visits(db, NOW - 3 * 3600, 1)
    config["alerts"]["probe_endpoint"] = closed_port()
    assert rules(alerts.evaluate(config, db, NOW)) == ["low_rate", "nightly_failed", "probe_down"]


class Collector(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).received.append((self.headers["Content-Type"], json.loads(body)))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_webhook(config):
    handler = type("H", (Collector,), {"received": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config["alerts"]["webhook"] = f"http://127.0.0.1:{server.server_address[1]}/hook"
        alerts.notify(
            config,
            [
                alerts.Alert("low_rate", "3 visits", "firing", "2026-11-21 12:00:00"),
                alerts.Alert("probe_down", "ok", "resolved"),
            ],
        )
    finally:
        server.shutdown()
        server.server_close()
    assert len(handler.received) == 2
    content_type, first = handler.received[0]
    assert content_type == "application/json"
    assert first == {"rule": "low_rate", "state": "firing", "message": "3 visits", "time": "2026-11-21 12:00:00"}
    assert handler.received[1][1]["state"] == "resolved"


def test_webhook_failure_is_not_raised(config):
    config["alerts"]["webhook"] = closed_port()
    alerts.notify(config, [alerts.Alert("low_rate", "x")])


def test_email(config, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append((host, port))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    config["alerts"]["email"] = {"to": "ops@example.org", "from": "wp@example.org", "smtp": "mail.example.org:2525"}
    alerts.notify(config, [alerts.Alert("nightly_failed", "build broke")])
    assert sent[0] == ("mail.example.org", 2525)
    assert sent[1]["To"] == "ops@example.org"
    assert "nightly_failed firing" in sent[1]["Subject"]
    assert "build broke" in sent[1].get_content()


def test_notifier_failure_does_not_stop_step(config, db):
    def broken(config, changes):
        raise RuntimeError("down")

    add_visits(db, NOW - 3 * 3600, 1)
    assert rules(alerts.step(config, db, NOW, UP, broken)) == ["low_rate", "nightly_failed"]
    assert metrics.alerts_open(db) == 2


def test_run_loop_stops(config, db, monkeypatch):
    stop = threading.Event()
    calls = []

    def fake_step(config, db):
        calls.append(1)
        stop.set()
        return []

    monkeypatch.setattr(alerts, "step", fake_step)
    alerts.run_loop(config, db, interval=0.01, stop=stop)
    assert calls == [1]


def test_pushover(config, tmp_path, monkeypatch):
    creds = tmp_path / "pushover.conf"
    creds.write_text('# comment\nPUSHOVER_TOKEN="tok"\nPUSHOVER_USER=usr\nPUSHOVER_TOKEN_WP=apptok\n')
    sent = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b'{"status":1}'

    def fake_urlopen(request, timeout):
        sent.append((request.full_url, dict(urllib.parse.parse_qsl(request.data.decode()))))
        return Response()

    monkeypatch.setattr(alerts.urllib.request, "urlopen", fake_urlopen)
    config["alerts"]["pushover"] = {"credentials": str(creds), "app": "", "priority": 0}
    alerts.notify(config, [alerts.Alert("low_rate", "12 visits")])
    url, form = sent[0]
    assert url == alerts.PUSHOVER_URL
    assert form["token"] == "tok" and form["user"] == "usr" and "low_rate firing" in form["title"]
    config["alerts"]["pushover"]["app"] = "wp"
    alerts.send_text(config, "webparity nightly: ok", "Last 24 h: 1 visits", -1)
    assert sent[1][1]["token"] == "apptok" and sent[1][1]["priority"] == "-1"


def test_pushover_failure_is_not_raised(config, tmp_path):
    config["alerts"]["pushover"] = {"credentials": str(tmp_path / "missing.conf")}
    alerts.notify(config, [alerts.Alert("low_rate", "x")])
    alerts.send_text(config, "t", "m")
