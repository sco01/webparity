# SPDX-License-Identifier: BSD-2-Clause
import time

import pytest

from webparity import metrics
from webparity.db import DB

NOW = time.mktime((2026, 11, 21, 12, 30, 0, 0, 0, -1))


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "webparity.db")


def add_visit(db, vid, when, worker="w1", status="ok", build="abc123", site=1, findings=()):
    db.x(
        "INSERT INTO visits (id, site, url, started, worker, build, status, errors) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (vid, site, f"https://site{site}.example/", metrics.ts(when), worker, build, status, len(findings)),
    )
    for signature, category in findings:
        db.x(
            "INSERT INTO findings (visit, site, signature, category, detector, text) VALUES (?, ?, ?, ?, 'd', 't')",
            (vid, site, signature, category),
        )


def test_timestamps_round_trip():
    assert metrics.parse_ts(metrics.ts(NOW)) == NOW
    assert metrics.parse_ts(None) is None
    assert metrics.parse_ts("garbage") is None


def test_counts(db):
    add_visit(db, "a", NOW - 30 * 60, "w1", findings=[("crash:x", "error"), ("layout:y", "info")])
    add_visit(db, "b", NOW - 50 * 60, "w2", status="failed")
    add_visit(db, "c", NOW - 5 * 3600, "w1", build="old")
    db.add_site("https://queued.example/")
    db.x("INSERT INTO candidates (signature, status, sites) VALUES ('crash:x', 'new', 1), ('js:z', 'filed', 3)")
    db.x("INSERT INTO alerts (rule, started) VALUES ('low_rate', '2026-11-21 10:00:00')")

    assert metrics.visits_last_hour(db, NOW) == 2
    assert metrics.queue_length(db) == 1
    assert metrics.visits_by_status(db) == {"failed": 1, "ok": 2}
    assert metrics.findings_by_category(db, NOW - 3600) == {"error": 1, "info": 1}
    assert metrics.candidates_by_status(db) == {"filed": 1, "new": 1}
    assert metrics.alerts_open(db) == 1
    assert metrics.current_build(db) == "abc123"
    assert [w["worker"] for w in metrics.active_workers(db, NOW)] == []
    assert [w["worker"] for w in metrics.active_workers(db, NOW, minutes=60)] == ["w1", "w2"]
    assert metrics.first_visit(db) == NOW - 5 * 3600


def test_visits_per_hour(db):
    add_visit(db, "a", NOW - 10 * 60)  # 12:20
    add_visit(db, "b", NOW - 20 * 60)  # 12:10
    add_visit(db, "c", NOW - 3600)  # 11:30
    add_visit(db, "d", NOW - 30 * 3600)  # too old
    hours = metrics.visits_per_hour(db, NOW)
    assert len(hours) == 24
    assert hours[-1] == ("12:00", 2)
    assert hours[-2] == ("11:00", 1)
    assert sum(c for _, c in hours) == 3


def test_nightly(db):
    assert metrics.latest_nightly(db) is None
    assert metrics.last_successful_nightly(db) is None
    db.x(
        "INSERT INTO runs (kind, started, finished, status) VALUES ('nightly', ?, ?, 'ok')",
        (metrics.ts(NOW - 7200), metrics.ts(NOW - 3600)),
    )
    db.x("INSERT INTO runs (kind, started, status) VALUES ('nightly', ?, 'failed')", (metrics.ts(NOW - 600),))
    assert metrics.latest_nightly(db)["status"] == "failed"
    assert metrics.last_successful_nightly(db) == NOW - 3600


def test_render_prometheus(db):
    add_visit(db, "a", NOW - 60, 'worker "one"', findings=[("crash:x", "error")])
    db.x("INSERT INTO candidates (signature, status) VALUES ('crash:x', 'new')")
    db.x(
        "INSERT INTO runs (kind, started, finished, status) VALUES ('nightly', ?, ?, 'ok')",
        (metrics.ts(NOW - 100), metrics.ts(NOW - 50)),
    )
    text = metrics.render(db, NOW)
    lines = text.splitlines()
    assert "# TYPE webparity_visits_total counter" in lines
    assert 'webparity_visits_total{status="ok"} 1' in lines
    assert "webparity_visits_last_hour 1" in lines
    assert "webparity_queue_length 0" in lines
    assert 'webparity_findings_last_hour{category="error"} 1' in lines
    assert 'webparity_candidates{status="new"} 1' in lines
    assert "webparity_alerts_open 0" in lines
    assert f"webparity_nightly_last_success_timestamp {int(NOW - 50)}" in lines
    assert f'webparity_worker_last_visit_timestamp{{worker="worker \\"one\\""}} {int(NOW - 60)}' in lines
    assert text.endswith("\n")


def test_render_empty_db(db):
    text = metrics.render(db, NOW)
    assert "webparity_nightly_last_success_timestamp 0" in text
    assert "webparity_visits_last_hour 0" in text
