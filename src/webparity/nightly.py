# SPDX-License-Identifier: BSD-2-Clause
"""The nightly chain, run once a night after the Ladybird build (``webparity nightly``).

Steps, each recorded in the run summary and never stopping the chain unless the build is unusable:

1. ``build``: the build under test (``ladybird.build_info``); optionally run ``nightly.build_command`` first.
2. ``canary``: visit the canary pages (``webparity.canary``: our own pages that misbehave only in Ladybird), each with
   the signature it must produce. A missing signature means a detector or the collection broke.
3. ``regression``: visit the regression sites (shallow), and report error signatures that are new since the last run.
4. ``verify``: run filed issues' test cases against the new build.
5. ``watch``: refresh filed issues from GitHub (anonymous, read-only).
6. ``retire``: candidates whose signature no longer appears in any site's latest visit become ``gone``; sites that
   failed three times become ``dead``.
7. ``requeue``: candidate sites last visited more than ``nightly.recheck_days`` ago are queued again (new build).
8. ``index``: write INDEX.md from the database.

The workers pause during the quiet hours, so the chain gets the CPU.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import traceback

from webparity.db import DB

log = logging.getLogger("webparity.nightly")

DEFAULTS = {
    "build_command": "",
    "canary": [],  # extra pages besides the built-in ones: [{"url": ..., "expect": ["signature-prefix", ...]}]
    "regression_sites": [],
    "recheck_days": 7,
}


def _settings(config: dict) -> dict:
    return {**DEFAULTS, **(config.get("nightly") or {})}


def step_build(config, db, s):
    if s["build_command"]:
        r = subprocess.run(s["build_command"], shell=True, capture_output=True, text=True, timeout=4 * 3600)
        if r.returncode != 0:
            raise RuntimeError(f"build failed: {(r.stderr or r.stdout)[-300:]}")
    from webparity.visit import build_info

    return build_info(config) or {"note": "no build info file"}


def _visit_list(config, entries, deep: bool):
    from webparity.browsers import Handle
    from webparity.known import Known
    from webparity.proxy import Proxy
    from webparity.visit import visit

    cfg = json.loads(json.dumps(config))
    cfg["visit"]["deep"] = deep
    proxy = Proxy(cfg) if cfg["proxy"]["record"] else None
    handles = {}
    for name in cfg["browsers"]:
        h = Handle(
            name, cfg["endpoints"][name], proxy=proxy.chromium_settings() if proxy and name == "chromium" else None
        )
        h.start()
        handles[name] = h
    known = Known.from_config(cfg)
    try:
        for entry in entries:
            url = entry["url"] if isinstance(entry, dict) else entry
            yield entry, visit(url, cfg, handles, proxy=proxy, known=known)
            for h in list(handles.values()):
                if not h.alive():
                    h.stop()
                    h.start()
    finally:
        for h in handles.values():
            h.stop()


def step_canary(config, db, s):
    """The built-in canary pages (webparity.canary), plus any configured ones."""
    from webparity import canary

    base, server = canary.start_server()
    entries = canary.entries(base) + list(s["canary"] or [])
    cfg = json.loads(json.dumps(config))
    cfg["proxy"]["keep"] = "none"
    failed, noisy = {}, {}
    try:
        for entry, bundle in _visit_list(cfg, entries, deep=True):
            errors = [f["signature"] for f in bundle.data["findings"] if f["category"] == "error"]
            everything = [f["signature"] for f in bundle.data["findings"]]
            expect = entry.get("expect", [])
            if not expect and errors:  # the clean page must give no error finding
                noisy[entry.get("name", entry["url"])] = errors
            lost = [e for e in expect if not any(sig.startswith(e) for sig in everything)]
            if lost:
                failed[entry.get("name", entry["url"])] = {"expected": lost, "got": everything[:8]}
    finally:
        server.shutdown()
    result = {"pages": len(entries), "failed": failed, "noisy": noisy}
    if failed or noisy:
        raise RuntimeError(
            f"canary: {len(failed)} detectors silent, {len(noisy)} clean pages noisy: {json.dumps(result)[:600]}"
        )
    return result


def step_regression(config, db, s):
    if not s["regression_sites"]:
        return {"skipped": "no regression sites configured"}
    previous = db.one("SELECT summary FROM runs WHERE kind = 'nightly' AND status = 'ok' ORDER BY id DESC LIMIT 1")
    before = {}
    if previous and previous["summary"]:
        before = (json.loads(previous["summary"]).get("regression") or {}).get("signatures", {})
    now_sigs, new = {}, {}
    for entry, bundle in _visit_list(config, s["regression_sites"], deep=False):
        url = entry["url"] if isinstance(entry, dict) else entry
        sigs = sorted(
            {f["signature"] for f in bundle.data["findings"] if f["category"] == "error" and not f.get("known")}
        )
        now_sigs[url] = sigs
        added = sorted(set(sigs) - set(before.get(url, sigs)))
        if added:
            new[url] = added
    return {"sites": len(now_sigs), "new": new, "signatures": now_sigs}


def step_verify(config, db, s):
    from webparity.issues.verify import verify

    results = verify(config, db)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.get("result", "?")] = counts.get(r.get("result", "?"), 0) + 1
    return {
        "issues": len(results),
        "results": counts,
        "fixed": [r.get("number") for r in results if r.get("result") == "fixed"],
    }


def step_watch(config, db, s):
    from webparity.issues.github import watch

    changes = watch(config, db)
    return {"changed": [c for c in changes if c.get("number")], "errors": [c for c in changes if not c.get("number")]}


def step_retire(config, db, s):
    gone = 0
    for r in db.q("SELECT signature FROM candidates WHERE status IN ('new', 'investigating', 'filed')"):
        still = db.one(
            "SELECT 1 FROM findings f JOIN sites s ON s.last_visit = f.visit WHERE f.signature = ? LIMIT 1",
            (r["signature"],),
        )
        if still is None:
            db.x(
                "UPDATE candidates SET status = 'gone', note = COALESCE(note || ' · ', '') || ? WHERE signature = ?",
                (f"gone {time.strftime('%Y-%m-%d')}", r["signature"]),
            )
            gone += 1
    dead = db.x("UPDATE sites SET status = 'dead' WHERE status = 'failed' AND attempts >= 3").rowcount
    return {"candidates_gone": gone, "sites_dead": dead}


def step_requeue(config, db, s):
    n = db.requeue(
        "status = 'candidate' AND (visited IS NULL OR visited < datetime('now', 'localtime', ?))",
        (f"-{int(s['recheck_days'])} days",),
        priority=70,
    )
    failed = db.x("UPDATE sites SET status = 'queued' WHERE status = 'failed' AND attempts < 3").rowcount
    return {"candidates_requeued": n, "failed_requeued": failed}


def step_index(config, db, s):
    from pathlib import Path

    from webparity.issues.index import generate_index

    generate_index(config, db, write=True)
    return {"path": str(Path(config["data_dir"]) / "INDEX.md")}


STEPS = [
    ("build", step_build, True),
    ("canary", step_canary, False),
    ("regression", step_regression, False),
    ("verify", step_verify, False),
    ("watch", step_watch, False),
    ("retire", step_retire, False),
    ("requeue", step_requeue, False),
    ("index", step_index, False),
]


def run(config: dict, only: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = DB.open(config)
    s = _settings(config)
    # The chain uses the same browsers as the workers: ask them to stop, and wait for running visits to finish
    from pathlib import Path

    stop_file = Path(config["data_dir"]) / "stop-workers"
    created = not stop_file.exists()
    stop_file.touch()
    deadline = time.time() + 600
    while time.time() < deadline and db.one("SELECT 1 FROM sites WHERE status = 'visiting' LIMIT 1"):
        time.sleep(10)
    time.sleep(35)  # a worker checks the stop file between visits and then closes its sessions
    try:
        return _run_steps(config, db, s, only)
    finally:
        if created:
            stop_file.unlink(missing_ok=True)


def _run_steps(config: dict, db: DB, s: dict, only: list[str] | None) -> int:
    run_id = db.start_run("nightly")
    summary: dict = {}
    failed = []
    for name, fn, fatal in STEPS:
        if only and name not in only:
            continue
        t0 = time.time()
        try:
            summary[name] = fn(config, db, s)
            log.info("%s: %s", name, json.dumps(summary[name], ensure_ascii=False)[:300])
        except Exception as exc:
            failed.append(name)
            summary[name] = {"error": str(exc)[:500], "traceback": traceback.format_exc()[-1500:]}
            log.error("%s failed: %s", name, exc)
            if fatal:
                break
        summary[name]["seconds"] = round(time.time() - t0, 1) if isinstance(summary[name], dict) else None
    status = "ok" if not failed else "failed"
    summary["failed_steps"] = failed
    db.finish_run(run_id, status, summary)
    print(f"nightly: {status}" + (f" ({', '.join(failed)})" if failed else ""))
    return 0 if status == "ok" else 1
