# SPDX-License-Identifier: BSD-2-Clause
"""The visit steps with a fake browser handle, and the helpers they share with the detectors (images, the log)."""

from __future__ import annotations

import shutil
import subprocess
import threading

import pytest

from webparity import images, js, ladybird_log, measure
from webparity.bundle import Bundle
from webparity.known import Entry


class FakeHandle:
    """Answers ``js`` by script; records input actions and viewport changes."""

    def __init__(self, answers: dict | None = None, png: bytes | None = None):
        self.answers = answers or {}
        self.calls: list[tuple[str, tuple]] = []
        self.viewport = measure.DESKTOP
        self.viewports: list[tuple[int, int]] = []
        self.clicks: list[tuple[int, int]] = []
        self.wheels: list[tuple[int, int, int]] = []
        self.png = png or images.encode(16, 16, lambda x, y: (255, 255, 255))

    def js(self, body, *args, timeout=30):
        self.calls.append((body, args))
        for script, answer in self.answers.items():
            if body == script or body.startswith(script):
                return answer(*args) if callable(answer) else answer
        return None

    def screenshot(self):
        return self.png

    def set_viewport(self, w, h):
        self.viewport = (w, h)
        self.viewports.append((w, h))

    def navigate(self, url):
        return None

    def click_at(self, x, y):
        self.clicks.append((x, y))

    def wheel(self, x, y, dy, steps=1):
        self.wheels.append((x, y, dy))

    def actions(self, actions):
        pass


class Ctx:
    def __init__(self, bundle, browser="ladybird", shared=None, config=None):
        self.bundle, self.browser, self.url = bundle, browser, bundle.data["url"]
        self.config = config or {"visit": {"stable_timeout": 5}, "ladybird": {"log": None}}
        self.shared = shared if shared is not None else {}

    def save(self, name, content):
        self.bundle.write_file(f"{self.browser}-{name}", content)

    def measurements(self, browser=None):
        return self.bundle.browser(browser or self.browser)


@pytest.fixture
def bundle(tmp_path):
    return Bundle.create(tmp_path, "https://www.example.test/", "20261121-120000-abcdef")


def test_steps_and_should_run(bundle):
    assert list(measure.STEPS) == ["load", "probe", "idle", "interact", "reload"]
    assert measure.should_run("load", "chromium", bundle)
    bundle.browser("chromium")["probe"] = {"blocked": True, "elements": 10}
    assert not measure.should_run("interact", "chromium", bundle)
    assert not measure.should_run("reload", "chromium", bundle)
    assert measure.should_run("interact", "ladybird", bundle)
    bundle.browser("ladybird")["load"] = {"error": "timeout"}
    assert not measure.should_run("idle", "ladybird", bundle)
    bundle.browser("ladybird")["load"] = {}
    bundle.browser("ladybird")["probe"] = {"error_page": True}
    assert not measure.should_run("reload", "ladybird", bundle)
    assert measure.should_run("idle", "ladybird", bundle)


def test_blocked_and_error_page():
    assert measure.blocked({"title": "Just a moment..."}, "")
    assert measure.blocked({"title": "Shop", "text": 100}, "Access denied: error code 1020")
    assert not measure.blocked({"title": "Shop", "text": 5000}, "access denied " + "x" * 500)
    # 2026-11-15: an unambiguous wall phrase counts on a page with a lot of text
    assert measure.blocked({"title": "Shop", "text": 5000}, "Welcome. Enter the characters you see in the image.")
    datadome = "<html><head><title>shop.example</title></head><body><script>var dd={'host':'geo.captcha-delivery.com'}"
    assert measure.blocked({"title": "shop.example", "text": 0}, "", datadome)
    tag = '<script src="https://js.datadome.co/tags.js"></script>'
    assert not measure.blocked({"title": "Shop", "text": 5000}, "x" * 5000, tag + "geo.captcha-delivery.com")
    assert measure.error_page({"title": "", "text": 10}, {})
    assert measure.error_page({"title": "Page not found", "text": 900}, {})
    assert measure.error_page({"title": "x", "text": 900, "url": "chrome-error://chromewebdata/"}, {})
    assert measure.error_page({"title": "x", "text": 900, "elements": 0}, {"error": "timeout"})
    assert not measure.error_page({"title": "Start", "text": 900, "elements": 400}, {})


