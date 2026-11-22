# SPDX-License-Identifier: BSD-2-Clause
"""The visit steps: what is measured in each browser.

Every step is ``fn(handle, ctx) -> dict``; ``webparity.visit`` runs it in all browsers in parallel and stores the
result under the step's name in the browser's measurements (a result with ``"_merge": True`` is merged instead, for
steps that produce several measurements). ``ctx.save(name, bytes)`` writes ``<browser>-<name>`` into the bundle.

Steps, in order: ``load`` (navigate and wait until stable), ``probe`` (the page probe, layout map, first screenshot,
DOM, bot-wall and error-page detection), ``idle`` (three idle seconds: CPU, second screenshot, memory, response
times), ``interact`` (CSS rule comparison, scroll, wheel, mobile viewport, menu) and ``reload`` (load again: stability
and memory in Ladybird, the reference's own noise, then the known-bug page tests). Every key is listed in
``docs/measurements.md``.

Ported from the old explorer's ``besok()``; the dated comments say which false finding each detail fixed. Steps never
raise for a failing measurement: the error is recorded and the step goes on, so the browsers meet where they have to
(the CSS comparison and the page tests need both browsers' results).
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from webparity import js
from webparity.bidi import BidiError

DESKTOP = (1440, 900)
MOBILE = (390, 844)
LINUX = os.path.isdir("/proc/self")

BOT_WALL = re.compile(
    r"just a moment|attention required|checking your browser|verify you are human|access denied|are you a robot|"
    r"captcha|pardon our interruption|request unsuccessful|ddos-guard|please wait while we verify|security checkpoint|"
    r"has been blocked|request rejected|bot protection|are you human|^403 forbidden$|something doesn.t look right",
    re.I,
)  # 2026-11-08: a Vercel checkpoint, an F5 wall
# 2026-11-15: unambiguous wall phrases, also searched in the first 3,000 characters of a page with a lot of text (a site
# showed its own image CAPTCHA under the cookie banner when it rate-limited - BOT_WALL is only searched in the title and
# on almost empty pages)
BOT_WALL_STRONG = re.compile(
    r"enter the characters (?:seen|shown|you see) in the image|verify you are (?:a )?human|"
    r"complete the security check to access|press (?:&|and) hold to confirm you are",
    re.I,
)
ERRORS = (BidiError, OSError, ValueError, TypeError, KeyError, IndexError)


# --- helpers ----------------------------------------------------------------------------------------------------------
def _short(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:150]


def _try(out: dict, key: str, fn: Callable[[], Any], error_key: str | None = None) -> Any:
    """Runs fn; stores its value under key (if any) or the error under error_key (default ``<key>_error``)."""
    try:
        value = fn()
    except ERRORS as exc:
        out[error_key or f"{key}_error"] = _short(exc)
        return None
    if key:
        out[key] = value
    return value


def _json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode()


def wait_stable(h, timeout: float, minimum: float = 3) -> tuple[float, bool]:
    """Waits until elements, resources and text length are unchanged for two seconds and readyState is complete."""
    t0, previous, same = time.time(), None, 0
    while time.time() - t0 < timeout:
        time.sleep(1)
        try:
            current = h.js(js.STABLE, timeout=10)
        except ERRORS:
            current = None
        same = same + 1 if current is not None and current == previous and current[3] == "complete" else 0
        previous = current
        if same >= 2 and time.time() - t0 >= minimum:
            return round(time.time() - t0, 1), True
    return round(time.time() - t0, 1), False


def browsers_of(bundle) -> list[str]:
    configured = (bundle.data.get("config") or {}).get("browsers")
    return list(configured) if configured else list(bundle.data.get("browsers") or {})


def reference_name(bundle) -> str | None:
    """The reference Ladybird is compared with: Chromium when it takes part, else the first other browser."""
    names = [b for b in browsers_of(bundle) if b != "ladybird"]
    return "chromium" if "chromium" in names else (names[0] if names else None)


class _Meeting:
    def __init__(self):
        self.cond = threading.Condition()
        self.values: dict[str, Any] = {}


def meet(ctx, key: str, value: Any, expected: set[str], timeout: float = 120) -> dict[str, Any]:
    """Posts this browser's value and waits (at most timeout) until every expected browser has posted; returns all."""
    meeting = ctx.shared.setdefault(f"meeting:{key}", _Meeting())
    deadline = time.monotonic() + timeout
    with meeting.cond:
        meeting.values[ctx.browser] = value
        meeting.cond.notify_all()
        while not expected <= set(meeting.values):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            meeting.cond.wait(left)
        return dict(meeting.values)


