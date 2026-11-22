# SPDX-License-Identifier: BSD-2-Clause
import argparse
import gzip
import io
import json
import re
from pathlib import Path

import pytest

from webparity import reduce as r
from webparity.probe import Result

# --- ddmin -------------------------------------------------------------------------------------------------------


def test_ddmin_finds_the_minimal_pair_without_repeating_tests():
    seen = []

    def test(subset):
        seen.append(tuple(subset))
        return 3 in subset and 17 in subset

    assert r.ddmin(list(range(40)), test) == [3, 17]
    assert len(seen) == len(set(seen))  # every subset tested once


def test_ddmin_tries_the_empty_set_and_handles_single_items():
    assert r.ddmin([1, 2, 3], lambda s: True) == []
    assert r.ddmin(["a"], lambda s: s == ["a"]) == ["a"]
    assert r.ddmin([], lambda s: True) == []


def test_ddmin_is_one_minimal_for_a_monotone_condition():
    needed = {2, 5, 11, 12, 30}
    result = r.ddmin(list(range(32)), lambda s: needed <= set(s))
    assert set(result) == needed


def test_ddmin_budget_returns_an_interesting_superset():
    calls = []

    def test(subset):
        calls.append(subset)
        return 7 in subset

    result = r.ddmin(list(range(64)), test, max_tests=4)
    assert len(calls) == 4 and 7 in result and len(result) < 64


def test_ddmin_stops_when_the_test_raises_budget_exhausted():
    count = [0]

    def test(subset):
        count[0] += 1
        if count[0] > 2:
            raise r.BudgetExhausted
        return 1 in subset

    result = r.ddmin(list(range(16)), test)
    assert 1 in result


def test_ddmin_uses_a_shared_cache():
    cache = {}
    calls = []
    test = lambda s: calls.append(1) or 4 in s  # noqa: E731
    r.ddmin(list(range(10)), test, cache=cache)
    first = len(calls)
    r.ddmin(list(range(10)), test, cache=cache)
    assert len(calls) == first


# --- HTML model --------------------------------------------------------------------------------------------------

