# SPDX-License-Identifier: BSD-2-Clause
"""The issue workflow: drafts, checks, registering, the GitHub watcher (against a fake local server), verification
(with fake probe results), the index and the gallery import."""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from webparity.db import DB
from webparity.issues import review
from webparity.issues import verify as verify_mod
from webparity.issues.common import split_front_matter, split_source, update_front_matter
from webparity.issues.drafts import check_draft, create_draft
from webparity.issues.github import watch
from webparity.issues.import_gallery import import_gallery, parse_status
from webparity.issues.index import generate_index
from webparity.issues.register import register, register_comment
from webparity.issues.verify import assess, format_report, result_text, same, verify
from webparity.probe import Result

SIG = "js-error:TypeError: x is not a function"


@pytest.fixture
def env(tmp_path: Path):
    config = {
        "data_dir": str(tmp_path / "data"),
        "browsers": ["ladybird", "chromium"],
        "github": {"repository": "Owner/repo", "user_agent": "webparity-test"},
        "ladybird": {"build_info": str(tmp_path / "build.json")},
    }
    db = DB(tmp_path / "data" / "webparity.db")
    for i, domain in enumerate(["a.example", "b.example", "c.example"], start=1):
        db.add_site(f"https://{domain}/")
        db.x("UPDATE sites SET status = 'done' WHERE id = ?", (i,))
        db.x(
            "INSERT INTO visits (id, site, url, started, build, status) VALUES (?, ?, ?, ?, ?, 'done')",
            (f"v{i}", i, f"https://{domain}/", f"2026-11-2{i} 10:00:00", f"abc{i}def0123"),
        )
        if i < 3:
            db.x(
                "INSERT INTO findings (visit, site, signature, category, text) VALUES (?, ?, ?, 'error', ?)",
                (f"v{i}", i, SIG, "TypeError: x is not a function"),
            )
    db.x(
        "INSERT INTO candidates (signature, text, first_seen, last_seen, sites) VALUES (?, ?, ?, ?, 2)",
        (SIG, "TypeError: x is not a function", "2026-11-21", "2026-11-22"),
    )
    return config, db, tmp_path


def fill_draft(path: Path, test_case: Path, steps: str = "1. Open the test case below.") -> None:
    text = path.read_text(encoding="utf-8")
    meta, body = split_front_matter(text)
    body = body.replace(body[body.index("## Title") : body.index("## Notes")], "")
    body = (
        "## Title\nCalling x() throws a TypeError\n\n## Summary\nLadybird throws.\n\n"
        f"## Steps to reproduce\n{steps}\n\n## Expected behavior\nAs in Chromium:\n```\nok\n```\n\n"
        "## Actual behavior\n```\nTypeError\n```\n\n" + body
    )
    path.write_text(text[: text.index("\n---\n") + 5] + body, encoding="utf-8")
    update_front_matter(path, test_case=str(test_case))


# --- drafts ----------------------------------------------------------------------------------------------------------
def test_create_draft_fills_template_and_inserts_row(env):
    config, db, _ = env
    path = create_draft(config, db, SIG)
    assert path.parent == Path(config["data_dir"]) / "drafts" and path.suffix == ".md"
    text = path.read_text(encoding="utf-8")
    meta, body = split_front_matter(text)
    assert meta["signatures"] == [SIG] and meta["kind"] == "issue" and meta["test_case"].startswith("test-cases/")
    for heading in ("## Title", "## Summary", "## Steps to reproduce", "## Expected behavior", "## Actual behavior"):
        assert heading in body
    assert "a.example" in body and "b.example" in body and "abc2def0123" in body and "2 site(s)" in body
    row = db.one("SELECT * FROM issues WHERE draft = ?", (str(path),))
    assert row["state"] == "draft" and json.loads(row["signatures"]) == [SIG]
    cand = db.one("SELECT * FROM candidates WHERE signature = ?", (SIG,))
    assert cand["status"] == "investigating" and cand["issue"] == f"draft:{path.stem}"
    second = create_draft(config, db, SIG, title="A title")
    assert second != path and second.name == "a-title.md"


