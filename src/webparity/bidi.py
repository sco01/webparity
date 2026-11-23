# SPDX-License-Identifier: BSD-2-Clause
"""WebDriver BiDi for Ladybird, Chromium and Firefox, using only the standard library.

Ladybird and Chromium get a BiDi WebSocket through a classic WebDriver session created with ``webSocketUrl: true``.
Firefox speaks BiDi directly on its remote debugging port (``ws://host:port/session``) and needs ``session.new``.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class BidiError(RuntimeError):
    pass


class WebSocket:
    """A minimal RFC 6455 client: text frames, masking, fragmentation, ping/pong and close."""

    def __init__(self, url: str, timeout: float = 30):
        u = urllib.parse.urlsplit(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        if u.scheme == "wss":
            raise BidiError("wss:// is not supported; the browsers listen on plain ws:// locally")
        self.sock = socket.create_connection((u.hostname, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            data = self.sock.recv(4096)
            if not data:
                raise BidiError("connection closed during the WebSocket handshake")
            reply += data
        head, self.buffer = reply.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n")[0]
        if b" 101 " not in status:
            raise BidiError("WebSocket handshake failed: " + status.decode(errors="replace"))
        self.sock.settimeout(None)

    @staticmethod
    def encode_frame(payload: bytes, opcode: int = 0x1, mask: bytes | None = None) -> bytes:
        mask = mask if mask is not None else os.urandom(4)
        n = len(payload)
        if n < 126:
            header = bytes([0x80 | opcode, 0x80 | n])
        elif n < 65536:
            header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", n)
        else:
            header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", n)
        return header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def send(self, text: str) -> None:
        self.sock.sendall(self.encode_frame(text.encode()))

    def _read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            data = self.sock.recv(1 << 20)
            if not data:
                raise BidiError("WebSocket closed")
            self.buffer += data
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def receive(self) -> str:
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise BidiError("WebSocket closed by the browser")
            if opcode == 0x9:
                self.sock.sendall(self.encode_frame(data, opcode=0xA))
                continue
            if opcode == 0xA:
                continue
            parts.append(data)
            if b0 & 0x80:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class BidiClient:
    """Sends BiDi commands and waits for their results; keeps every event, in order."""

    def __init__(self, ws_url: str, timeout: float = 30):
        self.ws = WebSocket(ws_url, timeout=timeout)
        self._next_id = 0
        self._results: dict[int, dict] = {}
        self.events: list[dict] = []
        self._cond = threading.Condition()
        self._closed: Exception | None = None
        # Commands that got no answer: a browser that stops answering (2026-11-23: Ladybird hanging in navigate after
        # "check_if_unloading_is_canceled timed out") keeps its connection open, so this marks the session as broken
        self.timeouts = 0
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        while True:
            try:
                message = json.loads(self.ws.receive())
            except Exception as exc:  # the connection is gone; wake everyone up
                with self._cond:
                    self._closed = exc
                    self._cond.notify_all()
                return
            with self._cond:
                if "id" in message:
                    self._results[message["id"]] = message
                else:
                    self.events.append(message)
                self._cond.notify_all()

    def command(self, method: str, params: dict | None = None, timeout: float = 60) -> Any:
        with self._cond:
            self._next_id += 1
            mine = self._next_id
        self.ws.send(json.dumps({"id": mine, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        with self._cond:
            while mine not in self._results:
                if self._closed is not None:
                    raise BidiError(f"{method}: connection closed ({self._closed})")
                left = deadline - time.monotonic()
                if left <= 0:
                    self.timeouts += 1
                    raise BidiError(f"{method}: no answer in {timeout} s")
                self._cond.wait(left)
            message = self._results.pop(mine)
        if message.get("type") == "error":
            raise BidiError(f"{method}: {message.get('error')}: {message.get('message')}")
        return message.get("result")

    def close(self) -> None:
        self.ws.close()


def webdriver(base: str, method: str, path: str, body: Any = None, timeout: float = 90) -> Any:
    """One classic WebDriver HTTP call; returns its ``value``."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base.rstrip("/") + path, data, {"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response).get("value")
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc).get("value", {}).get("message", str(exc))
        except ValueError:
            message = str(exc)
        raise BidiError(f"{method} {path}: {message}") from None


class Session:
    """A browser session with its BiDi client. Use ``Session.ladybird``, ``.chromium`` or ``.firefox``."""

    def __init__(self, name: str, client: BidiClient, end, classic: tuple[str, str] | None = None):
        self.name, self.client, self._end = name, client, end
        # (WebDriver base URL, session id) for Ladybird and Chromium, for the few things only classic WebDriver does
        self.classic = classic

    def classic_call(self, method: str, path: str, body: Any = None) -> Any:
        if self.classic is None:
            raise BidiError(f"{self.name}: no classic WebDriver session")
        base, session_id = self.classic
        return webdriver(base, method, f"/session/{session_id}{path}", body)

    def end(self) -> None:
        try:
            self._end()
        finally:
            self.client.close()

    @classmethod
    def _classic(cls, name: str, base: str, capabilities: dict) -> Session:
        value = webdriver(base, "POST", "/session", {"capabilities": {"alwaysMatch": capabilities}})
        ws_url = value["capabilities"].get("webSocketUrl")
        if not ws_url:
            raise BidiError(f"{name}: the session has no webSocketUrl")
        session_id = value["sessionId"]
        return cls(
            name, BidiClient(ws_url), lambda: webdriver(base, "DELETE", f"/session/{session_id}"), (base, session_id)
        )

    @classmethod
    def ladybird(cls, base: str) -> Session:
        return cls._classic("ladybird", base, {"webSocketUrl": True})

    @classmethod
    def chromium(cls, base: str, args: list[str] | None = None) -> Session:
        options = {"args": ["--no-first-run", "--password-store=basic", *(args or [])]}
        return cls._classic(
            "chromium", base, {"browserName": "chrome", "webSocketUrl": True, "goog:chromeOptions": options}
        )

    @classmethod
    def firefox(cls, bidi_url: str) -> Session:
        client = BidiClient(bidi_url.rstrip("/") + "/session")
        client.command("session.new", {"capabilities": {}})
        return cls("firefox", client, lambda: client.command("session.end", {}))
