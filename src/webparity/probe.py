# SPDX-License-Identifier: BSD-2-Clause
"""``webparity probe``: load URLs or local test cases in the browsers and print what a script returns in each.

It keeps the semantics of the old probe service, so results can be compared:

- after navigation, wait for ``readyState == "complete"`` (at most 20 s) and ``document.fonts.status == "loaded"``
  (at most 10 s), then a fixed wait: 0.3 s for local test cases, 5 s for real sites;
- without a script: the text of the test case's ``#out`` (waiting at most 5 s for it to be filled), else the title;
- with a script: it runs as an expression if it parses as one, otherwise as statements with their own ``return``.

Local test cases are served from a directory on ``http://127.0.0.1:PORT/``; ``http://localhost:PORT/`` is the same
server on a different origin, for cross-origin tests (``--cross-origin`` loads the targets from there). The server
understands three test helpers: ``?sleep=MS`` delays the response (a slow resource), ``?throttle=KB`` sends the body at
that many kB per second (a page left while a body streams in), and ``GET /_count?PATH`` answers how many times
``PATH`` was requested.

Per target and browser the order is: load, wait, ``before`` script, ``after`` seconds, wheel step, 1 s, script,
screenshot. Ladybird's log lines about crashes, panics and VERIFY/ASSERT failures during the target are reported
with it.
"""

from __future__ import annotations

import base64
import functools
import http.server
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from webparity.bidi import BidiError, Session

