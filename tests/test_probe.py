# SPDX-License-Identifier: BSD-2-Clause
import urllib.request
from pathlib import Path

from webparity.probe import Result, format_results, resolve_targets, wrap_script


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