def test_check_draft(env):
    config, db, tmp = env
    path = create_draft(config, db, SIG)
    problems = check_draft(path)
    assert any("TODO" in p for p in problems)
    assert any("does not exist" in p for p in problems)

    case = tmp / "case"
    case.mkdir()
    (case / "index.html").write_text(
        '<pre id="out"></pre><script src="https://cdn.example.com/lib.js"></script>'
        '<svg xmlns="http://www.w3.org/2000/svg"></svg><!-- me@example.org -->'
    )
    fill_draft(path, case)
    problems = check_draft(path)
    assert any("external URL https://cdn.example.com/lib.js" in p for p in problems)
    assert any("email address" in p for p in problems)
    assert not any("w3.org" in p for p in problems)

    (case / "index.html").write_text('<pre id="out"></pre><script>out.textContent = "ok" // done</script>')
    assert check_draft(path) == []

    text = path.read_text(encoding="utf-8")
    long_title = "Ä " + "x" * 120
    text = text.replace("Calling x() throws a TypeError", long_title)
    text = text.replace("Ladybird throws.", "See /Users/someone/case.html on 192.168.1.5")
    text = text.replace("1. Open the test case below.", "")
    path.write_text(text, encoding="utf-8")
    problems = check_draft(path)
    assert any("characters" in p for p in problems)
    assert any("English" in p for p in problems)
    assert any("local path" in p for p in problems)
    assert any("IP address (192.168.1.5)" in p for p in problems)
    assert "no steps to reproduce" in problems


def test_notes_section_is_not_checked(env):
    config, db, tmp = env
    path = create_draft(config, db, SIG)
    case = tmp / "c.html"
    case.write_text('<pre id="out"></pre>')
    fill_draft(path, case)
    path.write_text(path.read_text(encoding="utf-8") + "Hittad på någon sajt, 10.0.0.1\n", encoding="utf-8")
    assert check_draft(path) == []


# --- register --------------------------------------------------------------------------------------------------------
def test_register_and_comment(env):
    config, db, tmp = env
    path = create_draft(config, db, SIG)
    row = register(config, db, path.stem, "#123", filed="2026-11-22")
    assert row["number"] == 123 and row["state"] == "filed" and row["filed"] == "2026-11-22"
    cand = db.one("SELECT * FROM candidates WHERE signature = ?", (SIG,))
    assert cand["status"] == "filed" and cand["issue"] == "#123"
    meta, _ = split_front_matter(path.read_text(encoding="utf-8"))
    assert meta["issue"] == "123" and meta["filed"] == "2026-11-22"
    assert db.one("SELECT COUNT(*) FROM issues")[0] == 1

    other = create_draft(config, db, SIG, title="Other")
    with pytest.raises(ValueError):
        register(config, db, other, 123)

    comment = Path(config["data_dir"]) / "drafts" / "123-comment.md"
    comment.write_text("## Comment\nStill happens.\n", encoding="utf-8")
    row = register_comment(config, db, comment, 123, posted="2026-11-23")
    assert row["kind"] == "comment" and row["state"] == "posted" and row["number"] is None
    assert row["note"] == "comment on #123"
    assert db.one("SELECT status FROM candidates")[0] == "filed"


# --- the watcher -----------------------------------------------------------------------------------------------------
class FakeGitHub:
    def __init__(self):
        self.issues: dict[int, dict] = {}
        self.timelines: dict[int, list] = {}
        self.requests: list[tuple[str, dict]] = []
        self.remaining = 50
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.requests.append((self.path, dict(self.headers)))
                parts = self.path.split("?")[0].strip("/").split("/")
                if fake.remaining <= 0:
                    self.send_response(403)
                    self.send_header("X-RateLimit-Remaining", "0")
                    self.send_header("X-RateLimit-Reset", "9999999999")
                    self.end_headers()
                    return
                if parts[-1] == "timeline":
                    body = fake.timelines.get(int(parts[-2]), [])
                else:
                    body = fake.issues.get(int(parts[-1]))
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                etag = f'"{hash(data) & 0xFFFFFFF:x}"'
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    return
                fake.remaining -= 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("ETag", etag)
                self.send_header("X-RateLimit-Remaining", str(fake.remaining))
                self.send_header("X-RateLimit-Reset", "9999999999")
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def github(env):
    config, _, _ = env
    fake = FakeGitHub()
    config["github"]["api"] = fake.url
    yield fake
    fake.close()


