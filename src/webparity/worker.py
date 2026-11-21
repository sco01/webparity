# SPDX-License-Identifier: BSD-2-Clause
"""The worker: takes sites from the queue and visits them, one at a time, until stopped.

Sessions are long-lived and replaced when broken, and every ``recycle_after`` visits. The worker pauses during the
quiet hours (the nightly build and regression watch get the CPU) and while the load average is above
``worker.max_load``. A stop file (``<data>/stop-workers``) makes every worker finish its visit and wait.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import time
from datetime import datetime
from pathlib import Path

from webparity.browsers import Handle
from webparity.db import DB
from webparity.proxy import Proxy, ProxyError

log = logging.getLogger("webparity.worker")
RECYCLE_AFTER = 50


def in_quiet_hours(config: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    minute = now.hour * 60 + now.minute
    for span in config["worker"].get("quiet_hours") or []:
        start, end = (int(a) * 60 + int(b) for a, b in (part.split(":") for part in span.split("-")))
        if (start <= minute < end) if start <= end else (minute >= start or minute < end):
            return True
    return False


def overloaded(config: dict) -> bool:
    try:
        return os.getloadavg()[0] > float(config["worker"]["max_load"])
    except OSError:
        return False


class Browsers:
    """The worker's browser handles, started lazily and replaced when broken."""

    def __init__(self, config: dict, proxy: Proxy | None):
        self.config, self.proxy = config, proxy
        self.handles: dict[str, Handle] = {}
        self.visits = 0

    def get(self) -> dict[str, Handle]:
        if self.visits >= RECYCLE_AFTER:
            self.stop()
        for name in self.config["browsers"]:
            h = self.handles.get(name)
            if h is not None and h.alive():
                continue
            if h is not None:
                h.stop()
            proxy = self.proxy.chromium_settings() if (self.proxy and name == "chromium") else None
            h = Handle(name, self.config["endpoints"][name], proxy=proxy)
            for attempt in range(3):
                try:
                    h.start()
                    break
                except Exception as exc:
                    log.warning("starting %s failed (%s), attempt %d", name, exc, attempt + 1)
                    time.sleep(5 * (attempt + 1))
            else:
                raise RuntimeError(f"{name} would not start")
            self.handles[name] = h
        return self.handles

    def after_visit(self, bundle) -> None:
        self.visits += 1
        # A crashed Ladybird or a failed step leaves a session in an unknown state: start fresh next time
        for name, alive in (bundle.data.get("alive") or {}).items():
            if not alive and name in self.handles:
                self.handles.pop(name).stop()

    def stop(self) -> None:
        for h in self.handles.values():
            h.stop()
        self.handles, self.visits = {}, 0


def run(config: dict, once: bool = False, worker_name: str | None = None) -> int:
    from webparity import visit as visit_mod
    from webparity.known import Known

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker_name = worker_name or os.environ.get("WEBPARITY_WORKER") or socket.gethostname()
    db = DB.open(config)
    proxy = Proxy(config) if config["proxy"]["record"] else None
    browsers = Browsers(config, proxy)
    stop_file = Path(config["data_dir"]) / "stop-workers"
    stopping = False

    def on_signal(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    known, known_loaded = None, 0.0
    visits = 0
    while not stopping:
        if stop_file.exists() or in_quiet_hours(config) or overloaded(config):
            browsers.stop()
            if once:
                return 0
            time.sleep(30)
            continue
        db.release_stale()
        site = db.claim(worker_name)
        if site is None:
            if once:
                return 0
            time.sleep(30)
            continue
        if known is None or time.time() - known_loaded > 300:
            known, known_loaded = Known.from_config(config, db), time.time()
        started = time.time()
        try:
            handles = browsers.get()
            bundle = visit_mod.visit(site["url"], config, handles, proxy=proxy, known=known)
            browsers.after_visit(bundle)
            lb = bundle.data["browsers"].get("ladybird", {})
            status_text = "partial" if any(isinstance(v, dict) and v.get("error") for v in lb.values()) else "ok"
            db.record_visit(site["id"], bundle, worker_name, status_text, time.time() - started)
            errors = [
                f["signature"] for f in bundle.data["findings"] if f["category"] == "error" and not f.get("known")
            ]
            blocked = any(bundle.m(n, "probe", "blocked") for n in bundle.data["browsers"] if n != "ladybird")
            status = "candidate" if errors else ("blocked" if blocked else "clean")
            db.finish_site(site["id"], status, bundle.id)
            log.info("%s %.0f s %s", site["domain"], time.time() - started, ", ".join(errors[:4]) or "clean")
        except (ProxyError, RuntimeError, OSError) as exc:
            log.error("%s failed: %s", site["domain"], exc)
            browsers.stop()
            db.finish_site(site["id"], "failed" if site["attempts"] >= 3 else "queued", None)
            time.sleep(10)
        visits += 1
        if once:
            break
    browsers.stop()
    return 0
