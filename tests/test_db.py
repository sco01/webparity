# SPDX-License-Identifier: BSD-2-Clause
from pathlib import Path

from webparity.db import DB


class FakeBundle:
    def __init__(self, visit_id: str, signatures: list[str]):
        self.id = visit_id
        self.path = Path("/nonexistent") / visit_id
        self.data = {
            "url": "https://a.example/",
            "findings": [{"signature": s, "category": "error"} for s in signatures],
        }

    def has(self, name: str) -> bool:
        return False


def test_gone_or_fixed_candidate_comes_back_as_new(tmp_path):
    db = DB(tmp_path / "webparity.db")
    db.x("INSERT INTO sites (id, url, domain) VALUES (1, 'https://a.example/', 'a.example')")
    db.record_visit(1, FakeBundle("v1", ["sig-gone", "sig-fixed", "sig-dismissed", "sig-filed"]), "w", "ok", 1)
    db.x("UPDATE candidates SET status = 'gone', note = 'gone 2026-11-20' WHERE signature = 'sig-gone'")
    db.x("UPDATE candidates SET status = 'fixed', issue = '#1' WHERE signature = 'sig-fixed'")
    db.x("UPDATE candidates SET status = 'dismissed' WHERE signature = 'sig-dismissed'")
    db.x("UPDATE candidates SET status = 'filed', issue = '#2' WHERE signature = 'sig-filed'")

    db.record_visit(1, FakeBundle("v2", ["sig-gone", "sig-fixed", "sig-dismissed", "sig-filed"]), "w", "ok", 1)
    rows = {r["signature"]: r for r in db.q("SELECT * FROM candidates")}
    assert rows["sig-gone"]["status"] == "new" and rows["sig-gone"]["note"].startswith("gone 2026-11-20 · back ")
    assert rows["sig-gone"]["note"].endswith("(was gone)")
    assert rows["sig-fixed"]["status"] == "new" and rows["sig-fixed"]["issue"] == "#1"
    assert rows["sig-fixed"]["note"].endswith("(was fixed)")
    assert rows["sig-dismissed"]["status"] == "dismissed" and rows["sig-filed"]["status"] == "filed"


def test_candidate_text_follows_the_latest_finding(tmp_path):
    db = DB(tmp_path / "webparity.db")
    db.x("INSERT INTO sites (id, url, domain) VALUES (1, 'https://a.example/', 'a.example')")
    db.record_visit(1, FakeBundle("v1", ["sig"]), "w", "ok", 1)
    db.x("UPDATE candidates SET text = 'Layoutskillnad' WHERE signature = 'sig'")
    bundle = FakeBundle("v2", ["sig"])
    bundle.data["findings"][0]["text"] = "Layout difference"
    db.record_visit(1, bundle, "w", "ok", 1)
    assert db.one("SELECT text FROM candidates WHERE signature = 'sig'")[0] == "Layout difference"


def _queue(db, rows):
    for i, (url, domain) in enumerate(rows, start=1):
        db.x("INSERT INTO sites (id, url, domain, priority) VALUES (?, ?, ?, 25)", (i, url, domain))


def test_two_workers_never_take_the_same_host(tmp_path):
    db = DB(tmp_path / "webparity.db")
    _queue(
        db,
        [
            ("https://a.example/one/", "a.example/one"),
            ("https://a.example/two/", "a.example/two"),
            ("https://b.example/", "b.example"),
        ],
    )
    servers = {"a.example": "10.0.0.1", "b.example": "10.0.0.2"}
    first = db.claim("w1", resolve=servers.get)
    second = db.claim("w2", resolve=servers.get)
    assert first["domain"] == "a.example/one" and second["domain"] == "b.example"
    assert db.claim("w3", resolve=servers.get) is None  # a.example/two waits for w1
    db.finish_site(first["id"], "clean", None)
    assert db.claim("w3", resolve=servers.get)["domain"] == "a.example/two"


def test_two_workers_never_take_hosts_on_the_same_server(tmp_path):
    db = DB(tmp_path / "webparity.db")
    _queue(
        db,
        [("https://a.example/", "a.example"), ("https://b.example/", "b.example"), ("https://c.example/", "c.example")],
    )
    servers = {"a.example": "10.0.0.1", "b.example": "10.0.0.1", "c.example": "10.0.0.3"}
    assert db.claim("w1", resolve=servers.get)["domain"] == "a.example"
    assert db.claim("w2", resolve=servers.get)["domain"] == "c.example"
    assert db.claim("w3", resolve=servers.get) is None
    # A host that doesn't resolve is not held back by the server rule
    db.x("INSERT INTO sites (id, url, domain, priority) VALUES (4, 'https://d.example/', 'd.example', 25)")
    assert db.claim("w3", resolve=servers.get)["domain"] == "d.example"