def _active(bundle, step: str) -> set[str]:
    return {b for b in browsers_of(bundle) if should_run(step, b, bundle)}


# --- processes (the worker shares the PID namespace with the browsers) ------------------------------------------------
def _read(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def renderer_pids(browser: str) -> list[str]:
    """Ladybird's WebContent processes, Chromium's renderers, Firefox's content processes."""
    out = []
    for pid in os.listdir("/proc") if LINUX else []:
        if not pid.isdigit():
            continue
        if browser == "ladybird":
            if _read(f"/proc/{pid}/comm").strip() == "WebContent":
                out.append(pid)
            continue
        cmdline = _read(f"/proc/{pid}/cmdline").replace("\0", " ")
        if browser == "chromium" and "--type=renderer" in cmdline and "--extension-process" not in cmdline:
            out.append(pid)
        elif browser == "firefox" and "-contentproc" in cmdline and " tab" in cmdline:
            out.append(pid)
    return out


def cpu_times(pids: list[str]) -> dict[str, int]:
    t = {}
    for pid in pids:
        stat = _read(f"/proc/{pid}/stat")
        try:
            fields = stat.rsplit(")", 1)[1].split()
            t[pid] = int(fields[11]) + int(fields[12])
        except (IndexError, ValueError):
            pass
    return t


def rss_mb(browser: str) -> int | None:
    if not LINUX:
        return None
    rss = 0
    for pid in renderer_pids(browser):
        try:
            rss += int(_read(f"/proc/{pid}/statm").split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (IndexError, ValueError):
            pass
    return round(rss / 2**20)


def cpu(h, browser: str, seconds: float = 3) -> dict:
    """CPU use of the page's renderer process over ``seconds`` while the page is idle, in percent.

    Only the main page's process is compared with the reference (2026-11-08): iframes in their own processes (ads; a new
    process every 15 s on one site) were otherwise counted in Ladybird. The process is found with a short busy loop in
    the page; the excess over each process's own rate decides (2026-11-11: an iframe process already at 100 % - five
    background videos - got as much time during the loop as the main page and was sometimes chosen). The sum over all
    renderer processes is kept as ``cpu_idle_all``."""
    out: dict[str, Any] = {"cpu_idle": None, "cpu_idle_all": None}
    if not LINUX:
        time.sleep(seconds)
        return out
    pids = renderer_pids(browser)
    t, c0 = time.time(), cpu_times(pids)
    time.sleep(seconds)
    c1 = cpu_times(pids)
    elapsed = max(0.001, time.time() - t)
    tick = os.sysconf("SC_CLK_TCK")
    per = {p: (c1[p] - c0[p]) / tick / elapsed * 100 for p in c1 if p in c0}
    if not per:
        return out
    out["cpu_idle_all"] = round(sum(per.values()))
    main = next(iter(per)) if len(per) == 1 else None
    if len(per) > 1:
        try:
            tb0, b0 = time.time(), cpu_times(pids)
            h.js(js.BUSY, timeout=10)
            b1, tb1 = cpu_times(pids), time.time()

            def rate(p):
                return per.get(p, 0) / 100 * tick * (tb1 - tb0)

            main = max((p for p in b1 if p in b0), key=lambda p: b1[p] - b0[p] - rate(p), default=None)
        except ERRORS:
            main = None
    out["cpu_idle"] = round(per[main]) if main in per else out["cpu_idle_all"]
    return out


def response_times(h) -> dict:
    out: dict[str, Any] = {}
    _try(out, "nav", lambda: h.js(js.NAV_TIMING))
    times = []
    for _ in range(3):
        t = time.time()
        try:
            h.js("return 1")
            times.append(round((time.time() - t) * 1000))
        except ERRORS:
            times.append(30000)
        time.sleep(0.3)
    out["response_ms"] = max(times)
    return out


# 2026-11-22: DataDome's interstitial (to.skruvat.se in Ladybird); normal pages load DataDome from js.datadome.co
BOT_WALL_MARKUP = re.compile(r"(?:geo|ct)\.captcha-delivery\.com", re.I)


def blocked(probe: dict, body_text: str, dom: str = "") -> bool:
    """A bot wall: in the title, or in the text of an almost empty page (an "Access Denied: error code ..." page), or an
    unambiguous wall phrase in the first 3,000 characters, or an almost empty page whose markup loads a wall (DataDome's
    page is only a script and an iframe, with the site's name as the title)."""
    almost_empty = (probe.get("text") or 0) < 400
    short = body_text[:400] if almost_empty else ""
    return bool(
        BOT_WALL.search(probe.get("title") or "")
        or BOT_WALL.search(short)
        or BOT_WALL_STRONG.search(body_text or "")
        or (almost_empty and BOT_WALL_MARKUP.search(dom[:20000]))
    )


def error_page(probe: dict, load_result: dict | None) -> bool:
    """A browser error page, an empty page or a "404" page."""
    title = probe.get("title") or ""
    load_result = load_result or {}
    return bool(
        (not title and (probe.get("text") or 0) < 200)
        or title.startswith(("Can’t reach", "Can't reach"))
        or (probe.get("url") or "").startswith("chrome-error:")  # Chromium's error page has the domain as its title
        or probe.get("net_error_page")  # ... and sometimes still the site's address (ERR_HTTP2_PROTOCOL_ERROR)
        or (load_result.get("error") and not probe.get("elements"))
        or probe.get("error")
        or re.search(r"\b(404|not found|page not found)\b|\bError$", title, re.I)
    )


def pointer_away(h) -> None:
    """Moves the WebDriver pointer to (1, 1) (2026-11-12). Ladybird keeps the pointer position between pages and sets
    :hover on what ends up under it on the next page; after the wheel test (in the middle of the window) and the menu
    click that opened a site builder's submenus only in Ladybird (layout:extra:block>none≠block)."""
    try:
        h.actions(
            [
                {
                    "type": "pointer",
                    "id": "mouse",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [{"type": "pointerMove", "x": 1, "y": 1, "origin": "viewport", "duration": 0}],
                }
            ]
        )
    except ERRORS:
        pass


def _log_slice(ctx) -> bytes | None:
    start = ctx.shared.get("log_start")
    path = (ctx.config.get("ladybird") or {}).get("log")
    if start is None or not path or not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        f.seek(start)
        return f.read(20_000_000)


# --- load, probe, idle ------------------------------------------------------------------------------------------------
def load(h, ctx) -> dict:
    if ctx.browser == "ladybird" and "log_start" not in ctx.shared:
        path = (ctx.config.get("ladybird") or {}).get("log")
        try:
            ctx.shared["log_start"] = os.path.getsize(path) if path else None
        except OSError:
            ctx.shared["log_start"] = None
    t0 = time.time()
    out: dict = {}
    error = h.navigate(ctx.url)
    if error:
        out["error"] = error
    out["seconds"] = round(time.time() - t0, 1)
    out["waited"], out["stable"] = wait_stable(h, ctx.config["visit"]["stable_timeout"])
    return out


def probe(h, ctx) -> dict:
    out: dict = {}
    try:
        result = h.js(js.PROBE, timeout=30)
        out.update(result if isinstance(result, dict) else {"error": f"unexpected probe result: {str(result)[:100]}"})
    except ERRORS as exc:
        out["error"] = _short(exc)
    if "error" not in out:
        t = time.time()
        layout = _try(out, "", lambda: h.js(js.LAYOUT, timeout=30), "layout_error")
        if isinstance(layout, list) and layout:
            ctx.save("layout.json.gz", _json(layout))
        out["layout_seconds"] = round(time.time() - t, 2)
    shot = _try(out, "", h.screenshot, "screenshot_error")
    if shot:
        ctx.save("load.png", shot)
        if ctx.browser == "ladybird":  # the regression watch compares this from build to build
            from webparity import images

            out["fingerprint"] = images.fingerprint(images.grid(shot))
    dom = _try(out, "", lambda: h.js(js.OUTER_HTML, timeout=30), "dom_error")
    if isinstance(dom, str):
        ctx.save("dom.html.gz", dom)
    body = _try(out, "", lambda: h.js(js.BODY_TEXT, timeout=15), "body_text_error") or ""
    load_result = ctx.measurements().get("load") or {}
    out["blocked"] = blocked(out, body if isinstance(body, str) else "", dom if isinstance(dom, str) else "")
    if ctx.browser != "ladybird" and load_result.get("error") and not out.get("elements"):
        out["blocked"] = True
    out["error_page"] = error_page(out, load_result)
    return out


def idle(h, ctx) -> dict:
    out: dict = cpu(h, ctx.browser)
    out["memory_mb"] = rss_mb(ctx.browser)
    shot = _try(out, "", h.screenshot, "screenshot_error")
    if shot:
        ctx.save("idle.png", shot)
    out.update(response_times(h))
    return out


# --- the CSS rule comparison ------------------------------------------------------------------------------------------
CSS_NOISE = re.compile(
    r"^(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|calc|min|max|clamp|var|url|local|format|attr|env)$", re.I
)
# Chromium's split of border-spacing
CSS_REFERENCE_INTERNAL = {"-webkit-border-horizontal-spacing", "-webkit-border-vertical-spacing"}
IMPLICIT = ("initial", "normal", "auto", "none", "inherit", "unset", "revert", "")


def value_kind(v: str) -> str:
    """The kind of a CSS value for the signature: the first function name (except serialization noise), else the
    keyword or the unit (10cqw -> cqw)."""
    f = [x for x in re.findall(r"([-\w]+)\(", v or "") if not CSS_NOISE.match(x)]
    if f:
        return f[0].lower()
    m = re.match(r"\s*(-?[a-z][-a-z]*)\b", v or "", re.I)
    if m:
        return m.group(1).lower()
    m = re.match(r"\s*-?[\d.]+([a-z%]+)", v or "", re.I)
    return m.group(1).lower() if m else "value"


def _alternatives(prop: str) -> list[str]:
    # Properties missing from Ladybird's list can be a shorthand there (-webkit-line-clamp is split into max-lines,
    # block-ellipsis and continue) - ask for the value directly; and for the unprefixed name and the shorthand without
    # -x/-y (-webkit-mask-position-x -> mask-position-x, mask-position): Ladybird treats -webkit-mask as an alias of
    # mask
    # and stores only the unprefixed names
    unprefixed = re.sub(r"^-(webkit|moz)-", "", prop)
    return list(dict.fromkeys([prop, unprefixed, re.sub(r"-[xy]$", "", unprefixed)]))


def css_comparison(h, ctx, expected: set[str]) -> dict | None:
    """Step 1 in both browsers, step 2 for the rules that differ (at most 300), step 3 and CSS.supports in Ladybird.

    Returns (in Ladybird) ``{"rules": [ladybird, reference], "differing": n, "dropped": [...]}``; see
    ``compare_css``."""
    me, ref = ctx.browser, next(iter(expected - {"ladybird"}))
    try:
        step1: Any = h.js(js.CSS_STEP1, timeout=30)
    except ERRORS as exc:
        step1 = _short(exc)
    got = meet(ctx, "css1", step1, expected, timeout=90)
    a, b = got.get("ladybird"), got.get(ref)
    if not isinstance(a, dict) or not isinstance(b, dict):
        return {"error": str(a if not isinstance(a, dict) else b)[:120]} if me == "ladybird" else None
    differing = [k for k in b if k in a and a[k] != b[k]][:300]
    if not differing:
        return {"rules": [len(a), len(b)], "differing": 0, "dropped": []} if me == "ladybird" else None
    try:
        step2: Any = h.js(js.CSS_STEP2, differing, timeout=30)
    except ERRORS as exc:
        step2 = _short(exc)
    got = meet(ctx, "css2", step2, expected, timeout=90)
    if me != "ladybird":
        return None
    da, db = got.get("ladybird"), got.get(ref)
    if not isinstance(da, dict) or not isinstance(db, dict):
        return {"error": "step 2"}
    return compare_css(h, a, b, da, db, differing)


def compare_css(h, a: dict, b: dict, da: dict, db: dict, differing: list[str]) -> dict:
    """``dropped``: properties the reference has and Ladybird lacks in the same rule, or where the reference's value has
    a function Ladybird's value lacks, grouped by (property, value kind): ``{"property", "kind", "count", "examples",
    "supported"}``. Implicit values (initial, normal ...) are not counted - they come from a shorthand being split
    differently. ``supported`` is ``CSS.supports(property, 'initial')`` in Ladybird."""
    ask = [
        [k, q]
        for k in differing
        for prop in ((db.get(k) or {}).get("p") or {})
        if prop not in ((da.get(k) or {}).get("p") or {})
        for q in _alternatives(prop)
    ][:900]
    direct: dict = {}
    if ask:
        try:
            direct = h.js(js.CSS_STEP3, ask, timeout=30) or {}
        except ERRORS:
            direct = {}
    groups: dict = {}
    for k in differing:
        lb_props, ref_props = dict((da.get(k) or {}).get("p") or {}), (db.get(k) or {}).get("p") or {}
        dk = direct.get(k) or {}
        for prop in ref_props:
            if prop not in lb_props:
                v = next(
                    (dk.get(q) or lb_props.get(q) for q in _alternatives(prop) if dk.get(q) or lb_props.get(q)), None
                )
                if v:
                    lb_props[prop] = v
        for prop, v in ref_props.items():
            if prop in CSS_REFERENCE_INTERNAL:
                continue
            if v.strip().lower() in IMPLICIT and prop not in lb_props:
                continue
            fn = [x for x in re.findall(r"([-\w]+)\(", v) if not CSS_NOISE.match(x)]
            if prop not in lb_props or (fn and fn[0].lower() not in (lb_props.get(prop) or "").lower()):
                kind = value_kind(v)
                g = groups.setdefault((prop, kind), {"property": prop, "kind": kind, "count": 0, "examples": []})
                g["count"] += 1
                if len(g["examples"]) < 3:
                    g["examples"].append(
                        {"rule": (db.get(k) or {}).get("sel"), "reference": v[:100], "ladybird": lb_props.get(prop)}
                    )
    dropped = sorted(groups.values(), key=lambda g: -g["count"])[:10]
    # Does Ladybird support the property at all? Otherwise it is a missing feature, not a dropped value
    if dropped:
        try:
            supported = h.js(js.CSS_SUPPORTS, [g["property"] for g in dropped], timeout=10)
            for g, s in zip(dropped, supported or [], strict=False):
                g["supported"] = bool(s)
        except ERRORS:
            pass
    return {"rules": [len(a), len(b)], "differing": len(differing), "dropped": dropped}


# --- interaction ------------------------------------------------------------------------------------------------------
def scroll(h) -> dict:
    """Scrolls down a screen at a time (at most 12) and back. Measures how the page grows: lazy loading, infinite
    scroll."""
    try:
        start = h.js(js.SCROLL_STATE)
        previous, still = None, 0
        for _ in range(12):
            now = h.js(js.SCROLL_STEP)
            time.sleep(0.35)
            still = still + 1 if previous and now[0] == previous[0] else 0
            previous = now
            if still >= 2:
                break
        time.sleep(1)
        end = h.js(js.SCROLL_STATE)
        h.js(js.SCROLL_TOP)
        return {
            "height": [start[1], end[1]],
            "elements": [start[2], end[2]],
            "resources": [start[3], end[3]],
            "bottom": end[0],
        }
    except ERRORS as exc:
        return {"error": _short(exc)}


def wheel(h) -> dict:
    """The scroll detector (2026-11-10, #14254: a career-site vendor's pages could not be scrolled with the mouse in
    Ladybird): one wheel step down (deltaY 400) in the middle of the window, and how far the page and inner elements
    scrolled. Only when the page is at least 200 px taller than the window. Back to the top afterwards."""
    try:
        before = h.js(js.WHEEL_READ)
        if before[1] <= before[2] + 200:
            return {"scrollable": False}
        h.wheel(int(before[3] // 2), int(before[2] // 2), 400)
        time.sleep(1)
        after = h.js(js.WHEEL_READ)
        overflow_y = h.js("return " + js.VIEWPORT_OVERFLOW_Y)
        h.js(js.SCROLL_TOP)
        return {"scrollable": True, "scrolled": after[0] - before[0], "viewport_overflow_y": overflow_y}
    except ERRORS as exc:
        return {"error": _short(exc)}
    finally:
        pointer_away(h)


def mobile(h, ctx) -> dict:
    """Mobile width (2026-11-09): the viewport 390 x 844 without reloading (media queries switch), the layout map, a
    screenshot and the horizontal overflow, then back."""
    out: dict = {}
    original = tuple(h.viewport) if getattr(h, "viewport", None) else DESKTOP
    try:
        h.set_viewport(*MOBILE)
        time.sleep(2.5)
        out["viewport"] = h.js("return [innerWidth, innerHeight]")
        out["overflow"] = h.js(js.OVERFLOW)
        out["viewport_overflow_x"] = h.js("return " + js.VIEWPORT_OVERFLOW_X)
        if (out["overflow"] or 0) > 20:
            out["above"] = h.js("return " + js.ABOVE, timeout=30)
        layout = h.js(js.LAYOUT, timeout=30)
        if isinstance(layout, list) and layout:
            ctx.save("layout-mobile.json.gz", _json(layout))
        shot = h.screenshot()
        if shot:
            ctx.save("mobile.png", shot)
    except ERRORS as exc:
        out["error"] = _short(exc)
    finally:
        try:
            h.set_viewport(*original)
            time.sleep(0.5)
        except ERRORS:
            pass
    return out


def menu(h) -> dict | None:
    """Opens the first visible menu button (aria-expanded=false) in the header. No links, no forms. The click is real
    input when the button is the element at its center (as WebDriver's element click checks), else a JS click; the
    detector compares only menus clicked the same way. The pointer is moved away afterwards."""
    try:
        found = h.js(js.MENU_FIND)
        if not found:
            return None
        found = dict(found)
        center = found.pop("center", None)
        if center and h.js(js.MENU_HIT):
            try:
                h.click_at(center[0], center[1])
                found["method"] = "input"
            except ERRORS:
                h.js(js.MENU_JS_CLICK)
                found["method"] = "js"
        else:
            h.js(js.MENU_JS_CLICK)
            found["method"] = "js"
        time.sleep(1)
        found.update(h.js(js.MENU_READ) or {})
        return found
    except ERRORS as exc:
        return {"error": _short(exc)}
    finally:
        pointer_away(h)


def interact(h, ctx) -> dict:
    out: dict = {"_merge": True}
    bundle = ctx.bundle
    ref = reference_name(bundle)
    expected = {b for b in ("ladybird", ref) if b in _active(bundle, "interact")}
    if ctx.browser in expected and len(expected) == 2 and not bundle.m(ref, "probe", "error_page"):
        try:
            css = css_comparison(h, ctx, expected)
        except ERRORS as exc:
            css = {"error": _short(exc)} if ctx.browser == "ladybird" else None
        if css is not None:
            out["css"] = css
    # The wheel test first, on the page as loaded (2026-11-22): after the scroll test's scrollBy/scrollTo steps
    # Ladybird's wheel scrolled 0 px on some pages (se.recoma.com 3 of 4, j3m.se) - a Ladybird bug of its own, but it
    # made scroll-locked 14 % of all visits instead of measuring what a visitor's first wheel step does.
    out["wheel"] = wheel(h)
    out["scroll"] = scroll(h)
    # Before the menu test - a menu that opened only in one browser would otherwise give differences
    out["mobile"] = mobile(h, ctx)
    out["menu"] = menu(h)
    return out


# --- reload and the known-bug page tests ------------------------------------------------------------------------------
def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return True
    except (socket.gaierror, UnicodeError, OSError):
        return False


def reload(h, ctx) -> dict:
    """Load again. Ladybird: stability and memory - more reloads only while memory grows noticeably, up to four, so that
    memory that levels off (1.6-2 GB, released at about:blank) is told apart from a leak. The reference reloads at the
    same time (2026-11-09): what differs between its own two loads (ads, A/B tests, random images, cookie dialogs) is
    noise, masked in the image and layout comparisons. Then the known-bug page tests (``webparity.known``)."""
    out: dict = {}
    if ctx.browser == "ladybird":
        memory = [ctx.measurements().get("idle", {}).get("memory_mb")]
        for i in range(4):
            r = load(h, ctx)
            memory.append(rss_mb("ladybird"))
            if i == 0:
                out.update({k: r[k] for k in ("seconds", "waited", "stable", "error") if k in r})
                counts = _try(out, "", lambda: h.js(js.COUNTS), "counts_error")
                if isinstance(counts, list) and len(counts) == 2:
                    out["elements"], out["text"] = counts
                _reload_artifacts(h, ctx, out)
            if not (memory[-1] and memory[-2] and memory[-1] - memory[-2] >= 100):
                break
        out["memory"] = memory
    else:
        out.update(load(h, ctx))
        _reload_artifacts(h, ctx, out)
    try:
        out["known_tests"] = page_tests(h, ctx)
    except Exception as exc:  # the tests must never lose the visit
        out["known_tests_error"] = _short(exc)
    if ctx.browser == "ladybird":
        out["unresolvable_hosts"] = _unresolvable_hosts(ctx)
    return out


def _reload_artifacts(h, ctx, out: dict) -> None:
    shot = _try(out, "", h.screenshot, "screenshot_error")
    if shot:
        ctx.save("reload.png", shot)
    layout = _try(out, "", lambda: h.js(js.LAYOUT, timeout=30), "layout_error")
    if isinstance(layout, list) and layout:
        ctx.save("layout-reload.json.gz", _json(layout))


def _unresolvable_hosts(ctx) -> list[str]:
    """Hosts in "Unable to resolve host" failures that do not resolve here either (2026-11-10: Chromium adds a Resource
    Timing entry for an iframe whose navigation fails, so a failed load of a host that doesn't exist is no Ladybird
    error). Looked up during the visit so the analysis stays offline."""
    from urllib.parse import urlsplit

    raw = _log_slice(ctx)
    if not raw:
        return []
    hosts = set()
    for m in re.finditer(rb'Failed load of: "([^"]*)", Error: [^\n]*resolve host', raw):
        try:
            hosts.add(urlsplit(m.group(1).decode("utf-8", "replace")).hostname or "")
        except ValueError:
            pass
    return sorted(host for host in hosts if host and not _resolves(host))[:20]


def page_tests(h, ctx) -> dict:
    """Runs the known-bug page tests a preliminary analysis asks for, in this browser.

    The leader (Ladybird, or the reference when Ladybird is not reloading) waits until every browser has reloaded,
    writes the log so far into the bundle (``visit`` rewrites it with the whole slice later), runs the detectors and
    picks the tests (``Known.page_tests_for``); the others wait for that and run the tests assigned to them."""
    from webparity.known import Known, build_commit

    bundle = ctx.bundle
    active = _active(bundle, "reload")
    ref = reference_name(bundle)
    leader = "ladybird" if "ladybird" in active else ref
    meet(ctx, "reloaded", True, active, timeout=300)
    ready = ctx.shared.setdefault("page_tests_ready", threading.Event())
    if ctx.browser == leader:
        try:
            known = Known.from_config(ctx.config)
            log = _log_slice(ctx)
            if log is not None:
                bundle.write_file("ladybird.log", log)
            from webparity.detectors import analyze

            findings = analyze(bundle)
            # The final analysis must not see results from this preliminary one; decoded screenshots and layout maps
            # stay (every file they come from is complete by now), so the PNGs are decoded once per visit
            cache = bundle.__dict__.get("_detector_cache") or {}
            for key in [k for k in cache if not k.startswith(("grid:", "layout:"))]:
                del cache[key]
            bundle.__dict__.pop("_ladybird_log", None)
            errors = [f.signature for f in findings if f.category == "error"]
            ctx.shared["page_tests"] = known.page_tests_for(errors, build_commit(bundle))
        finally:
            ready.set()
    else:
        ready.wait(120)
    tests = ctx.shared.get("page_tests") or []
    reference_usable = bool(ref) and ref in active and not bundle.m(ref, "probe", "blocked")
    results: dict[str, Any] = {}
    for entry, signatures in tests:
        runs_in = "ladybird" if entry.run_in == "ladybird" or not reference_usable else ref
        if runs_in == ctx.browser:
            results[entry.id] = run_page_test(h, entry, signatures)
    return results


def run_page_test(h, entry, signatures: list[str]) -> Any:
    mobile_view = entry.wants_mobile(signatures)
    original = tuple(h.viewport) if getattr(h, "viewport", None) else DESKTOP
    try:
        if mobile_view:
            h.set_viewport(*MOBILE)
            time.sleep(2.5)
        value = h.js(entry.script(), timeout=30 if entry.is_async else 20)
        return value if isinstance(value, (bool, int, float, str, list)) or value is None else str(value)[:200]
    except ERRORS as exc:
        return {"error": _short(exc)}
    finally:
        if mobile_view:
            try:
                h.set_viewport(*original)
                time.sleep(0.5)
            except ERRORS:
                pass


STEPS = {"load": load, "probe": probe, "idle": idle, "interact": interact, "reload": reload}


def should_run(step: str, browser: str, bundle) -> bool:
    """Whether a step runs in a browser, given what the visit has measured so far."""
    if step in ("load", "probe"):
        return True
    load_result = bundle.m(browser, "load") or {}
    if load_result.get("error") and not bundle.m(browser, "probe", "elements"):
        return False
    if browser != "ladybird" and bundle.m(browser, "probe", "blocked"):
        return False
    # No interaction and no reload when Ladybird got an error page: there is nothing to compare
    if step in ("interact", "reload") and bundle.m("ladybird", "probe", "error_page"):
        return False
    return True
