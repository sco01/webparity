# SPDX-License-Identifier: BSD-2-Clause
"""Browser handles: one browser session with what the probe and the visit need.

Each handle knows its top-level browsing context, keeps every BiDi event (so steps can take the events since a
position), and can run scripts, take screenshots, resize the viewport and send input. With a proxy, Chromium gets
``--proxy-server`` and the proxy CA's SPKI hash; Ladybird and Firefox are configured by their containers.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from webparity.bidi import BidiError, Session

# The same Chromium flags as the explorer: no scrollbars (the same layout width as Ladybird) and WebGL in software.
# HttpsUpgrades off (2026-11-22): Chromium upgraded http:// pages to https, then blocked their http:// stylesheets as
# mixed content (orebroastronomi.se came out unstyled), and reached sites that Ladybird and Firefox, which stay on
# http, did not (cordate.ryttargarden.se) - all three browsers now load the address as given
CHROMIUM_ARGS = [
    "--hide-scrollbars",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--disable-features=HttpsUpgrades",
]
DESKTOP = (1440, 900)
MOBILE = (390, 844)
EVENTS = [
    "log.entryAdded",
    "browsingContext.contextCreated",
    "browsingContext.navigationStarted",
    "browsingContext.load",
    "network.responseCompleted",
    "network.fetchError",
]


def wrap_script(code: str, as_expression: bool) -> str:
    """A function declaration for ``script.callFunction`` that returns a string, a JSON text or null."""
    body = f"return ({code.strip().rstrip(';')}\n);" if as_expression else code
    return (
        "async function () {\n"
        f"  const value = await (async () => {{\n{body}\n}}).apply(this, arguments);\n"
        "  if (value === undefined || value === null) return null;\n"
        "  return typeof value === 'string' ? value : JSON.stringify(value);\n"
        "}"
    )


def _to_local(value: Any) -> dict:
    """A Python value as a BiDi LocalValue (for script arguments)."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, (int, float)):
        return {"type": "number", "value": value}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, (list, tuple)):
        return {"type": "array", "value": [_to_local(v) for v in value]}
    if isinstance(value, dict):
        return {"type": "object", "value": [[str(k), _to_local(v)] for k, v in value.items()]}
    raise TypeError(f"can't pass {type(value)} to a script")


class Handle:
    def __init__(self, name: str, endpoint: str, mobile: bool = False, proxy: dict | None = None):
        self.name, self.endpoint, self.mobile = name, endpoint, mobile
        # {"address": "127.0.0.1:8790", "spki": "..."} when Chromium should go through the recording proxy
        self.proxy = proxy
        self.session: Session | None = None
        self.context: str | None = None
        self.viewport = MOBILE if mobile else DESKTOP

    # --- lifecycle ------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self.name == "ladybird":
            self.session = Session.ladybird(self.endpoint)
        elif self.name == "chromium":
            args = [*CHROMIUM_ARGS, f"--window-size={self.viewport[0]},{self.viewport[1]}"]
            if self.proxy:
                args.append(f"--proxy-server={self.proxy['address']}")
                if self.proxy.get("spki"):
                    args.append(f"--ignore-certificate-errors-spki-list={self.proxy['spki']}")
            self.session = Session.chromium(self.endpoint, args)
        elif self.name == "firefox":
            self.session = Session.firefox(self.endpoint)
        else:
            raise ValueError(f"unknown browser {self.name}")
        self.context = self.session.client.command("browsingContext.getTree", {})["contexts"][0]["context"]
        try:
            self.session.client.command("session.subscribe", {"events": EVENTS})
        except BidiError:
            pass
        self.set_viewport(*self.viewport)

    def stop(self) -> None:
        if self.session is not None:
            try:
                self.session.end()
            except (BidiError, OSError):
                pass
            self.session = None

    def alive(self) -> bool:
        """The connection is open and every command got an answer; otherwise the worker starts a fresh session."""
        client = self.session.client if self.session is not None else None
        return client is not None and client._closed is None and not getattr(client, "timeouts", 0)

    # --- events ---------------------------------------------------------------------------------------------------
    def event_pos(self) -> int:
        return len(self.session.client.events) if self.session else 0

    def events_since(self, pos: int) -> list[dict]:
        return list(self.session.client.events[pos:]) if self.session else []

    # --- commands -------------------------------------------------------------------------------------------------
    def set_viewport(self, width: int, height: int) -> None:
        self.viewport = (width, height)
        try:
            self.session.client.command(
                "browsingContext.setViewport", {"context": self.context, "viewport": {"width": width, "height": height}}
            )
        except BidiError:
            if self.session.classic is not None:  # Ladybird without setViewport: resize the window instead
                self.session.classic_call("POST", "/window/rect", {"width": width, "height": height})

    def navigate(self, url: str, timeout: float = 45) -> str | None:
        """Navigates and waits for load; returns an error text, or None. A cancelled navigation counts as loaded."""
        try:
            self.session.client.command(
                "browsingContext.navigate", {"context": self.context, "url": url, "wait": "complete"}, timeout=timeout
            )
            return None
        except BidiError as exc:
            text = str(exc)
            if "navigation canceled" in text or "navigation aborted" in text:
                return None
            return text[:300]

    def call(self, function: str, args: list | None = None, timeout: float = 30) -> Any:
        """Calls a function declaration in the top document; returns its JSON value (strings are parsed if JSON)."""
        params = {"functionDeclaration": function, "target": {"context": self.context}, "awaitPromise": True}
        if args:
            params["arguments"] = [_to_local(a) for a in args]
        result = self.session.client.command("script.callFunction", params, timeout=timeout)
        if result.get("type") == "exception":
            details = result.get("exceptionDetails", {})
            raise BidiError(f"script exception: {details.get('text', details)}")
        value = result.get("result", {})
        return value.get("value") if value.get("type") not in ("null", "undefined") else None

    def js(self, body: str, *args: Any, timeout: float = 30) -> Any:
        """Runs statements with their own ``return``, like classic WebDriver's execute; JSON comes back parsed."""
        import json

        function = (
            "async function () {\n"
            f"  const value = await (async () => {{\n{body}\n}}).apply(this, arguments);\n"
            "  return value === undefined ? 'null' : JSON.stringify(value);\n"
            "}"
        )
        raw = self.call(function, list(args) or None, timeout=timeout)
        return json.loads(raw) if isinstance(raw, str) else raw

    def screenshot(self) -> bytes:
        data = self.session.client.command("browsingContext.captureScreenshot", {"context": self.context}, timeout=30)
        return base64.b64decode(data["data"])

    def actions(self, actions: list[dict]) -> None:
        self.session.client.command("input.performActions", {"context": self.context, "actions": actions}, timeout=30)
        try:
            self.session.client.command("input.releaseActions", {"context": self.context}, timeout=10)
        except BidiError:
            pass

    def wheel(self, x: int, y: int, delta_y: int, steps: int = 1) -> None:
        self.actions(
            [
                {
                    "type": "wheel",
                    "id": "wheel",
                    "actions": [{"type": "scroll", "x": x, "y": y, "deltaX": 0, "deltaY": delta_y}] * steps,
                }
            ]
        )

    def click_at(self, x: int, y: int) -> None:
        self.actions(
            [
                {
                    "type": "pointer",
                    "id": "mouse",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [
                        {"type": "pointerMove", "x": int(x), "y": int(y)},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        )

    def wait_ready(self, timeout: float = 20) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.js("return document.readyState", timeout=10) == "complete":
                    return True
            except BidiError:
                pass
            time.sleep(0.3)
        return False
