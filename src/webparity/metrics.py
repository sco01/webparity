# SPDX-License-Identifier: BSD-2-Clause
"""Measures computed from the database, for the health page, the alerts and Prometheus (``/metrics``).

Timestamps in the database are local time, ``YYYY-MM-DD HH:MM:SS`` (see ``webparity.db.now``), so string comparison
is time comparison. Every function takes ``now`` (seconds since the epoch) so tests can use a fake clock.

The ``db`` argument is anything with ``q(sql, args)`` and ``one(sql, args)``: a ``webparity.db.DB`` or the web
interface's read-only connection.
"""

from __future__ import annotations

import time
from typing import Any

FORMAT = "%Y-%m-%d %H:%M:%S"


def ts(epoch: float) -> str:
    """Seconds since the epoch as a database timestamp (local time)."""
    return time.strftime(FORMAT, time.localtime(epoch))


def parse_ts(value: str | None) -> float | None:
    """A database timestamp as seconds since the epoch, or None."""
    if not value:
        return None
    try:
        return time.mktime(time.strptime(value[:19], FORMAT))
    except ValueError:
        return None


def _now(now: float | None) -> float:
    return time.time() if now is None else now


def visits_since(db, since: float) -> int:
    return db.one("SELECT COUNT(*) FROM own_visits WHERE started >= ?", (ts(since),))[0]


def visits_last_hour(db, now: float | None = None) -> int:
    return visits_since(db, _now(now) - 3600)


def visits_per_hour(db, now: float | None = None, hours: int = 24) -> list[tuple[str, int]]:
    """Visits per clock hour for the last ``hours`` hours, oldest first, as (``HH:00``, count); the last is now."""
    now = _now(now)
    start_hour = int(now // 3600) * 3600 - (hours - 1) * 3600
    counts = {
        row[0]: row[1]
        for row in db.q(
            "SELECT substr(started, 1, 13) AS h, COUNT(*) FROM own_visits WHERE started >= ? GROUP BY h",
            (ts(start_hour),),
        )
    }
    out = []
    for i in range(hours):
        hour = start_hour + i * 3600
        key = ts(hour)[:13]
        out.append((key[11:13] + ":00", counts.get(key, 0)))
    return out


def queue_length(db) -> int:
    return db.one("SELECT COUNT(*) FROM sites WHERE status = 'queued'")[0]


def active_workers(db, now: float | None = None, minutes: int = 15) -> list[dict[str, Any]]:
    """Workers with visits in the last ``minutes`` minutes: worker, visits, last visit."""
    rows = db.q(
        "SELECT COALESCE(worker, '') AS worker, COUNT(*) AS visits, MAX(started) AS last FROM own_visits"
        " WHERE started >= ? GROUP BY worker ORDER BY worker",
        (ts(_now(now) - minutes * 60),),
    )
    return [dict(r) for r in rows]


def worker_last_visits(db) -> dict[str, float]:
    """Every worker's latest visit, as seconds since the epoch."""
    out = {}
    for row in db.q("SELECT COALESCE(worker, '') AS worker, MAX(started) AS last FROM own_visits GROUP BY worker"):
        t = parse_ts(row["last"])
        if t is not None:
            out[row["worker"]] = t
    return out


def visits_by_status(db, since: float | None = None) -> dict[str, int]:
    if since is None:
        rows = db.q("SELECT COALESCE(status, 'unknown'), COUNT(*) FROM own_visits GROUP BY 1 ORDER BY 1")
    else:
        rows = db.q(
            "SELECT COALESCE(status, 'unknown'), COUNT(*) FROM own_visits WHERE started >= ? GROUP BY 1 ORDER BY 1",
            (ts(since),),
        )
    return {r[0]: r[1] for r in rows}


def findings_by_category(db, since: float) -> dict[str, int]:
    rows = db.q(
        "SELECT f.category, COUNT(*) FROM findings f JOIN own_visits v ON v.id = f.visit WHERE v.started >= ?"
        " GROUP BY f.category ORDER BY f.category",
        (ts(since),),
    )
    return {r[0]: r[1] for r in rows}


def candidates_by_status(db) -> dict[str, int]:
    return {r[0]: r[1] for r in db.q("SELECT status, COUNT(*) FROM candidates GROUP BY status ORDER BY status")}


def alerts_open(db) -> int:
    return db.one("SELECT COUNT(*) FROM alerts WHERE resolved IS NULL")[0]


def latest_nightly(db):
    return db.one("SELECT * FROM runs WHERE kind = 'nightly' ORDER BY started DESC, id DESC LIMIT 1")


def last_successful_nightly(db) -> float | None:
    row = db.one(
        "SELECT COALESCE(finished, started) FROM runs WHERE kind = 'nightly' AND status = 'ok'"
        " ORDER BY COALESCE(finished, started) DESC LIMIT 1"
    )
    return parse_ts(row[0]) if row else None


def current_build(db) -> str | None:
    row = db.one("SELECT build FROM own_visits WHERE build IS NOT NULL AND build != '' ORDER BY started DESC LIMIT 1")
    return row[0] if row else None


def first_visit(db) -> float | None:
    row = db.one("SELECT MIN(started) FROM own_visits")
    return parse_ts(row[0]) if row else None


# --- Prometheus ----------------------------------------------------------------------------------------------------


def _label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render(db, now: float | None = None) -> str:
    """The Prometheus text exposition format (version 0.0.4)."""
    now = _now(now)
    lines: list[str] = []

    def metric(name: str, kind: str, help_text: str, samples: list[tuple[dict[str, str], float]]) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            label_text = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
            text = str(int(value)) if float(value).is_integer() else repr(float(value))
            lines.append(f"{name}{{{label_text}}} {text}" if label_text else f"{name} {text}")

    metric(
        "webparity_visits_total",
        "counter",
        "Visits recorded, by status.",
        [({"status": s}, n) for s, n in visits_by_status(db).items()],
    )
    metric("webparity_visits_last_hour", "gauge", "Visits started in the last hour.", [({}, visits_last_hour(db, now))])
    metric("webparity_queue_length", "gauge", "Sites queued for a visit.", [({}, queue_length(db))])
    metric(
        "webparity_findings_last_hour",
        "gauge",
        "Findings from visits started in the last hour, by category.",
        [({"category": c}, n) for c, n in findings_by_category(db, now - 3600).items()],
    )
    metric(
        "webparity_candidates",
        "gauge",
        "Candidates (error signatures), by status.",
        [({"status": s}, n) for s, n in candidates_by_status(db).items()],
    )
    metric("webparity_alerts_open", "gauge", "Alerts firing now.", [({}, alerts_open(db))])
    last_ok = last_successful_nightly(db)
    metric(
        "webparity_nightly_last_success_timestamp",
        "gauge",
        "When the last successful nightly run finished, in seconds since the epoch (0: never).",
        [({}, last_ok or 0)],
    )
    metric(
        "webparity_worker_last_visit_timestamp",
        "gauge",
        "Each worker's latest visit, in seconds since the epoch.",
        [({"worker": w}, t) for w, t in sorted(worker_last_visits(db).items())],
    )
    return "\n".join(lines) + "\n"