def test_watch_follows_issue_until_closed(env, github):
    config, db, _ = env
    register(config, db, create_draft(config, db, SIG), 42)
    github.issues[42] = {"number": 42, "title": "T", "state": "open", "closed_at": None}
    assert watch(config, db) == []
    path, headers = github.requests[0]
    assert path == "/repos/Owner/repo/issues/42"
    assert headers["User-Agent"] == "webparity-test" and "Authorization" not in headers

    assert watch(config, db) == []  # unchanged: answered from the ETag cache
    assert github.requests[-1][1].get("If-None-Match")
    assert github.remaining == 49

    github.issues[42] = {
        "number": 42,
        "title": "T",
        "state": "closed",
        "state_reason": "completed",
        "closed_at": "2026-11-23T10:00:00Z",
    }
    github.timelines[42] = [
        {"event": "cross-referenced", "source": {"issue": {"number": 7, "pull_request": {"merged_at": None}}}},
        {"event": "cross-referenced", "source": {"issue": {"number": 9, "pull_request": {"merged_at": "2026-11-23"}}}},
        {"event": "closed", "commit_id": "0123456789abcdef"},
    ]
    changes = watch(config, db)
    assert changes == [
        {
            "number": 42,
            "title": changes[0]["title"],
            "old": "filed",
            "new": "closed",
            "closed": "2026-11-23",
            "closed_by": "#9",
            "requeued": 2,
        }
    ]
    row = db.one("SELECT * FROM issues WHERE number = 42")
    assert row["state"] == "closed" and row["closed"] == "2026-11-23" and row["closed_by"] == "#9"
    assert db.one("SELECT status FROM candidates")[0] == "fixed?"
    queued = {r["domain"] for r in db.q("SELECT domain FROM sites WHERE status = 'queued' AND recheck = 1")}
    assert queued == {"a.example", "b.example"}
    assert Path(config["data_dir"], "github-cache.json").is_file()

    github.issues[42] = {"number": 42, "title": "T", "state": "open", "closed_at": None}
    changes = watch(config, db)
    assert changes[0]["new"] == "filed" and changes[0]["closed_by"] is None
    assert db.one("SELECT status FROM candidates")[0] == "filed"


def test_watch_stops_at_rate_limit(env, github):
    config, db, _ = env
    register(config, db, create_draft(config, db, SIG), 42)
    github.remaining = 0
    changes = watch(config, db)
    assert changes and changes[0]["number"] is None and "rate limit" in changes[0]["error"]
    assert db.one("SELECT state FROM issues")[0] == "filed"


def test_watch_checks_open_issues_first(env, github):
    config, db, _ = env
    for number, state, closed_by in ((10, "closed", "#1"), (11, "closed", None), (12, "filed", None)):
        sql = "INSERT INTO issues (number, kind, state, closed_by) VALUES (?, 'issue', ?, ?)"
        db.x(sql, (number, state, closed_by))
        github.issues[number] = {"number": number, "title": "T", "state": "closed" if state == "closed" else "open"}
    github.remaining = 1
    watch(config, db)
    assert github.requests[0][0].endswith("/issues/12")


# --- verification ----------------------------------------------------------------------------------------------------
def make_issue(config, db, tmp, number, state, html, expected, actual, steps="1. Open the test case below.", **kw):
    case = tmp / f"case{number}"
    case.mkdir()
    if isinstance(html, dict):
        for name, content in html.items():
            (case / name).write_text(content)
    else:
        (case / "index.html").write_text(html)
    draft = tmp / f"draft{number}.md"
    draft.write_text(
        f"## Title\nIssue {number}\n\n## Steps to reproduce\n{steps}\n\n"
        f"## Expected behavior\n```\n{expected}\n```\n\n## Actual behavior\n```\n{actual}\n```\n"
    )
    db.x(
        "INSERT INTO issues (number, draft, title, state, signatures, test_case, closed) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (number, str(draft), f"Issue {number}", state, json.dumps([SIG]), str(case), kw.get("closed")),
    )


