# SPDX-License-Identifier: BSD-2-Clause
"""Every detector fixture through ``analyze(bundle, only=[detector])``.

A fixture is ``tests/fixtures/detectors/<detector>/<case>/`` with ``visit.json`` (and any files the detector reads) and
``expected.json``: ``must`` (signatures that must appear, exactly), ``must_not`` (signatures that must not appear: an
entry ending in ``:`` is a prefix, otherwise exact), optional ``categories`` (signature -> ``error``/``info``) and
optional ``known`` (signature -> a substring of ``finding.known``, or null for "not known"; the detectors then run with
the known differences from ``known/*.yaml``). All fixture data is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webparity.bundle import Bundle
from webparity.detectors import all_detectors, analyze
from webparity.findings import Finding
from webparity.known import Entry, Known, load_entries
from webparity.signatures import translate_signature, translate_status

FIXTURES = Path(__file__).parent / "fixtures" / "detectors"
CASES = sorted(p.parent for p in FIXTURES.glob("*/*/expected.json"))


@pytest.mark.parametrize("case", CASES, ids=[f"{p.parent.name}/{p.name}" for p in CASES])
def test_fixture(case: Path):
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    detector = expected.get("detector") or case.parent.name
    known = Known.load() if expected.get("known") else None
    findings = analyze(Bundle.load(case), only=[detector], known=known)
    by_signature = {f.signature: f for f in findings}
    errors = [f for f in findings if f.signature.startswith("detector-error:")]
    assert not errors, errors[0].data.get("traceback")
    for sig in expected.get("must", []):
        assert sig in by_signature, f"missing {sig!r}; got {sorted(by_signature)}"
    for sig in expected.get("must_not", []):
        bad = [s for s in by_signature if (s.startswith(sig) if sig.endswith(":") else s == sig)]
        assert not bad, f"unexpected {bad} (rule {sig!r})"
    for sig, category in expected.get("categories", {}).items():
        assert by_signature[sig].category == category, f"{sig}: {by_signature[sig].category} != {category}"
    for sig, reason in expected.get("known", {}).items():
        if reason is None:
            assert not by_signature[sig].known, f"{sig} unexpectedly known: {by_signature[sig].known}"
        else:
            assert reason in (by_signature[sig].known or ""), f"{sig}: known={by_signature[sig].known!r}"


def test_every_detector_has_positive_and_negative_fixtures():
    for detector in all_detectors():
        cases = [json.loads(p.read_text()) for p in (FIXTURES / detector.name).glob("*/expected.json")]
        assert any(c.get("must") for c in cases), f"{detector.name}: no positive fixture"
        assert any(c.get("must_not") for c in cases), f"{detector.name}: no negative fixture"


def test_detectors_have_names_and_descriptions():
    names = [d.name for d in all_detectors()]
    assert len(names) == len(set(names))
    assert {"crash", "script_error", "layout", "image", "scroll", "css", "network", "performance"} <= set(names)
    assert all(d.description for d in all_detectors())


def test_known_entries_are_valid():
    entries = load_entries()
    ids = [e.id for e in entries if e.id]
    assert len(ids) == len(set(ids))
    for e in entries:
        assert e.issue and e.text, e
        assert bool(e.prefix) != bool(e.regex), f"{e.issue}: exactly one of prefix/regex"
        assert e.run_in in ("reference", "ladybird"), e
        assert e.mobile in (False, True, "always"), e
        if e.is_test:
            assert e.id, f"{e.issue}: a page test needs an id"
        # Every signature an entry names must be an English one
        for p in e.prefix:
            # (an English prefix of a longer signature, like ``video`` for ``video:not-started``, is fine)
            assert translate_signature(p).startswith(p), f"{e.issue}: old signature {p!r}"


def test_known_page_test_selection_and_fixed_commits():
    entry = Entry(issue="#1", text="t", prefix=("layout:",), id="x", js="return true", fixed="abc1234")

    class Always:
        def has(self, build, commit):
            return build == "fixedbuild"

    known = Known([entry], build_check=Always())
    assert [e.id for e, _ in known.page_tests_for(["layout:missing:block>block", "menu"], "oldbuild")] == ["x"]
    assert known.page_tests_for(["layout:missing:block>block"], "fixedbuild") == []
    assert known.page_tests_for(["menu"], "oldbuild") == []


def test_async_and_mobile_page_tests():
    e = Entry(issue="#2", text="t", prefix=("mobile-",), id="y", js="done(true);", is_async=True, mobile=True)
    assert e.script().startswith("return await new Promise(done => {")
    assert e.wants_mobile(["mobile-layout:width:flex>img"]) and not e.wants_mobile(["layout:x", "mobile-layout:x"])
    assert Entry(issue="#3", text="t", prefix=("crash:",), id="z", js="x", mobile="always").wants_mobile(["crash:x"])


def test_site_exceptions_from_the_database(tmp_path):
    class FakeDb:
        def site_exceptions(self, domain):
            return {"layout:": "random start image"} if domain == "example.test" else {}

    bundle = Bundle(tmp_path, {"url": "https://www.example.test/", "browsers": {}})
    findings = [Finding("layout:missing:block>block", "error", "x"), Finding("menu", "error", "y")]
    Known([], db=FakeDb()).mark(bundle, findings)
    assert findings[0].known == "site exception: random start image"
    assert findings[1].known is None


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("krasch:SIGSEGV i WebContent", "crash:SIGSEGV in WebContent"),
        ("krasch:minnesbrist (OOM)", "crash:out-of-memory (OOM)"),
        ("säkerhet:VERIFY x @ A.cpp", "security:VERIFY x @ A.cpp"),
        ("layout:saknas:block>block", "layout:missing:block>block"),
        ("layout:läge", "layout:position"),
        ("mobil-layout:höjd:flex>img", "mobile-layout:height:flex>img"),
        ("mobil-överflödning-dold", "mobile-overflow-hidden"),
        ("mobil-överflödning", "mobile-overflow"),
        ("överflödning-dold", "overflow-hidden"),
        ("bild-skiljer", "image-differs"),
        ("rullning-låst", "scroll-locked"),
        ("scroll", "scroll-lazy-content"),
        ("video", "video:not-started"),
        ("meny", "menu"),
        ("seg", "slow-response"),
        ("långsam", "slow-load"),
        ("typsnittsfel:woff2+ttf", "font-error:woff2+ttf"),
        ("typsnittsfel", "font-error"),
        ("css-tappad:clip-path:värde", "css-dropped:clip-path:value"),
        ("css-saknas:zoom", "missing-feature:css:zoom"),
        ("js-saknas:TODO(x)", "missing-feature:js:TODO(x)"),
        ("saknas:FIXME: Unimplemented N", "missing-feature:FIXME: Unimplemented N"),
        ("js-fel:[TypeError] x", "script-error:[TypeError] x"),
        ("js-fel-iframe", "script-error-iframe"),
        ("fler-resurser:annat:svg", "more-resources:other:svg"),
        ("nät-loop:annat", "network-loop:other"),
        ("laddar-inte:anslutning", "not-loading:connection"),
        ("laddar-inte:xml-flöde", "not-loading:xml-feed"),
        ("saknade-resurser-fa", "missing-resources-few"),
        ("känd:utkast:ellips-inline", "known:draft:ellips-inline"),
        ("känd:#14116", "known:#14116"),
        ("spärrsida-bara-ladybird", "blocked:ladybird-only"),
        ("crash:already-english", "crash:already-english"),
        ("segment", "segment"),
    ],
)
def test_translate_signature(old, new):
    assert translate_signature(old) == new


def test_translate_status():
    assert translate_status("candidate", "rapporterad") == "filed"
    assert translate_status("candidate", "rättad") == "fixed"
    assert translate_status("site", "kö") == "queued"
    assert translate_status("visit", "kandidat") == "candidate"
    assert translate_status("category", "fel") == "error"


def test_proxy_upstream_errors_mark_error_findings():
    from types import SimpleNamespace

    from webparity.detectors import PROXY_KNOWN, proxy_errors

    lost = {"url": "https://a.example/style.css", "error": "server closed connection", "user_agent": "Ladybird/1.0"}
    bundle = SimpleNamespace(data={"proxy_upstream_errors": [lost]})
    findings = [Finding("layout:height:block>block", "error", "t"), Finding("missing-feature:x", "info", "t")]
    proxy_errors(bundle, findings)
    assert findings[0].known == PROXY_KNOWN and findings[1].known is None
    info = findings[-1]
    assert info.signature == "proxy-upstream-errors" and info.category == "info"
    assert info.data["per_browser"] == {"ladybird": 1}

    cancelled = {"url": "https://a.example/x.js", "error": "stream reset by client (CANCEL)", "user_agent": "Chrome"}
    dns = {"url": "https://t.example/p", "error": "[Errno -5] No address associated with hostname", "user_agent": "x"}
    beacon = {"method": "POST", "url": "https://o1.ingest.example/api/1/envelope/", "error": "connection closed"}
    both = [
        {"url": "https://c.example/a", "error": "[Errno 101] Network is unreachable", "user_agent": ua}
        for ua in ("Ladybird/1.0", "Chrome/1")
    ]
    ignored = [Finding("layout:height:block>block", "error", "t")]
    proxy_errors(SimpleNamespace(data={"proxy_upstream_errors": [cancelled, dns, beacon, *both]}), ignored)
    assert len(ignored) == 1 and ignored[0].known is None

    clean = [Finding("layout:height:block>block", "error", "t")]
    proxy_errors(SimpleNamespace(data={}), clean)
    assert len(clean) == 1 and clean[0].known is None


def test_pdf_viewer_share():
    from webparity.detectors.image import pdf_viewer_share

    pdf = {"box": [48, 331, 1344, 680], "src": "epark.example/assets/terms.pdf"}
    page = {"box": [0, 0, 1440, 900], "src": "epark.example/widget"}
    assert pdf_viewer_share([32, 320, 1376, 576], [pdf]) > 0.9
    assert pdf_viewer_share([32, 320, 1376, 576], [page]) == 0
    assert pdf_viewer_share(None, [pdf]) == 0


def test_font_blocked_when_every_source_came_back_as_html():
    from webparity.detectors.image import font_findings

    src = 'url("../webfonts/fa-solid-900.woff2") format(woff2), url("../webfonts/fa-solid-900.woff") format(woff)'
    lp = {
        "font_errors": 1,
        "font_error_names": ["Font Awesome 5 Free 900 normal"],
        "font_error_families": ["Font Awesome 5 Free"],
        "font_sources": {"Font Awesome 5 Free": src},
        "font_errors_all": ["Font Awesome 5 Free 900 normal"],
        "font_errors_unreplaced": 1,
    }
    cp = {"font_errors": 0, "fonts_loaded": ["Font Awesome 5 Free 900 normal"], "font_errors_unreplaced": 0}
    blocked = font_findings(lp, cp, {"fa-solid-900.woff2", "fa-solid-900.woff"})
    assert [f.signature for f in blocked] == ["font-blocked"] and blocked[0].category == "info"
    partly = font_findings(lp, cp, {"fa-solid-900.woff2"})
    assert len(partly) == 1 and partly[0].signature.startswith("font-error") and partly[0].category == "error"


def test_font_failures_explained_one_by_one():
    from webparity.detectors.image import font_findings

    lp = {
        "font_errors": 2,
        "font_error_names": ["Noto Serif 400 normal", "Poppins 400 normal"],
        "font_error_families": ["Noto Serif", "Poppins"],
        "font_sources": {
            "Noto Serif": 'url("https://f.example/l/font?kit=a")',
            "Poppins": 'url("https://f.example/l/font?kit=b")',
        },
        "font_errors_all": ["Noto Serif 400 normal", "Poppins 400 normal"],
        "font_errors_unreplaced": 1,
        "fonts_loaded": ["Noto Serif 700 normal", "Poppins 400 normal"],
    }
    cp = {
        "font_errors": 0,
        "fonts_loaded": ["Noto Serif 700 normal", "Poppins 400 normal"],
        "font_errors_unreplaced": 0,
    }
    assert [f.signature for f in font_findings(lp, cp)] == ["font-replaced"]
    cp_uses_it = dict(cp, fonts_loaded=cp["fonts_loaded"] + ["Noto Serif 400 normal"])
    assert font_findings(lp, cp_uses_it)[0].category == "error"
