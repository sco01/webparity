# SPDX-License-Identifier: BSD-2-Clause
import time
import urllib.request
from pathlib import Path

from webparity.probe import Result, format_results, log_lines_since, log_size, resolve_targets, wrap_script


def test_wrap_script_expression_and_statements():
    expression = wrap_script("[scrollY, 1];", True)
    assert "return ([scrollY, 1]\n);" in expression
    statements = wrap_script("const a = 1; return a", False)
    assert "const a = 1; return a" in statements and "return (const" not in statements
    assert statements.startswith("async function")


def test_resolve_targets_serves_local_files(tmp_path: Path):
    (tmp_path / "case").mkdir()
    page = tmp_path / "case" / "test.html"
    page.write_text("<pre id=out>hello</pre>")
    resolved, server = resolve_targets([str(page) + "?v=1", "https://example.com/"], None)
    try:
        (url, is_case), (site, site_is_case) = resolved
        assert is_case and not site_is_case and site == "https://example.com/"
        assert url.startswith("http://127.0.0.1:") and url.endswith("/test.html?v=1")
        body = urllib.request.urlopen(url, timeout=5).read().decode()
        assert "hello" in body
        other_origin = url.replace("127.0.0.1", "localhost")
        assert "hello" in urllib.request.urlopen(other_origin, timeout=5).read().decode()
    finally:
        server.close()


def test_format_results():
    results = [
        Result("http://a/", "ladybird", value="one\ntwo", ms=12),
        Result("http://a/", "chromium", error="boom", ms=3),
    ]
    text = format_results(results)
    assert text.splitlines()[0] == "=== http://a/"
    assert "ladybird" in text and "two" in text and "ERROR boom" in text


def test_server_helpers(tmp_path: Path):
    page = tmp_path / "slow.js"
    page.write_text("x" * 3000)
    resolved, server = resolve_targets([str(page)], None, cross_origin=True)
    try:
        url = resolved[0][0]
        assert url.startswith("http://localhost:")
        started = time.monotonic()
        assert len(urllib.request.urlopen(url + "?sleep=300", timeout=5).read()) == 3000
        assert time.monotonic() - started >= 0.3
        assert len(urllib.request.urlopen(url + "?throttle=100", timeout=5).read()) == 3000
        base = url.rsplit("/", 1)[0]
        assert urllib.request.urlopen(f"{base}/_count?/slow.js", timeout=5).read() == b"2"
    finally:
        server.close()


def test_log_lines_since(tmp_path: Path):
    log = tmp_path / "webdriver.log"
    log.write_text("old panicked at x.rs:1\n")
    offset = log_size(str(log))
    with log.open("a") as f:
        f.write("ok line\n\x1b[31mVERIFICATION FAILED: foo at Bar.cpp:12\x1b[0m\nWebContent process crashed\n")
    assert log_lines_since(str(log), offset) == [
        "VERIFICATION FAILED: foo at Bar.cpp:12",
        "WebContent process crashed",
    ]
    assert log_lines_since(str(tmp_path / "missing.log"), 0) == []
    assert log_lines_since(None, 0) == []


def test_format_results_screenshot_and_log():
    text = format_results(
        [Result("http://x/", "ladybird", value="1", screenshot="/data/probe/a.png", log=["panicked at y.rs:2"])]
    )
    assert "screenshot /data/probe/a.png" in text and "log: panicked at y.rs:2" in text