def fake_probe(answers: dict[int, dict[str, str | None]], calls: list):
    def probe(targets, script=None, browsers=None, wait=None, root=None, **kw):
        calls.append({"targets": targets, "script": script, "browsers": browsers, "wait": wait, "root": root})
        out = []
        for t in targets:
            rel = Path(t).resolve().relative_to(Path(root).resolve()).as_posix()
            number = int(rel.split("/")[0])
            for b in browsers:
                value = answers.get(number, {}).get(b)
                url = f"http://127.0.0.1:1234/{rel}"
                out.append(Result(url, b, error="timeout") if value == "ERR" else Result(url, b, value=value))
        return out

    return probe


def test_verify(env, monkeypatch):
    config, db, tmp = env
    Path(config["ladybird"]["build_info"]).write_text(json.dumps({"commit": "feedface1234567", "date": "2026-11-20"}))
    db.x("UPDATE candidates SET status = 'fixed?'")
    page = '<pre id="out">running...</pre>'
    make_issue(config, db, tmp, 1, "closed", page, "width: 100", "width: 0", closed="2026-11-18")  # fixed
    make_issue(config, db, tmp, 2, "closed", page, "ok", "broken", closed="2026-11-18")  # not-fixed
    make_issue(config, db, tmp, 3, "closed", page, "ok", "broken", closed="2026-11-18")  # changed
    make_issue(config, db, tmp, 4, "filed", page, "ok", "broken")  # still broken
    make_issue(config, db, tmp, 5, "filed", page, "ok", "broken")  # fixed silently (like expected)
    make_issue(config, db, tmp, 6, "filed", "<p>no out</p>", "ok", "broken")  # manual
    make_issue(config, db, tmp, 7, "filed", page, "ok", "broken", steps="1. Click the button.")  # manual
    make_issue(config, db, tmp, 8, "filed", page, "ok", "broken")  # error: Chromium only has the placeholder
    make_issue(config, db, tmp, 9, "closed", page, "ok", "broken", closed="2026-11-21")  # waiting for build
    make_issue(
        config,
        db,
        tmp,
        10,
        "filed",
        {"index.html": '<pre id="out"></pre><iframe src="page.html">', "page.html": "<p>x</p>"},
        "ok",
        "broken",
    )
    make_issue(config, db, tmp, 11, "filed", "import http.server\n", "ok", "broken")  # server script
    make_issue(config, db, tmp, 12, "not-planned", page, "ok", "broken")
    make_issue(config, db, tmp, 13, "filed", page, "ok", "broken")  # Ladybird errored
    answers = {
        1: {"ladybird": "width: 99.5", "chromium": "width: 100"},
        2: {"ladybird": "broken", "chromium": "ok"},
        3: {"ladybird": "something else", "chromium": "ok"},
        4: {"ladybird": "broken", "chromium": "ok"},
        5: {"ladybird": "ok", "chromium": None},
        8: {"ladybird": "running...", "chromium": "running..."},
        9: {"ladybird": "broken", "chromium": "ok"},
        10: {"ladybird": "ok", "chromium": "ok"},
        13: {"ladybird": "ERR", "chromium": "ok"},
    }
    calls: list = []
    monkeypatch.setattr(verify_mod, "probe", fake_probe(answers, calls))
    results = {r["number"]: r for r in verify(config, db)}
    assert {n: r["result"] for n, r in results.items()} == {
        1: "fixed",
        2: "not-fixed",
        3: "changed",
        4: "still-broken",
        5: "fixed-silently",
        6: "manual",
        7: "manual",
        8: "error",
        9: "waiting-for-build",
        10: "fixed-silently",
        11: "manual",
        12: "closed-without-fix",
        13: "error",
    }
    assert results[1]["group"] == "fixed" and results[4]["group"] == "still broken" and results[8]["group"] == "error"
    assert results[7]["reason"] == "interactive steps" and results[11]["reason"] == "needs a server script"
    assert results[13]["reason"] == "timeout" and results[1]["build"] == "feedface123"
    assert calls[0]["browsers"] == ["ladybird", "chromium"] and calls[0]["wait"] == 4.0
    assert 'getElementById("out")' in calls[0]["script"]
    assert len(calls) == 1 and len(calls[0]["targets"]) == 9
    staged = Path(config["data_dir"]) / "verify" / "10"
    assert (staged / "index.html").is_file() and (staged / "page.html").is_file()

    row = db.one("SELECT * FROM issues WHERE number = 1")
    assert row["verified_result"] == "fixed" and row["verified"]
    assert db.one("SELECT status FROM candidates")[0] == "fixed"

    report = format_report(list(results.values()), "feedface123")
    assert "## fixed (1)" in report and "#2 Issue 2" in report and "ladybird: `broken`" in report

    calls.clear()
    again = verify(config, db)
    assert 1 not in {r["number"] for r in again}  # verified as fixed: skipped by default
    assert {r["number"] for r in verify(config, db, numbers=[1])} == {1}