SAMPLES = [
    "<!doctype html><html><head><title>t &amp; u</title></head><body><p>a<p>b<ul><li>x<li>y</ul></body></html>",
    "<div a=1 b='2' c=\"3\" d><br/><img src=x.png><svg><path d='M0'/></svg></div></span>trailing",
    "<script>if (a < b && c > d) { document.write('</div>') }</script><style>a > b { color: red }</style>",
    "text before<!-- comment --><![CDATA[x]]><?pi?>&#169;&copy;<table><tr><td>1<td>2<tr><td>3</table><div",
    "",
    "no markup at all\n\nlines\r\n",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_doc_round_trips_exactly(text):
    assert r.Doc(text).render() == text


def test_doc_structure_with_implied_end_tags():
    doc = r.Doc("<ul><li>a<li>b</ul><p>x<div>y</div>")
    ul = doc.elements("ul")[0]
    assert [c.tag for c in ul.children] == ["li", "li"]
    p, div = doc.elements("p")[0], doc.elements("div")[0]
    assert div.parent is not p  # <div> closes the open <p>
    stray = r.Doc("<b>x</i></b>")
    assert [n.kind for n in stray.elements("b")[0].children] == ["text", "other"]


def test_doc_render_remove_unwrap_replace():
    doc = r.Doc("<div id=a><span>one</span><b>two</b></div><i>three</i>")
    div, span, b, i = (doc.elements(t)[0] for t in ("div", "span", "b", "i"))
    assert doc.render(removed={span.id}) == "<div id=a><b>two</b></div><i>three</i>"
    assert doc.render(unwrapped={div.id}) == "<span>one</span><b>two</b><i>three</i>"
    assert doc.render(inner={div.id: "x"}, start={i.id: "<i class=c>"}) == "<div id=a>x</div><i class=c>three</i>"
    assert doc.render(replace={b.id: "<u>2</u>"}) == "<div id=a><span>one</span><u>2</u></div><i>three</i>"


def test_split_and_join_start_tag():
    head, attrs, tail = r.split_start_tag("<img src=\"a b.png\" alt=x data-x='1' hidden />")
    assert head == "<img" and attrs == ['src="a b.png"', "alt=x", "data-x='1'", "hidden"] and tail == " />"
    assert r.join_start_tag(head, attrs[1:2], tail) == "<img alt=x />"
    assert r.attr_value('title="a &amp; b"') == "a & b" and r.attr_value("hidden") is None
    assert r.make_attr("style", 'a:"b"') == 'style="a:&quot;b&quot;"'


# --- reducers with fake predicates -------------------------------------------------------------------------------


def counting(predicate):
    def test(html):
        test.calls += 1
        return predicate(html)

    test.calls = 0
    return test


def test_reduce_dom_removes_subtrees_level_by_level():
    filler = "".join(f"<div class=f{i}><p>filler {i}</p><span>more</span></div>" for i in range(20))
    html = (
        f"<!doctype html><html><head><title>x</title></head><body>{filler}"
        f"<div><section><b>needle</b></section></div>{filler}</body></html>"
    )
    test = counting(lambda h: "<b>needle</b>" in h)
    out = r.reduce_dom(html, test)
    assert "<b>needle</b>" in out and "filler" not in out and "<title>" not in out
    assert out.startswith("<!doctype html><html><head></head><body>")
    assert "<section>" not in out and "<div>" not in out  # wrappers unwrapped
    assert test.calls < 200


def test_reduce_dom_respects_protect():
    html = "<body><div><p>keep me</p></div><div>other</div><i>x</i></body>"
    out = r.reduce_dom(html, lambda h: "<i>" in h, protect=["keep me"])
    assert "keep me" in out and "other" not in out


def test_split_js_partitions_statements():
    src = (
        "var a = 1; var b = 'x;y\\n'\n"
        "function f() {\n  return /[;]/.test(a) // comment ;\n}\n"
        "const t = `a ${ {k: 1}.k ; } b`; /* c; */ g(a,\n b)\n"
        "x = y / 2; z = 3"
    )
    chunks = r.split_js(src)
    assert "".join(chunks) == src
    assert chunks[0] == "var a = 1;"
    assert any(c.strip().startswith("function f()") and c.rstrip().endswith("}") for c in chunks)
    assert any("`a ${ {k: 1}.k ; } b`;" in c for c in chunks)
    assert any("g(a,\n b)" in c for c in chunks)
    assert chunks[-1].strip() == "z = 3"


def test_reduce_scripts_removes_scripts_and_statements():
    html = (
        "<html><body><script src=lib.js></script><script>noise1();</script>"
        "<script>\nvar a = 1;\nnoise();\nwindow.hit = 1;\nmore();\n</script>"
        '<script type="application/ld+json">{"a": 1}</script></body></html>'
    )
    out = r.reduce_scripts(html, lambda h: "window.hit = 1;" in h)
    assert "window.hit = 1;" in out
    for gone in ("lib.js", "noise1", "noise()", "more()", "var a", "ld+json"):
        assert gone not in out


def test_reduce_scripts_inlines_local_classic_scripts(tmp_path):
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text("one();\ntwo();\n")
    html = '<script src="js/app.js"></script><script src="js/app.js" defer></script>'
    out = r.reduce_scripts(html, lambda h: "two();" in h or 'src="js/app.js"></script>' in h, tmp_path)
    assert out == "<script>\ntwo();\n</script>"


def test_reduce_stylesheets_inlines_and_rebases_urls(tmp_path):
    (tmp_path / "css").mkdir()
    (tmp_path / "css" / "site.css").write_text(
        "body { background: url(../img/bg.png) } .x { background: url('a.png') } @import 'more.css';"
    )
    html = (
        '<head><link rel="stylesheet" href="css/site.css" media="screen">'
        '<link rel=stylesheet href="https://x/y.css"></head>'
    )
    out = r.reduce_stylesheets(html, lambda h: "bg.png" in h, tmp_path)
    assert '<style media="screen">' in out and "https://x/y.css" in out
    assert 'url("img/bg.png")' in out and 'url("css/a.png")' in out and '@import "css/more.css"' in out


def test_parse_and_serialize_css():
    css = (
        "@charset 'utf-8'; /* c */ a { color: red; b: url(x;y) } "
        "@media (min-width: 1px) { b { c: d } @supports (x) { i { j: k } } } @keyframes k { from { a: b } }"
    )
    rules = r.parse_css(css)
    assert [x.prelude for x in rules] == ["@charset 'utf-8';", "a", "@media (min-width: 1px)", "@keyframes k"]
    assert rules[1].decls == ["color: red", "b: url(x;y)"]
    assert rules[2].children[1].children[0].prelude == "i"
    assert rules[3].children is None and rules[3].decls is None
    inner = rules[2].children[1].children[0].rid
    out = r.serialize_css(rules, removed={rules[2].children[0].rid, inner})
    assert "@media" not in out  # emptied group rules go away
    assert r.split_declarations("a: 1;; b: 'x;y' ; c: f(1;2)") == ["a: 1", "b: 'x;y'", "c: f(1;2)"]


def test_reduce_css_rules_removes_rules_and_nested_rules():
    sheet = " ".join(f".r{i} {{ color: blue }}" for i in range(30))
    html = f"<style>{sheet} @media screen {{ .a {{ x: y }} .needle {{ color: red }} .b {{ x: y }} }} {sheet}</style><p>"
    out = r.reduce_css_rules(html, lambda h: ".needle { color: red }" in h and "@media screen" in h)
    assert re.sub(r"\s+", " ", out) == "<style> @media screen { .needle { color: red } } </style><p>"


def test_reduce_declarations_in_rules_and_style_attributes():
    html = (
        '<style>.a { color: red; margin: 0; padding: 1px }</style><div style="display: flex; color: red; width: 1px">'
    )
    out = r.reduce_declarations(html, lambda h: "margin: 0" in h and "width: 1px" in h)
    assert ".a {margin: 0}" in out and '<div style="width: 1px">' in out
    out = r.reduce_declarations(html, lambda h: "margin: 0" in h)
    assert "<div>" in out  # an empty style attribute is dropped


def test_reduce_attributes():
    html = '<div id=a class="b c" data-x=1 hidden><img src=x.png alt="y" width=10></div>'
    out = r.reduce_attributes(html, lambda h: "class=" in h and "width=10" in h)
    assert out == '<div class="b c"><img width=10></div>'


def test_reduce_text_shortens_long_text():
    words = " ".join(f"word{i}" for i in range(64))
    html = f"<p>{words}</p><title>a long title here</title><script>keep this script text</script>"
    out = r.reduce_text(html, lambda h: "word40" in h)
    assert "<p>word40" in out and "word39" not in out and "word41" not in out
    assert "keep this script text" in out
    spaced = "<div>\n  <p>a</p>\n  <pre> x\n </pre>\n</div>"
    assert r.reduce_text(spaced, lambda h: "<p>a</p>" in h) == "<div><p>a</p><pre> x\n </pre></div>"
    assert r.reduce_text(spaced, lambda h: "</p> <pre>" in h) == "<div> <p>a</p> <pre> x\n </pre> </div>"


# --- WARC and bundles --------------------------------------------------------------------------------------------


def warc_record(warc_type: str, url: str, block: bytes, extra: dict | None = None) -> bytes:
    headers = {
        "WARC-Type": warc_type,
        "WARC-Record-ID": f"<urn:uuid:{warc_type}-{url}>",
        "WARC-Target-URI": url,
        "Content-Type": f"application/http; msgtype={warc_type}",
        "Content-Length": str(len(block)),
        **(extra or {}),
    }
    head = "WARC/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
    return gzip.compress(head.encode() + block + b"\r\n\r\n")  # one gzip member per record


def exchange(url: str, status: str, headers: list[tuple[str, str]], body: bytes, ua: str = "Chrome") -> bytes:
    path = "/" + url.split("/", 3)[3]
    request = f"GET {path} HTTP/1.1\r\nUser-Agent: {ua}\r\n\r\n".encode()
    response = (
        f"HTTP/1.1 {status}\r\n".encode() + "".join(f"{k}: {v}\r\n" for k, v in headers).encode() + b"\r\n" + body
    )
    rid = f"<urn:uuid:request-{url}-{ua}>"
    return warc_record("request", url, request, {"WARC-Record-ID": rid}) + warc_record(
        "response", url, response, {"WARC-Concurrent-To": rid, "WARC-Record-ID": f"<urn:uuid:r-{url}-{ua}>"}
    )


def tiny_warc() -> bytes:
    css = b"body { background: url(/img/bg.png) } @import 'other.css';"
    return (
        warc_record("warcinfo", "", b"software: test\r\n")
        + exchange("https://ex.com/s/site.css", "200 OK", [("Content-Type", "text/css")], css)
        + exchange(
            "https://ex.com/img/bg.png",
            "200 OK",
            [("Content-Type", "image/png"), ("Content-Encoding", "gzip")],
            gzip.compress(b"PNGDATA"),
        )
        + exchange("https://ex.com/old.js", "301 Moved", [("Location", "/new.js?v=2")], b"")
        + exchange("https://ex.com/new.js?v=2", "200 OK", [("Content-Type", "text/javascript")], b"chrome();")
        + exchange("https://ex.com/new.js?v=2", "200 OK", [("Content-Type", "text/javascript")], b"lb();", "Ladybird/1")
        + exchange("https://ex.com/br.js", "200 OK", [("Content-Encoding", "br")], b"\x00\x01")
    )


def test_iter_warc_reads_gzip_members_and_plain_files(tmp_path):
    path = tmp_path / "rec.warc.gz"
    path.write_bytes(tiny_warc())
    records = list(r.iter_warc(path))
    assert [x.type for x in records][:3] == ["warcinfo", "request", "response"]
    assert records[2].target == "https://ex.com/s/site.css" and records[2].content.endswith(b"other.css';")
    plain = b"".join(gzip.decompress(m) for m in [warc_record("resource", "https://a/", b"hello")])
    (only,) = list(r.iter_warc(io.BytesIO(plain)))
    assert only.content == b"hello" and only.headers["warc-type"] == "resource"


def test_parse_http_response_decodes_bodies():
    chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n"
    assert r.parse_http_response(chunked)[2] == b"abcde"
    import zlib

    deflated = b"HTTP/1.1 404 Not Found\r\nContent-Encoding: deflate\r\n\r\n" + zlib.compress(b"x")
    status, headers, body = r.parse_http_response(deflated)
    assert status == 404 and body == b"x" and headers == [("Content-Encoding", "deflate")]
    with pytest.raises(r.UnsupportedEncoding):
        r.parse_http_response(b"HTTP/1.1 200 OK\r\nContent-Encoding: compress\r\n\r\nxx")


def test_parse_http_response_decodes_brotli_when_available(monkeypatch):
    monkeypatch.setitem(r._DECODERS, "br", lambda: lambda data: data[::-1])
    assert r.parse_http_response(b"HTTP/1.1 200 OK\r\nContent-Encoding: br\r\n\r\ncba")[2] == b"abc"
    monkeypatch.setitem(r._DECODERS, "br", lambda: None)
    with pytest.raises(r.UnsupportedEncoding):
        r.parse_http_response(b"HTTP/1.1 200 OK\r\nContent-Encoding: br\r\n\r\nxx")


def test_load_responses_materialize_and_rewrite(tmp_path):
    warc = tmp_path / "rec.warc.gz"
    warc.write_bytes(tiny_warc())
    notes = []
    responses = r.load_responses(warc, notes)
    assert any("br.js" in n for n in notes)
    assert len(responses["https://ex.com/new.js?v=2"]) == 2
    site = tmp_path / "site"
    mapping = r.materialize(responses, site, prefer="ladybird")
    assert (site / mapping["https://ex.com/img/bg.png"]).read_bytes() == b"PNGDATA"
    assert (site / mapping["https://ex.com/new.js?v=2"]).read_bytes() == b"lb();"
    assert mapping["https://ex.com/old.js"] == mapping["https://ex.com/new.js?v=2"]  # the redirect
    assert r.materialize(responses, tmp_path / "cr", prefer="chromium")["https://ex.com/old.js"]
    assert (tmp_path / "cr" / mapping["https://ex.com/new.js?v=2"]).read_bytes() == b"chrome();"
    page = (
        '<html><head><base href="https://ex.com/s/"><meta charset="iso-8859-1">'
        '<link rel=stylesheet href="site.css" integrity="sha-x"></head>'
        '<body style="background: url(/img/bg.png)"><img srcset="/img/bg.png 2x, https://elsewhere/x.png 1x">'
        '<script src="//ex.com/old.js"></script><a href="https://ex.com/missing">m</a></body></html>'
    )
    out = r.rewrite_html_urls(page, "https://ex.com/page", mapping)
    assert "<base" not in out and 'charset="utf-8"' in out and "integrity" not in out
    assert 'href="res/ex.com/s/site.css"' in out
    assert 'style="background: url(&quot;res/ex.com/img/bg.png&quot;)"' in out
    assert 'srcset="res/ex.com/img/bg.png 2x, https://elsewhere/x.png 1x"' in out
    assert 'src="res/ex.com/new_' in out and 'href="https://ex.com/missing"' in out


def test_local_path_for():
    assert r.local_path_for("https://a.com/") == "res/a.com/index.html"
    assert r.local_path_for("https://a.com:8080/x/y.css") == "res/a.com_8080/x/y.css"
    q = r.local_path_for("https://a.com/x/app.js?v=1&b=2")
    assert q.startswith("res/a.com/x/app_") and q.endswith(".js")
    assert ".." not in r.local_path_for("https://a.com/../../etc/passwd")


def make_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "visit"
    bundle.mkdir()
    (bundle / "visit.json").write_text(
        json.dumps({"schema": 1, "id": "x", "url": "https://ex.com/page", "browsers": {}})
    )
    dom = '<!doctype html><html><head><link rel=stylesheet href="/s/site.css"></head><body><p id=x>hi</p></body></html>'
    (bundle / "ladybird-dom.html.gz").write_bytes(gzip.compress(dom.encode()))
    (bundle / "recording.warc.gz").write_bytes(tiny_warc())
    return bundle


def test_prepare_source_from_a_bundle(tmp_path):
    prepared = r.prepare_source(make_bundle(tmp_path), tmp_path / "work")
    site = tmp_path / "work" / "site"
    assert prepared.serve_dir == site and 'href="res/ex.com/s/site.css"' in prepared.html
    css = (site / "res/ex.com/s/site.css").read_text()
    assert 'url("../img/bg.png")' in css  # rewritten relative to the CSS file
    assert (site / "original.html").read_text() == prepared.html
    (tmp_path / "b").mkdir()
    chromium = r.prepare_source(make_bundle(tmp_path / "b"), tmp_path / "work2", dom="chromium", resources=False)
    assert any("using the ladybird DOM" in n for n in chromium.notes) and "/s/site.css" in chromium.html


def test_prepare_source_restores_the_doctype_from_the_recording(tmp_path):
    bundle = make_bundle(tmp_path)
    dom = "<html><head></head><body><p id=x>hi</p></body></html>"  # saved before the DOM kept its doctype
    (bundle / "ladybird-dom.html.gz").write_bytes(gzip.compress(dom.encode()))
    page = b"<!-- x -->\n<!DOCTYPE html>\n<html><body>hi</body></html>"
    with open(bundle / "recording.warc.gz", "ab") as f:
        f.write(exchange("https://ex.com/page", "200 OK", [("Content-Type", "text/html")], page))
    prepared = r.prepare_source(bundle, tmp_path / "work")
    assert prepared.html.startswith("<!DOCTYPE html>\n<html>")
    assert any("no doctype" in n for n in prepared.notes)
    (bundle / "recording.warc.gz").write_bytes(tiny_warc())  # no recorded page: left as it is (quirks)
    assert r.prepare_source(bundle, tmp_path / "work2").html.startswith("<html>")


# --- the driver --------------------------------------------------------------------------------------------------


def file_predicate(condition):
    def check(path: Path) -> bool:
        check.paths.append(path)
        assert path.exists()
        return condition(path.read_text())

    check.paths = []
    return check


def test_reduce_end_to_end_with_a_fake_predicate(tmp_path):
    (tmp_path / "s.css").write_text(".a { color: red; margin: 0 } .needle { width: 7px; height: 1px } .b { c: d }")
    page = tmp_path / "page.html"
    page.write_text(
        "<!doctype html><html><head><title>t</title><link rel=stylesheet href=s.css><script>noise()</script></head>"
        "<body><div class=wrap id=w><p>lots of filler text here</p><span class=needle>x</span></div></body></html>"
    )
    check = file_predicate(lambda h: ("width: 7px" in h or "href=s.css" in h) and "class=needle" in h)
    events = []
    out = tmp_path / "out" / "min.html"
    result = r.reduce(page, check, out, budget_tests=500, progress=events.append)
    text = out.read_text()
    assert "width: 7px" in text and "class=needle" in text
    assert "filler" not in text and "noise" not in text and "margin" not in text and "<title>" not in text
    assert "href=s.css" not in text  # inlined, then reduced
    assert result.bytes_after == len(text.encode()) < result.bytes_before
    assert result.tests == len(check.paths) and len(set(check.paths)) == len(check.paths)  # a fresh file each time
    assert not any(p.exists() for p in check.paths)  # candidates are removed
    assert (result.work_dir / "best.html").read_text() == text
    assert result.interrupted is None and result.resources == []
    assert {e["event"] for e in events} >= {"pass", "improved", "test"}
    assert [p["name"] for p in result.passes][:7] == list(r.PASSES)


def test_reduce_keeps_progress_when_the_budget_runs_out(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<body>" + "".join(f"<div>{i}</div>" for i in range(100)) + "<b>needle</b></body>")
    result = r.reduce(page, file_predicate(lambda h: "needle" in h), tmp_path / "min.html", budget_tests=3)
    assert result.interrupted == "budget" and result.tests == 3
    text = (tmp_path / "min.html").read_text()
    assert "needle" in text and len(text) < len(page.read_text())
    assert (result.work_dir / "best.html").read_text() == text


def test_reduce_refuses_a_start_that_is_not_interesting(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<p>x</p>")
    with pytest.raises(r.ReduceError):
        r.reduce(page, lambda p: False, tmp_path / "min.html")


def test_reduce_a_bundle_offline(tmp_path):
    check = file_predicate(lambda h: "id=x" in h)
    result = r.reduce(make_bundle(tmp_path), check, tmp_path / "min.html", passes=["dom", "attributes"])
    assert check.paths[0].parent == tmp_path / "min.html.work" / "site"
    assert result.best_path == tmp_path / "min.html.work" / "site" / "best.html" and result.best_path.exists()
    assert (tmp_path / "min.html").read_text() == "<!doctype html><html><head></head><body><p id=x></p></body></html>"
    assert result.resources == []


# --- conditions and command line ---------------------------------------------------------------------------------


def results(lb=None, cr=None, lb_error=None, cr_error=None):
    out = {"ladybird": Result("u", "ladybird", lb, lb_error)}
    if cr is not None or cr_error is not None:
        out["chromium"] = Result("u", "chromium", cr, cr_error)
    return out


def test_conditions():
    assert r.differs(results("1", "2")) and not r.differs(results("1", "1"))
    assert not r.differs(results("1", cr_error="boom")) and not r.differs(results("1"))
    assert r.ladybird_equals("ok")(results(" ok\n")) and not r.ladybird_equals("ok")(results(lb_error="x"))
    assert r.ladybird_matches(r"^\d+ px$")(results("12 px")) and not r.ladybird_matches("a")(results(None))
    crash = r.ladybird_error()
    assert crash(results(lb_error="script.callFunction: connection closed (EOF)", cr="fine"))
    assert not crash(results(lb_error="connection closed", cr_error="also broken"))
    assert not crash(results(lb_error="no answer in 30 s", cr="fine")) and crash(results(lb_error="WebSocket closed"))


def test_probe_predicate_repeat_modes(tmp_path, monkeypatch):
    answers = iter([True, False, True])
    pred = r.ProbePredicate(lambda res: next(answers), repeat=3)
    monkeypatch.setattr(pred, "run", lambda path: {})
    assert pred(tmp_path / "x.html") is False  # all must hold
    answers = iter([False, True])
    pred = r.ProbePredicate(lambda res: next(answers), repeat=3, repeat_any=True)
    monkeypatch.setattr(pred, "run", lambda path: {})
    assert pred(tmp_path / "x.html") is True


def parse(*argv):
    parser = argparse.ArgumentParser()
    r.add_arguments(parser)
    return parser.parse_args(list(argv))


def test_cli_arguments_build_the_predicate(tmp_path):
    args = parse("page.html", "-o", "out.html", "--ladybird-matches", "^0", "-j", "scrollY", "--repeat", "2")
    pred = r.predicate_from_args(args)
    assert pred.browsers == ["ladybird"] and pred.script == "scrollY" and pred.repeat == 2
    assert r.predicate_from_args(parse("p", "-o", "o", "--crash")).browsers == ["ladybird", "chromium"]
    with pytest.raises(r.ReduceError):
        r.predicate_from_args(parse("p", "-o", "o", "--differs", "--browsers", "ladybird"))
    with pytest.raises(SystemExit):
        parse("p", "-o", "o")  # a condition is required


def test_main_reduce_reports_errors(tmp_path, capsys):
    assert r.main_reduce([str(tmp_path / "missing.html"), "-o", str(tmp_path / "o.html"), "--differs"]) == 2
    assert "webparity reduce:" in capsys.readouterr().err


def test_probe_predicate_reuses_sessions_and_replaces_broken_ones(tmp_path, monkeypatch):
    from webparity import probe
    from webparity.bidi import BidiError

    log = []

    class FakeBrowser:
        def __init__(self, name, mobile):
            self.name, self.session = name, None

        def start(self):
            log.append(("start", self.name))
            self.session = object()

        def stop(self):
            log.append(("stop", self.name))
            self.session = None

        def load(self, url, test_case, wait):
            assert url.startswith("http://127.0.0.1:") and test_case
            if self.name == "ladybird" and "crash" in Path(tmp_path / url.rsplit("/", 1)[1]).read_text():
                raise BidiError("script.callFunction: connection closed (EOF)")

        def run_script(self, script, test_case):
            return f"{self.name}:{script}"

    monkeypatch.setattr(probe, "Browser", FakeBrowser)
    pred = r.ProbePredicate(r.ladybird_error(), script="1", browsers=["ladybird", "chromium"])
    try:
        (tmp_path / "a.html").write_text("fine")
        (tmp_path / "b.html").write_text("crash")
        assert pred(tmp_path / "a.html") is False and pred.last["chromium"].value == "chromium:1"
        assert pred(tmp_path / "b.html") is True and "connection closed" in pred.last["ladybird"].error
        assert pred(tmp_path / "a.html") is False
    finally:
        pred.close()
    starts = [entry for entry in log if entry[0] == "start"]
    assert starts == [("start", "ladybird"), ("start", "chromium"), ("start", "ladybird")]