def test_probe_step_saves_artifacts_and_flags(bundle):
    h = FakeHandle(
        {
            js.PROBE: {"title": "Start", "url": "https://www.example.test/", "elements": 400, "text": 2000},
            js.LAYOUT: [["body", -1, 0, 0, 1440, 900, "block", "static", "none", 0, 16, "1:", 0, 0]],
            js.OUTER_HTML: "<html><body>hi</body></html>",
            js.BODY_TEXT: "Hello",
        }
    )
    out = measure.probe(h, Ctx(bundle))
    assert out["title"] == "Start" and out["blocked"] is False and out["error_page"] is False
    assert len(out["fingerprint"]) == 32 * 20
    assert bundle.read_json("ladybird-layout.json.gz")[0][0] == "body"
    assert bundle.read_file("ladybird-dom.html.gz").startswith(b"<html>")
    assert bundle.has("ladybird-load.png")


def test_probe_step_records_a_failing_probe(bundle):
    class Failing(FakeHandle):
        def js(self, body, *args, timeout=30):
            raise OSError("connection reset")

    out = measure.probe(Failing(), Ctx(bundle, "chromium"))
    assert out["error"].startswith("OSError") and out["error_page"] is True


def test_value_kind():
    assert measure.value_kind("-webkit-radial-gradient(circle, red, blue)") == "-webkit-radial-gradient"
    assert measure.value_kind("rgb(0 0 0)") == "rgb"  # noise function: falls back to the keyword
    assert measure.value_kind("12") == "value"
    assert measure.value_kind("10cqw") == "cqw"
    assert measure.value_kind("-webkit-center") == "-webkit-center"


def test_compare_css_groups_and_supports():
    a = {"k1": "x", "k2": "y"}
    b = {"k1": "z", "k2": "w"}
    da = {"k1": {"sel": ".a", "p": {"color": "red"}}, "k2": {"sel": ".b", "p": {}}}
    db = {
        "k1": {"sel": ".a", "p": {"color": "red", "background-image": "-webkit-radial-gradient(red, blue)"}},
        "k2": {"sel": ".b", "p": {"-webkit-line-clamp": "3", "margin": "initial"}},
    }
    h = FakeHandle({js.CSS_STEP3: {"k2": {"-webkit-line-clamp": "3"}}, js.CSS_SUPPORTS: [True]})
    result = measure.compare_css(h, a, b, da, db, ["k1", "k2"])
    assert result["differing"] == 2
    # -webkit-line-clamp was found by asking directly (a shorthand in Ladybird); margin: initial is implicit
    assert [(d["property"], d["kind"], d["supported"]) for d in result["dropped"]] == [
        ("background-image", "-webkit-radial-gradient", True)
    ]


