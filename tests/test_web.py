# SPDX-License-Identifier: BSD-2-Clause
import copy
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from webparity import metrics
from webparity.bundle import Bundle
from webparity.config import DEFAULTS
from webparity.db import DB
from webparity.web import make_server
from webparity.web.server import safe_file

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
EVIL = '<script>alert("x")</script>'
VID = "20261121-120000-abcdef"


@pytest.fixture
def setup(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config["data_dir"] = str(tmp_path)
    db = DB(tmp_path / "webparity.db")
    db.add_site("https://evil.example/")
    now = time.time()

    bundle = Bundle.create(tmp_path, f"https://evil.example/?q={EVIL}", VID)
    bundle.data["timings"] = {"load": 2.5, "total": 9.1}
    bundle.data["steps"] = {"load": {"ladybird": 2.5, "chromium": 1.2}}
    bundle.save()
    bundle.write_file("ladybird-load.png", PNG)
    bundle.write_file("chromium-load.png", PNG)
    bundle.write_file("ladybird-dom.html.gz", "<html>" + EVIL)
    bundle.add_events(
        [
            {
                "browser": "ladybird",
                "step": "load",
                "method": "log.entryAdded",
                "level": "error",
                "top": True,
                "text": EVIL,
            },
            {
                "browser": "chromium",
                "step": "load",
                "method": "log.entryAdded",
                "level": "info",
                "text": "harmless info",
            },
        ]
    )
    (tmp_path / "secret.txt").write_text("TOPSECRET")

    db.x(
        "INSERT INTO visits (id, site, url, started, seconds, worker, build, browsers, status, errors, path)"
        " VALUES (?, 1, ?, ?, 9.1, 'worker-1', 'deadbeef1234', 'ladybird,chromium', 'ok', 1, ?)",
        (VID, f"https://evil.example/?q={EVIL}", metrics.ts(now - 120), str(bundle.path)),
    )
    signature = "js:TypeError: x is <b>undefined</b>"
    db.x(
        "INSERT INTO findings (visit, site, signature, category, detector, text) VALUES (?, 1, ?, 'error', 'js', ?)",
        (VID, signature, EVIL),
    )
    db.x(
        "INSERT INTO findings (visit, site, signature, category, detector, text, known) VALUES (?, 1, 'layout:gap',"
        " 'error', 'layout', 'known one', 'issue-1')",
        (VID,),
    )
    db.x(
        "INSERT INTO candidates (signature, text, first_seen, last_seen, sites, status, issue) VALUES (?, ?, ?, ?, 4,"
        " 'filed', '1234')",
        (signature, EVIL, metrics.ts(now - 3600), metrics.ts(now)),
    )
    db.x("INSERT INTO candidates (signature, sites, status) VALUES ('crash:WebContent:SIGSEGV', 9, 'new')")
    db.x(
        "INSERT INTO runs (kind, started, finished, status) VALUES ('nightly', ?, ?, 'ok')",
        (metrics.ts(now - 7200), metrics.ts(now - 3600)),
    )
    db.x("INSERT INTO alerts (rule, started, message) VALUES ('probe_down', ?, ?)", (metrics.ts(now - 60), EVIL))
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "js-typeerror.md").write_text("# Draft title\n\n" + EVIL)
    db.x(
        "INSERT INTO issues (draft, title, state, signatures, created) VALUES ('js-typeerror.md', 'TypeError on load',"
        " 'draft', ?, ?)",
        (json.dumps([signature]), metrics.ts(now)),
    )
    db.x(
        "INSERT INTO issues (draft, title, state, created) VALUES ('../secret.txt', 'Escaping draft', 'draft', ?)",
        (metrics.ts(now),),
    )
    db.x("INSERT INTO issues (number, title, state, filed) VALUES (5678, 'Filed one', 'filed', ?)", (metrics.ts(now),))

    server = make_server(config, address=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield {"base": base, "db": db, "config": config, "signature": signature, "tmp": tmp_path}
    server.shutdown()
    server.server_close()


def get(base, path, method="GET"):
    request = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def assert_escaped(body: bytes):
    text = body.decode()
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_health_page(setup):
    status, headers, body = get(setup["base"], "/")
    assert status == 200
    assert headers["Content-Security-Policy"] == "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'"
    assert headers["X-Content-Type-Options"] == "nosniff"
    text = body.decode()
    for expected in ("visits in the last hour", "worker-1", "deadbeef1234", "probe_down", "<svg", "sites queued"):
        assert expected in text
    assert "prefers-color-scheme:dark" in text and "<script" not in text
    assert_escaped(body)


def test_findings_page_and_filters(setup):
    status, _, body = get(setup["base"], "/findings")
    assert status == 200
    text = body.decode()
    assert "js:TypeError: x is &lt;b&gt;undefined&lt;/b&gt;" in text
    assert "known one" not in text  # known findings are left out
    assert f'href="/visit/{VID}"' in text
    assert_escaped(body)
    assert "TypeError" in get(setup["base"], "/findings?family=js&detector=js")[2].decode()
    assert "TypeError" not in get(setup["base"], "/findings?family=crash")[2].decode()
    assert "TypeError" not in get(setup["base"], "/findings?detector=layout")[2].decode()
    assert "TypeError" not in get(setup["base"], "/findings?family=%25")[2].decode()


def test_candidates_page(setup):
    status, _, body = get(setup["base"], "/candidates")
    text = body.decode()
    assert status == 200
    assert text.index("crash:WebContent:SIGSEGV") < text.index("js:TypeError")  # sorted by sites
    assert 'href="https://github.com/LadybirdBrowser/ladybird/issues/1234"' in text
    assert_escaped(body)
    filtered = get(setup["base"], "/candidates?status=filed")[2].decode()
    assert "js:TypeError" in filtered and "SIGSEGV" not in filtered


def test_candidate_page(setup):
    path = "/candidates/" + urllib.parse.quote(setup["signature"], safe="")
    status, _, body = get(setup["base"], path)
    text = body.decode()
    assert status == 200
    assert "evil.example" in text
    assert f"/visit/{VID}/file/ladybird-load.png" in text and f"/visit/{VID}/file/chromium-load.png" in text
    assert_escaped(body)
    assert get(setup["base"], "/candidates/nope%3Anothing")[0] == 404


def test_visit_page(setup):
    status, _, body = get(setup["base"], f"/visit/{VID}")
    text = body.decode()
    assert status == 200
    assert "worker-1" in text and "9.1" in text and "chromium" in text
    assert f'src="/visit/{VID}/file/ladybird-load.png"' in text
    assert "Console errors (1)" in text and "harmless info" not in text
    assert "ladybird-dom.html.gz" not in text  # site content is never served
    assert_escaped(body)
    assert get(setup["base"], "/visit/nope")[0] == 404


def test_visit_files_and_traversal(setup):
    base = setup["base"]
    status, headers, body = get(base, f"/visit/{VID}/file/ladybird-load.png")
    assert status == 200 and headers["Content-Type"] == "image/png" and body == PNG
    status, headers, body = get(base, f"/visit/{VID}/file/events.jsonl")
    assert status == 200 and headers["Content-Type"].startswith("text/plain")
    for bad in (
        "..%2F..%2Fsecret.txt",
        "%2E%2E%2Fsecret.txt",
        "..",
        "ladybird-dom.html.gz",
        "missing.png",
        "%2Fetc%2Fpasswd",
        ".hidden.png",
    ):
        assert get(base, f"/visit/{VID}/file/{bad}")[0] == 404, bad
    assert get(base, f"/visit/{VID}/file/../../secret.txt")[0] == 404


def test_safe_file(tmp_path):
    (tmp_path / "a.png").write_bytes(PNG)
    (tmp_path / "sub").mkdir()
    (tmp_path / "link.png").symlink_to(tmp_path.parent / "elsewhere.png")
    assert safe_file(tmp_path, "a.png") == (tmp_path / "a.png").resolve()
    for bad in ("../a.png", "sub/a.png", "sub", "link.png", "a.html", ""):
        assert safe_file(tmp_path, bad) is None


def test_issues_and_review(setup):
    status, _, body = get(setup["base"], "/issues")
    text = body.decode()
    assert status == 200
    assert "TypeError on load" in text and "issues/5678" in text
    status, _, body = get(setup["base"], "/review")
    text = body.decode()
    assert status == 200 and 'src="/static/review.js"' in text and 'data-repository="LadybirdBrowser/ladybird"' in text
    status, headers, body = get(setup["base"], "/static/review.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript") and b"X-Webparity-Review" in body
    status, headers, body = get(setup["base"], "/api/review")
    items = json.loads(body)
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    assert [i["title"] for i in items] == ["TypeError on load", "Escaping draft"]
    assert items[0]["found"] and not items[1]["found"]
    assert "TOPSECRET" not in body.decode()  # the draft outside data/drafts is never read


def test_metrics_endpoint(setup):
    status, headers, body = get(setup["base"], "/metrics")
    assert status == 200 and headers["Content-Type"].startswith("text/plain; version=0.0.4")
    text = body.decode()
    assert "webparity_visits_last_hour 1" in text
    assert 'webparity_candidates{status="filed"} 1' in text
    assert "webparity_alerts_open 1" in text
    assert 'webparity_worker_last_visit_timestamp{worker="worker-1"}' in text


def test_healthz(setup):
    status, headers, body = get(setup["base"], "/healthz")
    assert status == 200 and headers["Content-Type"] == "application/json"
    assert json.loads(body) == {"ok": True, "visits_last_hour": 1, "db": "ok"}


def test_read_only_and_unknown(setup):
    assert get(setup["base"], "/nothing-here")[0] == 404
    assert get(setup["base"], "/", method="POST")[0] == 405
    status, _, body = get(setup["base"], "/", method="HEAD")
    assert status == 200 and body == b""


def test_new_database_is_created(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config["data_dir"] = str(tmp_path / "fresh")
    server = make_server(config, address=("127.0.0.1", 0))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        for path in ("/", "/findings", "/candidates", "/issues", "/review", "/metrics"):
            assert get(base, path)[0] == 200, path
        assert json.loads(get(base, "/healthz")[2])["visits_last_hour"] == 0
    finally:
        server.shutdown()
        server.server_close()
    assert Path(config["data_dir"], "webparity.db").exists()


def test_listens_where_config_says(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config["data_dir"] = str(tmp_path)
    config["web"]["listen"] = "127.0.0.1:0"
    server = make_server(config)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def post(base, path, body, headers=None):
    data = json.dumps(body).encode()
    default = {"Content-Type": "application/json", "X-Webparity-Review": "1"}
    request = urllib.request.Request(base + path, data=data, method="POST", headers={**default, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


DRAFT = """NOTE (not for the issue): internal background.

---

## Title
A title

## Summary
What happens.

## Steps to reproduce
1. Open it.

## Screenshots or screen recordings
case-lb.png (Ladybird) case-cr.png (Chromium)

## Unknown heading
x
"""


def test_review_actions(setup):
    base, db, tmp = setup["base"], setup["db"], setup["tmp"]
    (tmp / "drafts" / "case.md").write_text(DRAFT)
    (tmp / "drafts" / "case-lb.png").write_bytes(PNG)
    db.x(
        "INSERT INTO issues (draft, title, kind, state, note, created)"
        " VALUES (?, 'A title', 'issue', 'draft', 'gallery queue #1, class 3: x', '2026-11-21')",
        (str(tmp / "drafts" / "case.md"),),
    )
    issue_id = db.one("SELECT id FROM issues WHERE title = 'A title'")["id"]
    items = json.loads(get(base, "/api/review")[2])
    case = next(i for i in items if i["id"] == issue_id)
    assert items[0]["id"] == issue_id  # queue position first
    assert case["note"] == "internal background." and case["unknown_headings"] == ["Unknown heading"]
    assert case["fields"]["screenshots"] == "Ladybird:\n\nChromium:"
    assert [(x["file"], x["available"]) for x in case["images"]] == [("case-lb.png", True), ("case-cr.png", False)]
    status, headers, body = get(base, f"/review/image/{issue_id}/case-lb.png")
    assert status == 200 and body == PNG
    assert get(base, f"/review/image/{issue_id}/..%2Fsecret.txt")[0] == 404

    # the guards: header, origin, JSON
    assert post(base, f"/api/review/{issue_id}/reset", {}, {"X-Webparity-Review": "0"})[0] == 403
    assert post(base, f"/api/review/{issue_id}/reset", {}, {"Origin": "https://evil.example"})[0] == 403
    assert post(base, f"/api/review/{issue_id}/reset", {}, {"Content-Type": "text/plain"})[0] == 415
    assert post(base, "/api/review/99999/reset", {})[0] == 404

    status, updated = post(base, f"/api/review/{issue_id}/fields", {"fields": {"summary": "Edited."}})
    assert status == 200 and updated["changed"] and updated["fields"]["summary"] == "Edited."
    assert updated["original"]["summary"] == "What happens."
    assert post(base, f"/api/review/{issue_id}/fields", {"fields": {"nope": "x"}})[0] == 409
    status, updated = post(base, f"/api/review/{issue_id}/fields", {"fields": {"summary": "What happens."}})
    assert not updated["changed"] and db.one("SELECT fields FROM issues WHERE id = ?", (issue_id,))[0] is None
    post(base, f"/api/review/{issue_id}/fields", {"fields": {"title": "New title"}})
    status, updated = post(base, f"/api/review/{issue_id}/reset", {})
    assert updated["fields"]["title"] == "A title" and not updated["changed"]

    assert post(base, f"/api/review/{issue_id}/mark", {"number": 5678})[0] == 409  # already another issue's
    status, updated = post(base, f"/api/review/{issue_id}/mark", {"number": 9001})
    assert status == 200 and updated["state"] == "filed" and updated["number"] == 9001
    assert issue_id in [i["id"] for i in json.loads(get(base, "/api/review")[2])]  # still shown, can be undone
    status, updated = post(base, f"/api/review/{issue_id}/mark", {"number": None})
    assert updated["state"] == "draft" and updated["number"] is None
    assert post(base, f"/api/review/{issue_id}/mark", {"number": "9001"})[0] == 400
    assert DRAFT == (tmp / "drafts" / "case.md").read_text()  # the draft file is never written
