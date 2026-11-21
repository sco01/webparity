# SPDX-License-Identifier: BSD-2-Clause
"""Client for the recording proxy's control port (see images/proxy/webparity_proxy.py)."""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class ProxyError(RuntimeError):
    pass


class Proxy:
    def __init__(self, config: dict):
        self.control = config["proxy"]["control"].rstrip("/")
        self.address = config["proxy"]["address"]
        self.recordings = Path(config["data_dir"]) / "recordings"
        self.ca_dir = Path(config["proxy"].get("ca_dir", "/etc/webparity"))

    def _call(self, method: str, path: str) -> dict:
        request = urllib.request.Request(
            self.control + path,
            method=method,
            data=b"" if method == "POST" else None,
            headers={"X-Webparity-Control": "1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                message = json.load(exc).get("error")
            except ValueError:
                message = str(exc)
            raise ProxyError(f"{path}: {message}") from None
        except OSError as exc:
            raise ProxyError(f"proxy control unreachable: {exc}") from None

    def status(self) -> dict:
        return self._call("GET", "/status")

    def spki(self) -> str | None:
        p = self.ca_dir / "proxy-ca.spki"
        return p.read_text().strip() if p.exists() else None

    def chromium_settings(self) -> dict:
        return {"address": self.address, "spki": self.spki()}

    def record(self, name: str) -> dict:
        return self._call("POST", "/record?name=" + urllib.parse.quote(name))

    def replay(self, name: str) -> dict:
        return self._call("POST", "/replay?name=" + urllib.parse.quote(name))

    def passthrough(self) -> dict:
        return self._call("POST", "/pass")

    def recording_path(self, name: str) -> Path:
        return self.recordings / f"{name}.warc.gz"

    def stage_for_replay(self, warc: Path, name: str) -> None:
        """Copies a recording (from a bundle) to where the proxy reads recordings."""
        self.recordings.mkdir(parents=True, exist_ok=True)
        target = self.recording_path(name)
        if Path(warc).resolve() != target.resolve():
            shutil.copyfile(warc, target)