def test_meet_waits_for_every_browser(bundle):
    shared: dict = {}
    results = {}

    def run(name, value):
        results[name] = measure.meet(Ctx(bundle, name, shared), "k", value, {"ladybird", "chromium"}, timeout=5)

    threads = [threading.Thread(target=run, args=(n, i)) for i, n in enumerate(["ladybird", "chromium"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results["ladybird"] == results["chromium"] == {"ladybird": 0, "chromium": 1}
    # A browser that never arrives doesn't block forever
    assert measure.meet(Ctx(bundle, "ladybird", {}), "k2", 1, {"ladybird", "firefox"}, timeout=0.1) == {"ladybird": 1}


def test_scroll_wheel_and_menu(bundle, monkeypatch):
    monkeypatch.setattr(measure.time, "sleep", lambda s: None)
    states = iter([[0, 3000, 500, 30], [800, 3000, 600, 35]])
    steps = iter([[800, 3000, 550, 32], [1600, 3000, 580, 33], [1600, 3000, 600, 35], [1600, 3000, 600, 35]])
    h = FakeHandle({js.SCROLL_STATE: lambda: next(states), js.SCROLL_STEP: lambda: next(steps)})
    assert measure.scroll(h) == {"height": [3000, 3000], "elements": [500, 600], "resources": [30, 35], "bottom": 800}

    reads = iter([[0, 3000, 900, 1440], [400, 3000, 900, 1440]])
    h = FakeHandle({js.WHEEL_READ: lambda: next(reads), "return " + js.VIEWPORT_OVERFLOW_Y: "visible"})
    assert measure.wheel(h) == {"scrollable": True, "scrolled": 400, "viewport_overflow_y": "visible"}
    assert h.wheels == [(720, 450, 400)]
    assert measure.wheel(FakeHandle({js.WHEEL_READ: [0, 900, 900, 1440]})) == {"scrollable": False}

    h = FakeHandle(
        {
            js.MENU_FIND: {"label": "Menu", "controls": "nav", "center": [100, 40]},
            js.MENU_HIT: True,
            js.MENU_READ: {"expanded": True, "controlled_visible": True, "url": "https://www.example.test/"},
        }
    )
    menu = measure.menu(h)
    assert menu["method"] == "input" and h.clicks == [(100, 40)] and menu["expanded"]
    h = FakeHandle({js.MENU_FIND: {"label": "Menu", "center": [1, 1]}, js.MENU_HIT: False, js.MENU_READ: {}})
    assert measure.menu(h)["method"] == "js" and h.clicks == []
    assert measure.menu(FakeHandle()) is None


def test_mobile_restores_the_viewport(bundle, monkeypatch):
    monkeypatch.setattr(measure.time, "sleep", lambda s: None)
    h = FakeHandle(
        {
            "return [innerWidth, innerHeight]": [390, 844],
            js.OVERFLOW: 120,
            "return " + js.VIEWPORT_OVERFLOW_X: "visible",
            "return " + js.ABOVE: [600, 380],
            js.LAYOUT: [["body", -1, 0, 0, 390, 844, "block", "static", "none", 0, 16, "1:", 0, 0]],
        }
    )
    out = measure.mobile(h, Ctx(bundle))
    assert out == {"viewport": [390, 844], "overflow": 120, "viewport_overflow_x": "visible", "above": [600, 380]}
    assert h.viewports == [measure.MOBILE, measure.DESKTOP]
    assert bundle.has("ladybird-layout-mobile.json.gz") and bundle.has("ladybird-mobile.png")


def test_run_page_test_mobile_and_errors(monkeypatch):
    monkeypatch.setattr(measure.time, "sleep", lambda s: None)
    entry = Entry(issue="#1", text="t", prefix=("mobile-",), id="x", js="return 'hit'", mobile=True)
    h = FakeHandle({"return 'hit'": "hit"})
    assert measure.run_page_test(h, entry, ["mobile-layout:width:flex>img"]) == "hit"
    assert h.viewports == [measure.MOBILE, measure.DESKTOP]

    class Failing(FakeHandle):
        def js(self, body, *args, timeout=30):
            raise OSError("gone")

    assert measure.run_page_test(Failing(), entry, ["layout:x"]) == {"error": "OSError: gone"}


def test_reference_name(bundle):
    bundle.data["config"] = {"browsers": ["ladybird", "firefox", "chromium"]}
    assert measure.reference_name(bundle) == "chromium"
    bundle.data["config"] = {"browsers": ["ladybird", "firefox"]}
    assert measure.reference_name(bundle) == "firefox"


# --- images -----------------------------------------------------------------------------------------------------------
def test_png_grid_spread_and_compare():
    white = images.grid(images.encode(64, 64, lambda x, y: (255, 255, 255)))
    stripes = images.grid(images.encode(64, 64, lambda x, y: (0, 0, 0) if (x // 8) % 2 else (255, 255, 255)))
    assert white["gw"] == white["gh"] == 8
    assert images.spread(white) == 0
    assert images.spread(stripes) > 100
    assert len(images.fingerprint(white)) == 640
    result = images.compare(white, white, stripes, stripes)
    assert result["share"] == 1.0 and result["masked"] == 0
    # A block that moves in the reference is masked
    assert images.compare(white, white, stripes, white)["share"] is None
    assert images.grid(b"not a png") is None


def test_regions_pairs_boxes():
    g = images.grid(images.encode(64, 64, lambda x, y: (255, 255, 255)))
    pairs = images.regions(g, g, [{"box": [0, 0, 64, 64], "elements": 1}], [{"box": [10, 5, 60, 60], "elements": 30}])
    assert pairs[0]["ladybird"] == 0 and pairs[0]["reference_elements"] == 30


def test_png_filters():
    """Filters 1-4 decode like filter 0 (the encoder writes 0; rows are re-filtered here)."""
    import struct
    import zlib

    w, h = 8, 4
    rows = [bytes((x * 30 + y * 7) % 256 for x in range(w * 3)) for y in range(h)]

    def paeth(a, b, c):
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if pa <= pb and pa <= pc else b if pb <= pc else c

    raw = bytearray()
    prev = bytes(w * 3)
    for y, r in enumerate(rows):
        f = y % 4 + 1
        raw.append(f)
        for i, v in enumerate(r):
            a = r[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            pred = {1: a, 2: b, 3: (a + b) // 2, 4: paeth(a, b, c)}[f]
            raw.append((v - pred) & 255)
        prev = r

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    plain = images.encode(w, h, lambda x, y: tuple(rows[y][x * 3 : x * 3 + 3]))
    assert images.grid(png, cell=2) == images.grid(plain, cell=2)


# --- Ladybird's log ---------------------------------------------------------------------------------------------------
LOG = """\
10.1 WebContent(27): Unhandled JavaScript exception: [TypeError] a.b is not a function
10.1 WebContent(27):     at init (https://cdn.example.test/app.js:1:200)
    at run (https://cdn.example.test/app.js:1:300)
10.2 RequestServer(3): Failed load of: "https://www.example.test/x.css", Error: Connection refused, Duration: 12ms
10.3 WebContent(27): FIXME: Unimplemented IDL interface: 'XSLTProcessor'
10.4 RequestServer(3): Unable to map error (92): "Stream error in the HTTP/2 framing layer"
10.5 WebContent(27): ASSERTION FAILED: m_count > 0 at ./Libraries/LibWeb/Layout/Node.h:120
"""


def test_parse_log():
    p = ladybird_log.parse(LOG)
    assert p["exceptions"] == {"[TypeError] a.b is not a function": 1}
    assert p["exception_hosts"] == {"[TypeError] a.b is not a function": "cdn.example.test"}
    assert p["failed_loads"] == [{"url": "https://www.example.test/x.css", "error": "Connection refused"}]
    assert p["missing"] == {"FIXME: Unimplemented IDL interface: 'XSLTProcessor'": 1}
    assert p["curl_errors"] == ["92: Stream error in the HTTP/2 framing layer"]
    assert p["crash_locations"] == ["ASSERT m_count > 0 @ Node.h"]


def test_security_reasons():
    sigsegv = {"crash": ["x"], "locations": ["SIGSEGV"], "reports": []}
    assert ladybird_log.security_reasons(sigsegv) == ["signal SIGSEGV"]
    verify_trap = {"crash": ["x"], "locations": ["VERIFY false @ A.cpp", "SIGILL"], "reports": []}
    assert ladybird_log.security_reasons(verify_trap) == []
    report = {"process": "ImageDecoder", "signal": "SIGABRT"}
    other_process = {"crash": ["x"], "locations": ["SIGABRT"], "reports": [report]}
    assert ladybird_log.security_reasons(other_process) == ["process ImageDecoder"]
    oom = {"crash": ["x"], "locations": ["out-of-memory (log)", "SIGSEGV"], "reports": []}
    assert ladybird_log.security_reasons(oom) == []
    jit = {"crash": ["x"], "locations": ["VERIFY is_cell() @ Heap.cpp"], "reports": []}
    assert ladybird_log.security_reasons(jit) == ["invalid value"]


def test_crash_report_and_reload_offset(tmp_path):
    bundle = Bundle(tmp_path, {"browsers": {}, "log_reload_offset": 10})
    bundle.write_file("ladybird.log", "0123456789" + LOG)
    bundle.write_file("crash-report-1.txt", "Process: RequestServer\nTermination signal: SIGSEGV\n")
    parsed = ladybird_log.for_bundle(bundle)
    assert parsed["first"]["exceptions"] == {}
    assert parsed["full"]["exceptions"]
    assert parsed["crash"]["reports"][0]["process"] == "RequestServer"


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_scripts_parse(tmp_path):
    """Every in-page script is valid JavaScript (node --check on the wrapped body)."""
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("node does not run")
    for name in dir(js):
        value = getattr(js, name)
        if name.isupper() and isinstance(value, str):
            body = value if "return" in value else "return " + value
            path = tmp_path / f"{name}.js"
            path.write_text("(async function () {\n" + body + "\n});\n")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, f"{name}: {result.stderr}"