ENDPOINTS = {
    "ladybird": os.environ.get("WEBPARITY_LADYBIRD", "http://127.0.0.1:9616"),
    "chromium": os.environ.get("WEBPARITY_CHROMIUM", "http://127.0.0.1:9515"),
    "firefox": os.environ.get("WEBPARITY_FIREFOX", "ws://127.0.0.1:9222"),
}
# The same Chromium flags as the explorer: no scrollbars (the same layout width as Ladybird) and WebGL in software.
CHROMIUM_ARGS = ["--hide-scrollbars", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
DESKTOP = (1440, 900)
MOBILE = (390, 844)

READ_OUT = "const o = document.getElementById('out'); return o ? o.textContent : null"
# #out text that means "not done yet"
PLACEHOLDERS = {"", "running", "loading", "waiting"}


def wrap_script(code: str, as_expression: bool) -> str:
    """A function declaration for ``script.callFunction`` that returns a string, a JSON text or null."""
    body = f"return ({code.strip().rstrip(';')}\n);" if as_expression else code
    return (
        "async function () {\n"
        f"  const value = await (async () => {{\n{body}\n}})();\n"
        "  if (value === undefined || value === null) return null;\n"
        "  return typeof value === 'string' ? value : JSON.stringify(value);\n"
        "}"
    )


@dataclass
class Result:
    target: str
    browser: str
    value: str | None = None
    error: str | None = None
    ms: int = 0
    screenshot: str | None = None
    log: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class Steps:
    """What happens after the load, per target and browser."""

    before: str | None = None  # a script run before the wheel step and the measurement
    after: float = 0  # seconds to wait after ``before``
    wheel: bool = False  # one wheel step
    wheel_target: str | None = None  # over this element (a CSS selector), else the middle of the viewport
    wheel_dy: int = 400
    screenshot_dir: str | None = None  # save a screenshot per browser here


LOG_LINE = re.compile(
    r"panicked at|VERIFICATION FAILED|ASSERTION FAILED|Verification failed|Assertion failed|"
    r"process crashed|Segmentation fault|SIGSEGV|SIGABRT|SIGILL|SIGTRAP"
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def log_size(path: str | None) -> int:
    try:
        return os.path.getsize(path) if path else 0
    except OSError:
        return 0


def log_lines_since(path: str | None, offset: int, limit: int = 12) -> list[str]:
    """Ladybird's log lines about crashes, panics and VERIFY/ASSERT failures written after ``offset``."""
    if not path:
        return []
    try:
        with open(path, "rb") as f:
            if os.path.getsize(path) < offset:
                offset = 0  # the log was rotated or truncated
            f.seek(offset)
            text = f.read(4_000_000).decode("utf-8", "replace")
    except OSError:
        return []
    lines = [ANSI.sub("", line).strip() for line in text.splitlines()]
    return [line[:300] for line in lines if LOG_LINE.search(line)][:limit]


class Server:
    """Serves a directory on 127.0.0.1 (and localhost, the same server on another origin)."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        handler = functools.partial(_QuietHandler, directory=str(self.root))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url_for(self, path: Path) -> str:
        rel = path.resolve().relative_to(self.root)
        return f"http://127.0.0.1:{self.port}/{rel.as_posix()}"

    def close(self) -> None:
        self.httpd.shutdown()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    counts: dict[str, int] = {}

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/_count":
            body = json.dumps(self.counts.get(query, 0)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.counts[path] = self.counts.get(path, 0) + 1
        m = re.search(r"(?:^|&)sleep=(\d+)", query)
        if m:
            time.sleep(min(int(m.group(1)), 30000) / 1000)
        m = re.search(r"(?:^|&)throttle=(\d+)", query)
        if m:
            try:
                file = self.translate_path(path)
                data = Path(file).read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(file))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            pause = 1 / max(int(m.group(1)), 1)
            try:
                for i in range(0, len(data), 1024):
                    self.wfile.write(data[i : i + 1024])
                    self.wfile.flush()
                    time.sleep(pause)
            except OSError:
                pass  # the page was left while the body streamed in
            return
        super().do_GET()


def resolve_targets(
    targets: list[str], root: str | None, cross_origin: bool = False
) -> tuple[list[tuple[str, bool]], Server | None]:
    """Turns arguments into (URL, is_test_case) pairs, starting a server for local files."""
    files = [Path(t.split("?", 1)[0]) for t in targets if not t.startswith(("http://", "https://"))]
    server = None
    if files:
        base = Path(root) if root else Path(os.path.commonpath([str(f.resolve().parent) for f in files]))
        server = Server(base)
    out = []
    for t in targets:
        if t.startswith(("http://", "https://")):
            out.append((t, False))
        else:
            path, _, query = t.partition("?")
            url = server.url_for(Path(path)) + (f"?{query}" if query else "")
            if cross_origin:
                url = url.replace("http://127.0.0.1:", "http://localhost:", 1)
            out.append((url, True))
    return out, server


class Browser:
    """One browser with a long-lived session (or a fresh one per target)."""

    def __init__(self, name: str, mobile: bool, proxy: dict | None = None):
        self.name, self.mobile, self.proxy = name, mobile, proxy
        self.session: Session | None = None
        self.context: str | None = None

    def start(self) -> None:
        endpoint = ENDPOINTS[self.name]
        if self.name == "ladybird":
            self.session = Session.ladybird(endpoint)
        elif self.name == "chromium":
            size = MOBILE if self.mobile else DESKTOP
            args = [*CHROMIUM_ARGS, f"--window-size={size[0]},{size[1]}"]
            if self.proxy:  # the recording proxy (Ladybird and Firefox get it from their containers)
                args.append(f"--proxy-server={self.proxy['address']}")
                if self.proxy.get("spki"):
                    args.append(f"--ignore-certificate-errors-spki-list={self.proxy['spki']}")
            self.session = Session.chromium(endpoint, args)
        else:
            self.session = Session.firefox(endpoint)
        self.context = self.session.client.command("browsingContext.getTree", {})["contexts"][0]["context"]
        self._set_viewport()

    def _set_viewport(self) -> None:
        width, height = MOBILE if self.mobile else DESKTOP
        try:
            self.session.client.command(
                "browsingContext.setViewport", {"context": self.context, "viewport": {"width": width, "height": height}}
            )
        except BidiError:
            if self.session.classic is not None:  # Ladybird without setViewport: resize the window instead
                self.session.classic_call("POST", "/window/rect", {"width": width, "height": height})

    def stop(self) -> None:
        if self.session is not None:
            try:
                self.session.end()
            except (BidiError, OSError):
                pass
            self.session = None

    def call(self, function: str, timeout: float = 30) -> Any:
        result = self.session.client.command(
            "script.callFunction",
            {"functionDeclaration": function, "target": {"context": self.context}, "awaitPromise": True},
            timeout=timeout,
        )
        if result.get("type") == "exception":
            details = result.get("exceptionDetails", {})
            raise BidiError(f"script exception: {details.get('text', details)}")
        value = result.get("result", {})
        return value.get("value") if value.get("type") not in ("null", "undefined") else None

    def run_steps(self, steps: Steps) -> None:
        if steps.before:
            try:
                self.call(wrap_script(steps.before, True))
            except BidiError as exc:
                if "SyntaxError" not in str(exc):
                    raise
                self.call(wrap_script(steps.before, False))
            if steps.after:
                time.sleep(steps.after)
        if steps.wheel:
            width, height = MOBILE if self.mobile else DESKTOP
            x, y = width // 2, height // 2
            if steps.wheel_target:
                rect = self.call(
                    wrap_script(
                        "const e = document.querySelector(" + json.dumps(steps.wheel_target) + "); if (!e) return null;"
                        " const r = e.getBoundingClientRect();"
                        " return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)}",
                        False,
                    )
                )
                if rect is None:
                    raise BidiError(f"wheel: no element {steps.wheel_target}")
                point = json.loads(rect) if isinstance(rect, str) else rect
                x = max(0, min(width - 1, int(point["x"])))
                y = max(0, min(height - 1, int(point["y"])))
            actions = [
                {
                    "type": "wheel",
                    "id": "wheel",
                    "actions": [{"type": "scroll", "x": x, "y": y, "deltaX": 0, "deltaY": steps.wheel_dy}],
                }
            ]
            self.session.client.command("input.performActions", {"context": self.context, "actions": actions}, 30)
            try:
                self.session.client.command("input.releaseActions", {"context": self.context}, timeout=10)
            except BidiError:
                pass
            time.sleep(1)

    def screenshot(self, folder: str, target: str) -> str:
        data = self.session.client.command("browsingContext.captureScreenshot", {"context": self.context}, timeout=30)
        stem = re.sub(r"[^A-Za-z0-9.-]+", "_", target.split("://", 1)[-1])[:80].strip("_")
        path = Path(folder) / f"{time.strftime('%Y%m%d-%H%M%S')}-{self.name}-{stem}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data["data"]))
        return str(path)

    def run_script(self, code: str | None, test_case: bool) -> str | None:
        if not code:
            if not test_case:
                return self.call(wrap_script("document.title", True))
            # Test cases fill #out in load, fonts.ready or a timeout, and many start with a placeholder ("running...").
            # Wait at most 5 s for real text; a page that navigates by itself may be between documents when read.
            # A missing #out is waited for 1 s only: it may be a document change, or a test case without #out.
            start = time.monotonic()
            deadline = start + 5
            while True:
                try:
                    text = self.call(wrap_script(READ_OUT, False))
                except BidiError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.1)
                    continue
                now = time.monotonic()
                if text is None:
                    if now - start > 1:
                        return self.call(wrap_script("document.title", True))
                elif text.strip().lower().rstrip(".") not in PLACEHOLDERS or now > deadline:
                    return text
                time.sleep(0.1)
        try:
            return self.call(wrap_script(code, True))
        except BidiError as exc:
            if "SyntaxError" not in str(exc):
                raise
            return self.call(wrap_script(code, False))

    def load(self, url: str, test_case: bool, wait: float | None) -> None:
        client = self.session.client
        try:
            client.command(
                "browsingContext.navigate", {"context": self.context, "url": url, "wait": "complete"}, timeout=60
            )
        except BidiError as exc:
            # A page that navigates by itself (a redirect, location.reload in a test case) cancels the navigation
            # being waited for; the page has loaded, so carry on with the readyState wait below.
            if "navigation canceled" not in str(exc) and "navigation aborted" not in str(exc):
                raise
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if self.call(wrap_script("document.readyState", True), timeout=10) == "complete":
                    break
            except BidiError:
                pass
            time.sleep(0.3)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if self.call(wrap_script("document.fonts ? document.fonts.status : 'loaded'", True), 10) == "loaded":
                    break
            except BidiError:
                break
            time.sleep(0.1)
        time.sleep(wait if wait is not None else (0.3 if test_case else 5))


def probe(
    targets: list[str],
    script: str | None = None,
    browsers: list[str] | None = None,
    wait: float | None = None,
    mobile: bool = False,
    fresh: bool = False,
    root: str | None = None,
    proxy: dict | None = None,
    steps: Steps | None = None,
    cross_origin: bool = False,
    ladybird_log: str | None = None,
) -> list[Result]:
    steps = steps or Steps()
    resolved, server = resolve_targets(targets, root, cross_origin)
    results: list[Result] = []
    open_browsers = {name: Browser(name, mobile, proxy) for name in (browsers or list(ENDPOINTS))}
    try:
        for url, test_case in resolved:
            for name, browser in open_browsers.items():
                started = time.monotonic()
                result = Result(url, name)
                offset = log_size(ladybird_log) if name == "ladybird" else 0
                try:
                    if browser.session is None or fresh:
                        browser.stop()
                        browser.start()
                    browser.load(url, test_case, wait)
                    browser.run_steps(steps)
                    result.value = browser.run_script(script, test_case)
                    if steps.screenshot_dir:
                        result.screenshot = browser.screenshot(steps.screenshot_dir, url)
                except (BidiError, OSError, KeyError) as exc:
                    result.error = str(exc)[:300]
                    browser.stop()  # a broken session is replaced for the next target
                if name == "ladybird":
                    result.log = log_lines_since(ladybird_log, offset)
                result.ms = int((time.monotonic() - started) * 1000)
                results.append(result)
    finally:
        for browser in open_browsers.values():
            browser.stop()
        if server is not None:
            server.close()
    return results


def format_results(results: list[Result], as_json: bool = False) -> str:
    if as_json:
        return json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=1)
    lines, current = [], None
    for r in results:
        if r.target != current:
            lines.append(f"=== {r.target}")
            current = r.target
        text = f"ERROR {r.error}" if r.error else ("null" if r.value is None else r.value)
        first, *rest = text.splitlines() or [""]
        lines.append(f"  {r.browser:9} {r.ms:6} ms  {first}")
        lines.extend(f"  {'':9} {'':6}     {line}" for line in rest)
        if r.screenshot:
            lines.append(f"  {'':9} {'':6}     screenshot {r.screenshot}")
        lines.extend(f"  {'':9} {'':6}     log: {line}" for line in r.log)
    return "\n".join(lines)