def test_verify_batches_and_firefox(env, monkeypatch):
    config, db, tmp = env
    config["browsers"].append("firefox")
    for n in range(1, 13):
        make_issue(config, db, tmp, n, "filed", '<pre id="out"></pre>', "ok", "broken")
    calls: list = []
    answers = {n: {"ladybird": "ok", "chromium": "broken", "firefox": "ok"} for n in range(1, 13)}
    monkeypatch.setattr(verify_mod, "probe", fake_probe(answers, calls))
    results = verify(config, db, record=False)
    assert [len(c["targets"]) for c in calls] == [10, 2]
    assert calls[0]["browsers"] == ["ladybird", "chromium", "firefox"]
    assert {r["result"] for r in results} == {"fixed-silently"}
    assert db.one("SELECT COUNT(*) FROM issues WHERE verified IS NOT NULL")[0] == 0


def test_build_info_text_file(env):
    config, db, _ = env
    Path(config["ladybird"]["build_info"]).write_text("b8625076d82 2026-11-22 01:26:07\n")
    assert verify_mod.build_info(config, db) == ("b8625076d82", "2026-11-22")


def test_result_text():
    assert result_text("```\nok\n```\n") == "ok\n"
    assert result_text("As in Chromium and Firefox:\n\na: 1\nb: 2\n") == "a: 1\nb: 2"
    assert result_text("a: 1\nb: 2\n") == "a: 1\nb: 2"
    assert result_text("\n") is None


def test_same_and_assess():
    assert same("width: 100\nheight: 50", "width: 101\nheight: 50")
    assert same("w 1000", "w 1019") and not same("w 1000", "w 1030")
    assert not same("a 1", "b 1") and not same("", "")
    base = {"expected": "ok", "actual": "bad", "start": "running...", "state": "filed", "closed": None}
    assert assess({**base, "ladybird": "ok", "chromium": "ok"}, None) == "fixed-silently"
    # Chromium giving the reported bug is not a reference
    assert assess({**base, "ladybird": "bad", "chromium": "bad"}, None) == "error"
    assert assess({**base, "ladybird": "", "chromium": "ok"}, None) == "error"
    closed = {**base, "state": "closed", "closed": "2026-11-01"}
    assert assess({**closed, "ladybird": "ok", "chromium": "x"}, "2026-11-02") == "fixed"
    assert assess({**closed, "ladybird": "ok", "chromium": "x"}, "2026-11-01") == "fixed-build-older"


# --- index and import ------------------------------------------------------------------------------------------------
def test_generate_index(env):
    config, db, _ = env
    register(config, db, create_draft(config, db, SIG, title="Filed | one"), 5)
    create_draft(config, db, SIG, title="Still a draft")
    db.x("UPDATE issues SET state = 'closed', closed = '2026-11-23', closed_by = '#9', verified_result = 'fixed'")
    db.x(
        "UPDATE issues SET state = 'draft', closed = NULL, closed_by = NULL, verified_result = NULL"
        " WHERE number IS NULL"
    )
    text = generate_index(config, db)
    assert (Path(config["data_dir"]) / "INDEX.md").read_text() == text
    assert "| [#5](https://github.com/Owner/repo/issues/5) | Filed \\| one | closed |" in text
    assert "2026-11-23 (#9)" in text and "| fixed" in text and SIG in text
    assert "## Drafts" in text and "Still a draft" in text


