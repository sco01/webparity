# SPDX-License-Identifier: BSD-2-Clause
"""One visit: the declared steps, run in every browser in parallel, written to a visit bundle.

The steps are named in ``config.visit.steps`` and implemented in ``webparity.measure``. Each step runs in all browsers
at once; its measurements go into ``visit.json`` under the browser, and the BiDi events that arrived during the step
are written to ``events.jsonl`` tagged with the step, the browser and the frame (``top`` is true for the top-level
document, false for iframes). While the visit runs, the proxy records every exchange into ``recording.warc.gz``.

The detectors run on the finished bundle (``webparity.detectors.analyze``); the recording is kept only if the visit
has error findings (``proxy.keep: findings``), or always (``all``), or never (``none``).
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

from webparity import measure
from webparity.bundle import Bundle
from webparity.detectors import analyze
from webparity.proxy import Proxy, ProxyError


class StepContext:
    """What a step function gets besides the browser handle."""

    def __init__(self, bundle: Bundle, browser: str, config: dict, url: str, shared: dict):
        self.bundle, self.browser, self.config, self.url = bundle, browser, config, url
        # Shared between browsers within one visit (for example the reference's measurements, once known)
        self.shared = shared

    def save(self, name: str, content: bytes | str) -> None:
        self.bundle.write_file(f"{self.browser}-{name}", content)

    def measurements(self, browser: str | None = None) -> dict:
        return self.bundle.browser(browser or self.browser)


def _parallel(jobs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def run(name, fn):
        try:
            out[name] = fn()
        except Exception as exc:  # a failed step in one browser is recorded, not raised
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:300]}

    threads = [threading.Thread(target=run, args=(n, f), daemon=True) for n, f in jobs.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def compact_event(event: dict, browser: str, step: str, top_context: str | None) -> dict:
    method, params = event.get("method", ""), event.get("params", {}) or {}
    out: dict[str, Any] = {"t": round(time.time(), 3), "browser": browser, "step": step, "method": method}
    if method == "log.entryAdded":
        context = (params.get("source") or {}).get("context")
        out.update(
            {
                "context": context,
                "top": context == top_context,
                "type": params.get("type"),
                "level": params.get("level"),
                "text": (params.get("text") or "")[:2000],
            }
        )
        frames = (params.get("stackTrace") or {}).get("callFrames") or []
        if frames:
            out["stack"] = [
                f"{f.get('functionName') or '?'} {f.get('url', '')[:200]}:{f.get('lineNumber')}:{f.get('columnNumber')}"
                for f in frames[:8]
            ]
    elif method.startswith("network."):
        context = params.get("context")
        request = params.get("request") or {}
        response = params.get("response") or {}
        out.update(
            {
                "context": context,
                "top": context == top_context,
                "url": (request.get("url") or "")[:500],
                "request_method": request.get("method"),
                "status": response.get("status"),
                "error": params.get("errorText"),
            }
        )
    else:
        context = params.get("context")
        out.update({"context": context, "top": context == top_context, "url": (params.get("url") or "")[:500]})
        if params.get("parent"):
            out["parent"] = params["parent"]
    return out


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _crash_reports(profile: str) -> set[str]:
    root = Path(profile) / "data" / "CrashReports"
    return {str(p) for p in root.glob("*.txt")} if root.exists() else set()


def build_info(config: dict) -> dict:
    """The build under test: a JSON file ({"commit": ..., "date": ...}) or a text file "<commit> <date> <time>"."""
    import json

    p = Path(config["ladybird"]["build_info"])
    try:
        text = p.read_text().strip()
    except OSError:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        commit, _, date = text.partition(" ")
        return {"commit": commit, "date": date}


def visit(
    url: str,
    config: dict,
    handles: dict[str, Any],
    proxy: Proxy | None = None,
    known=None,
    visit_id: str | None = None,
    steps: list[str] | None = None,
) -> Bundle:
    started = time.time()
    bundle = Bundle.create(config["data_dir"], url, visit_id)
    bundle.data.update(
        {
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "worker": socket.gethostname(),
            "build": build_info(config),
            "config": {"deep": config["visit"]["deep"], "browsers": list(handles)},
        }
    )
    steps = steps or list(config["visit"]["steps"])
    if not config["visit"]["deep"]:
        steps = [s for s in steps if s not in ("interact", "reload")]
    log_path, profile = config["ladybird"]["log"], config["ladybird"]["profile"]
    log_start = _file_size(log_path)
    reports_before = _crash_reports(profile)
    recording = False
    if proxy is not None:
        try:
            proxy.record(bundle.id)
            recording = True
        except ProxyError as exc:
            bundle.data["proxy_error"] = str(exc)
    shared: dict = {}
    timings: dict[str, float] = {}
    try:
        for step in steps:
            fn = measure.STEPS.get(step)
            if fn is None:
                continue
            if step == "reload":
                bundle.data["log_reload_offset"] = _file_size(log_path) - log_start
            active = {n: h for n, h in handles.items() if measure.should_run(step, n, bundle)}
            positions = {n: h.event_pos() for n, h in active.items()}
            t0 = time.time()
            results = _parallel(
                {
                    n: (lambda n=n, h=h, fn=fn: fn(h, StepContext(bundle, n, config, url, shared)))
                    for n, h in active.items()
                }
            )
            timings[step] = round(time.time() - t0, 1)
            for n, result in results.items():
                target = bundle.browser(n)
                if isinstance(result, dict) and result.get("_merge"):
                    result.pop("_merge")
                    target.update(result)
                else:
                    target[step] = result
                h = active[n]
                bundle.add_events([compact_event(e, n, step, h.context) for e in h.events_since(positions[n])])
                bundle.data["steps"].setdefault(step, {})[n] = timings[step]
    finally:
        if recording:
            try:
                proxy.passthrough()
            except ProxyError as exc:
                bundle.data["proxy_error"] = str(exc)
            bundle.move_in(proxy.recording_path(bundle.id), "recording.warc.gz")
    # Ladybird's log for this visit, and any new crash reports
    if "ladybird" in handles and os.path.exists(log_path):
        with open(log_path, "rb") as f:
            f.seek(log_start)
            bundle.write_file("ladybird.log", f.read(20_000_000))
    for i, report in enumerate(sorted(_crash_reports(profile) - reports_before)[:5]):
        try:
            bundle.write_file(f"crash-report-{i + 1}.txt", Path(report).read_bytes())
        except OSError:
            pass
    bundle.data["alive"] = {n: h.alive() for n, h in handles.items()}
    timings["measure"] = round(time.time() - started, 1)
    t0 = time.time()
    findings = analyze(bundle, known=known)
    timings["analysis"] = round(time.time() - t0, 1)
    timings["total"] = round(time.time() - started, 1)
    bundle.data["timings"] = timings
    bundle.data["findings"] = [f.to_dict() for f in findings]
    keep = config["proxy"].get("keep", "findings")
    has_errors = any(f.category == "error" and not f.known for f in findings)
    if keep == "none" or (keep == "findings" and not has_errors):
        bundle.delete("recording.warc.gz")
    bundle.save()
    return bundle


def reanalyze(bundle: Bundle, known=None, only: list[str] | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """Runs the detectors again; returns (gone, new, all) findings compared with what the bundle had."""
    before = {(f["signature"], f["category"]) for f in bundle.data.get("findings", [])}
    findings = [f.to_dict() for f in analyze(bundle, only=only, known=known)]
    after = {(f["signature"], f["category"]) for f in findings}
    gone = [f for f in bundle.data.get("findings", []) if (f["signature"], f["category"]) not in after]
    new = [f for f in findings if (f["signature"], f["category"]) not in before]
    return gone, new, findings
