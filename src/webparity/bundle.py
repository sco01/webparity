# SPDX-License-Identifier: BSD-2-Clause
"""The visit bundle: everything one visit produced, in one directory.

    <data>/visits/<YYYY-MM-DD>/<visit id>/
        visit.json           the visit: URL, steps, timings, per-browser measurements, findings
        events.jsonl         console entries, script errors and navigation events, tagged with step, browser, frame
        <browser>-<name>.png screenshots (load, idle, reload, mobile)
        <browser>-dom.html.gz, <browser>-layout.json.gz, <browser>-layout-reload.json.gz,
        <browser>-layout-mobile.json.gz
        ladybird.log         Ladybird's WebDriver log for the visit
        crash-report-N.txt   Ladybird's own crash reports
        recording.warc.gz    every request and response, when recording was on and the visit was kept

``visit.json`` keys: ``id``, ``url``, ``site``, ``started``, ``build``, ``browsers`` (name → measurements),
``steps`` (name → {browser → seconds}), ``timings``, ``findings`` (list of Finding dicts), ``schema`` (1).
Per-browser measurements are what the steps in ``webparity.measure`` return, keyed by step: ``load``, ``probe``,
``idle``, ``scroll``, ``wheel``, ``mobile``, ``menu``, ``reload``, plus ``log`` (parsed log) for Ladybird.
"""

from __future__ import annotations

import gzip
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = 1


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


class Bundle:
    def __init__(self, path: Path, data: dict | None = None):
        self.path = Path(path)
        self.data: dict[str, Any] = data if data is not None else {}

    # --- creating -------------------------------------------------------------------------------------------------
    @classmethod
    def create(cls, data_dir: str | Path, url: str, visit_id: str | None = None) -> Bundle:
        visit_id = visit_id or new_id()
        day = f"{visit_id[0:4]}-{visit_id[4:6]}-{visit_id[6:8]}"
        path = Path(data_dir) / "visits" / day / visit_id
        path.mkdir(parents=True, exist_ok=True)
        return cls(path, {"schema": SCHEMA, "id": visit_id, "url": url, "browsers": {}, "steps": {}, "findings": []})

    @property
    def id(self) -> str:
        return self.data["id"]

    def browser(self, name: str) -> dict:
        return self.data["browsers"].setdefault(name, {})

    def write_file(self, name: str, content: bytes | str) -> None:
        if isinstance(content, str):
            content = content.encode("utf-8", "replace")
        if name.endswith(".gz") and not content[:2] == b"\x1f\x8b":
            content = gzip.compress(content)
        (self.path / name).write_bytes(content)

    def write_json(self, name: str, obj: Any) -> None:
        self.write_file(name, json.dumps(obj, ensure_ascii=False).encode("utf-8", "replace"))

    def add_events(self, events: list[dict]) -> None:
        with open(self.path / "events.jsonl", "a", encoding="utf-8", errors="replace") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def save(self) -> None:
        # errors="replace": a page text cut in the middle of a surrogate pair (an emoji) leaves a lone surrogate,
        # which UTF-8 can't encode (2026-11-23: the worker died on every visit of one page and took it again)
        tmp = self.path / "visit.json.tmp"
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8", errors="replace")
        tmp.replace(self.path / "visit.json")

    def move_in(self, source: Path, name: str) -> None:
        if source.exists():
            shutil.move(str(source), self.path / name)

    def delete(self, name: str) -> None:
        (self.path / name).unlink(missing_ok=True)

    # --- reading ----------------------------------------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> Bundle:
        path = Path(path)
        if path.is_file():
            path = path.parent
        return cls(path, json.loads((path / "visit.json").read_text(encoding="utf-8")))

    def has(self, name: str) -> bool:
        return (self.path / name).exists()

    def read_file(self, name: str) -> bytes | None:
        p = self.path / name
        if not p.exists():
            return None
        content = p.read_bytes()
        return gzip.decompress(content) if name.endswith(".gz") else content

    def read_json(self, name: str) -> Any:
        content = self.read_file(name)
        return json.loads(content) if content is not None else None

    def events(self) -> list[dict]:
        p = self.path / "events.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def m(self, browser: str, step: str, key: str | None = None, default: Any = None) -> Any:
        """A measurement: ``bundle.m("ladybird", "probe", "elements")``."""
        value = self.data.get("browsers", {}).get(browser, {}).get(step)
        if key is None:
            return value if value is not None else default
        if not isinstance(value, dict):
            return default
        return value.get(key, default)


def iter_bundles(data_dir: str | Path, since: str | None = None):
    """Every bundle under ``data_dir/visits``, oldest first; ``since`` is a day (YYYY-MM-DD)."""
    root = Path(data_dir) / "visits"
    if not root.exists():
        return
    for day in sorted(root.iterdir()):
        if since and day.name < since:
            continue
        for visit in sorted(day.iterdir()):
            if (visit / "visit.json").exists():
                yield Bundle.load(visit)