def test_split_source():
    assert split_source("<p>one</p>") == [(None, "<p>one</p>\n")]
    parts = split_source("<!-- test.html -->\n<p>a</p>\n<!-- page.html -->\n<p>b</p>\n")
    assert parts == [("test.html", "<p>a</p>\n"), ("page.html", "<p>b</p>\n")]


def test_parse_status():
    s = parse_status("closed (completed) 2026-11-06 – rättad i 4652e25c17b, verifierad på Linux 6fba5b7 (2026-11-19)")
    assert s["state"] == "closed" and s["closed"] == "2026-11-06" and s["closed_by"] == "commit 4652e25c17b"
    assert s["verified_result"] == "fixed" and s["verified"] == "2026-11-19"
    s = parse_status("closed (completed) 2026-11-08 – fixed by #13785, #13894 – rättad i f7ecdca8c76")
    assert s["closed_by"] == "#13785"
    assert parse_status("closed (duplicate) 2026-11-14")["state"] == "duplicate"
    assert parse_status("open – PR [#14526](x) (öppen)") == {
        "state": "filed",
        "closed": None,
        "closed_by": None,
        "verified": None,
        "verified_result": None,
    }


def test_import_gallery(env):
    config, db, tmp = env
    gallery = tmp / "gallery"
    (gallery / "issues").mkdir(parents=True)
    (gallery / "testfall" / "folder-case").mkdir(parents=True)
    (gallery / "testfall" / "folder-case" / "test.html").write_text('<pre id="out"></pre>')
    (gallery / "testfall" / "img-case").mkdir()
    for name in ("test.html", "bild.png", "other.png"):  # bild.png is referenced by the source, other.png is not
        (gallery / "testfall" / "img-case" / name).write_text(name)
    (gallery / "issues" / "INDEX.md").write_text(
        "# Issues\n\n| Issue | Filed | Status | Title | Draft |\n|---|---|---|---|---|\n"
        "| [#100](u) | 2026-11-01 | closed (completed) 2026-11-03 (abcdef12345, PR #101) | First \\| one"
        " | [one.md](one.md) |\n"
        "| [#200](u) | 2026-11-02 | open | Second | [two.md](two.md) |\n"
        "\n## Kommentarer\n\n| Issue | Fil |\n|---|---|\n"
        "| [#100](u) | [100-comment.md](100-comment.md) – POSTED 2026-11-04 |\n"
        "| [#100](u) | [100-unposted.md](100-unposted.md) (draft) |\n"
        "| [#100](u) | [100-ej.md](100-ej.md) (utkast 2026-11-09, ej postad) |\n"
        "| [#100](u) | [100-old.md](100-old.md) |\n"
        "| [#100](u) | [100-done.md](100-done.md) – utkast 2026-11-21 – POSTAD av Kim 2026-11-21 |\n"
        "\n## Queue\n\n| # | Class | Draft | Effect |\n|---|---|---|---|\n| 1 | 3 | [q.md](q.md) | x |\n"
    )
    (gallery / "issues" / "one.md").write_text(
        "# Issue draft\n\nTest case testfall/img-case/test.html.\n\n## Title\nFirst | one\n\n## Steps to reproduce\n"
        '1. Open it.\n\n## HTML/SVG/etc. source for a reduced test case\n<!-- test.html -->\n<pre id="out"></pre>\n'
        '<img src="bild.png?1">\n'
        "<!-- page.html -->\n<p>b</p>\n\n## Log output\nN/A\n"
    )
    (gallery / "issues" / "two.md").write_text(
        "# Issue draft\n\nTest case testfall/folder-case/.\n\n## Title\nSecond\n\n"
        "## HTML/SVG/etc. source for a reduced test case\nN/A\n"
    )
    (gallery / "issues" / "100-comment.md").write_text("A comment.\n")
    (gallery / "issues" / "100-unposted.md").write_text("Not posted.\n")
    (gallery / "issues" / "100-ej.md").write_text("Not posted either.\n")
    (gallery / "issues" / "100-old.md").write_text("Old, unmarked.\n")
    (gallery / "issues" / "100-done.md").write_text("Drafted, then posted.\n")
    (gallery / "issues" / "q.md").write_text(
        "NOTE\n\n---\n\n## Title\nQueued one\n\n## HTML/SVG/etc. source for a reduced test case\n<p>q</p>\n"
    )
    db.x("UPDATE candidates SET issue = '#200', status = 'filed'")

    counts = import_gallery(config, db, gallery)
    assert counts == {"issues": 2, "comments": 2, "queued": 3, "test_cases": 3, "missing_drafts": []}
    queued = db.one("SELECT * FROM issues WHERE title = 'Queued one'")
    assert queued["state"] == "draft" and queued["kind"] == "issue" and queued["number"] is None
    assert queued["note"].startswith("gallery queue #1, class 3") and queued["test_case"] == "test-cases/imported/q"
    unposted = db.one("SELECT * FROM issues WHERE draft LIKE '%/100-unposted.md'")
    assert unposted["state"] == "draft" and unposted["kind"] == "comment"
    assert db.one("SELECT state FROM issues WHERE draft LIKE '%/100-ej.md'")["state"] == "draft"
    assert db.one("SELECT 1 FROM issues WHERE draft LIKE '%/100-old.md'") is None
    assert db.one("SELECT state FROM issues WHERE draft LIKE '%/100-done.md'")["state"] == "posted"
    first = db.one("SELECT * FROM issues WHERE number = 100")
    assert first["title"] == "First | one" and first["state"] == "closed" and first["closed_by"] == "#101"
    assert first["filed"] == "2026-11-01" and first["closed"] == "2026-11-03"
    data = Path(config["data_dir"])
    assert first["test_case"] == "test-cases/imported/100"
    files = {p.name for p in (data / first["test_case"]).iterdir()}
    assert files == {"index.html", "test.html", "page.html", "bild.png"}
    assert Path(first["draft"]).parent == data / "drafts" / "imported"
    second = db.one("SELECT * FROM issues WHERE number = 200")
    assert second["state"] == "filed" and json.loads(second["signatures"]) == [SIG]
    assert (data / second["test_case"] / "test.html").is_file()
    assert db.one("SELECT COUNT(*) FROM issues WHERE kind = 'comment'")[0] == 4

    assert import_gallery(config, db, gallery)["issues"] == 2  # again: same rows
    assert db.one("SELECT COUNT(*) FROM issues")[0] == 7

    # The queued draft is filed as #300 and the comment posted: the same rows are updated, nothing is left in review
    index = gallery / "issues" / "INDEX.md"
    text = index.read_text().replace(
        "| [#200](u) | 2026-11-02 | open | Second | [two.md](two.md) |\n",
        "| [#200](u) | 2026-11-02 | open | Second | [two.md](two.md) |\n"
        "| [#300](u) | 2026-11-22 | open | Queued one | [q.md](q.md) |\n",
    )
    text = text.replace("| 1 | 3 | [q.md](q.md) | x |\n", "").replace("(draft)", "– POSTED 2026-11-22")
    index.write_text(text)
    review.mark(config, db, unposted["id"], 100)  # marked as posted in review before it was registered
    import_gallery(config, db, gallery)
    assert db.one("SELECT COUNT(*) FROM issues")[0] == 7
    marked = db.one("SELECT * FROM issues WHERE id = ?", (unposted["id"],))
    assert marked["review_mark"] is None and marked["note"] == "comment on #100"
    assert unposted["id"] not in {r["id"] for r in review.rows(db)}
    filed = db.one("SELECT * FROM issues WHERE id = ?", (queued["id"],))
    assert filed["number"] == 300 and filed["state"] == "filed"
    assert db.one("SELECT state FROM issues WHERE id = ?", (unposted["id"],))["state"] == "posted"
    assert db.one("SELECT COUNT(*) FROM issues WHERE state = 'draft'")[0] == 1  # 100-ej.md is still waiting
