# SPDX-License-Identifier: BSD-2-Clause
"""The webparity command line."""

from __future__ import annotations

import argparse
import json
import sys

from webparity import __version__


def _config(args):
    from webparity import config

    return config.load(getattr(args, "config", None))


def _db(cfg):
    from webparity.db import DB

    return DB.open(cfg)


# --- collection -----------------------------------------------------------------------------------------------------
def cmd_probe(args) -> int:
    from webparity.probe import format_results, probe

    script = open(args.js_file, encoding="utf-8").read() if args.js_file else args.js
    proxy = None
    if args.record or args.replay:
        from webparity.proxy import Proxy

        proxy = Proxy(_config(args))
        proxy.record(args.record) if args.record else proxy.replay(args.replay)
    try:
        results = probe(
            args.targets,
            script=script,
            browsers=[b.strip() for b in args.browsers.split(",") if b.strip()],
            wait=args.wait,
            mobile=args.mobile,
            fresh=args.fresh,
            root=args.root,
            proxy=proxy.chromium_settings() if proxy else None,
        )
    finally:
        if proxy is not None:
            status = proxy.status()
            proxy.passthrough()
            if args.replay and status.get("missing"):
                print(f"not in the recording: {len(status['missing'])} requests", file=sys.stderr)
    print(format_results(results, as_json=args.json))
    return 1 if any(r.error for r in results) else 0


def _handles(cfg, browsers, proxy):
    from webparity.browsers import Handle

    handles = {}
    for name in browsers:
        h = Handle(
            name, cfg["endpoints"][name], proxy=proxy.chromium_settings() if proxy and name == "chromium" else None
        )
        h.start()
        handles[name] = h
    return handles


def _print_findings(bundle) -> None:
    for f in bundle.data["findings"]:
        mark = "  " if f["category"] == "info" else ("K " if f.get("known") else "! ")
        print(f"  {mark}{f['signature']:50} {f['text'][:100]}" + (f"  [{f['known']}]" if f.get("known") else ""))


def cmd_visit(args) -> int:
    from webparity.known import Known
    from webparity.proxy import Proxy
    from webparity.visit import visit

    cfg = _config(args)
    if args.shallow:
        cfg["visit"]["deep"] = False
    if args.keep:
        cfg["proxy"]["keep"] = args.keep
    browsers = [b.strip() for b in (args.browsers or ",".join(cfg["browsers"])).split(",") if b.strip()]
    proxy = None if args.no_record or not cfg["proxy"]["record"] else Proxy(cfg)
    handles = _handles(cfg, browsers, proxy)
    known = Known.from_config(cfg)
    try:
        for url in args.urls:
            bundle = visit(url, cfg, handles, proxy=proxy, known=known)
            print(f"{url}  {bundle.data['timings'].get('total')} s  {bundle.path}")
            _print_findings(bundle)
            if args.record_db:
                from webparity.db import DB

                db = DB.open(cfg)
                db.add_site(url, source="cli")
                site = db.one(
                    "SELECT id FROM sites WHERE domain = ?", (__import__("webparity.sites").sites.domain_of(url),)
                )
                db.record_visit(site["id"], bundle, "cli", "ok", bundle.data["timings"].get("total") or 0)
    finally:
        for h in handles.values():
            h.stop()
    return 0


def dom_summary(h) -> dict:
    return h.js(
        "return {title: document.title, elements: document.querySelectorAll('*').length,"
        " text: document.body ? document.body.innerText.length : 0,"
        " images: Array.from(document.images).filter(i => i.complete && i.naturalWidth > 0).length,"
        " height: document.documentElement.scrollHeight}"
    )


def cmd_replay(args) -> int:
    """Replays a recorded visit offline and compares the DOM with the recorded visit (phase 1 done criterion)."""
    from pathlib import Path

    from webparity.bundle import Bundle
    from webparity.measure import wait_stable
    from webparity.proxy import Proxy

    cfg = _config(args)
    path = Path(args.visit)
    if not path.exists():
        matches = list((Path(cfg["data_dir"]) / "visits").glob(f"*/{args.visit}"))
        if not matches:
            print(f"no visit {args.visit}", file=sys.stderr)
            return 2
        path = matches[0]
    bundle = Bundle.load(path)
    if not bundle.has("recording.warc.gz"):
        print("the visit has no recording", file=sys.stderr)
        return 2
    proxy = Proxy(cfg)
    name = f"replay-{bundle.id}"
    proxy.stage_for_replay(bundle.path / "recording.warc.gz", name)
    status = proxy.replay(name)
    print(f"replaying {bundle.data['url']} from {status['records']} recorded responses, offline")
    browsers = [b.strip() for b in (args.browsers or ",".join(bundle.data["browsers"])).split(",") if b.strip()]
    handles = _handles(cfg, browsers, proxy)
    rows, ok = [], True
    try:
        for name_b, h in handles.items():
            h.navigate(bundle.data["url"])
            wait_stable(h, cfg["visit"]["stable_timeout"])
            now = dom_summary(h)
            then = bundle.m(name_b, "probe") or {}
            rows.append((name_b, then, now))
            if args.screenshot:
                bundle.write_file(f"{name_b}-replay.png", h.screenshot())
    finally:
        missing = proxy.status().get("missing", [])
        proxy.passthrough()
        proxy.recording_path(name).unlink(missing_ok=True)
        for h in handles.values():
            h.stop()
    for name_b, then, now in rows:
        same = then.get("elements") and abs(now["elements"] - then["elements"]) <= max(5, 0.02 * then["elements"])
        same = bool(same) and now["title"] == then.get("title", now["title"])
        ok &= same
        print(
            f"  {name_b:9} recorded: {then.get('elements')} elements, {then.get('text')} chars,"
            f" title {then.get('title')!r}"
            f"\n  {'':9} replayed: {now['elements']} elements, {now['text']} chars, title {now['title']!r}"
            f"  {'SAME' if same else 'DIFFERENT'}"
        )
    print(f"  requests not in the recording: {len(missing)}")
    for m in missing[: args.show_missing]:
        print(f"    {m}")
    return 0 if ok else 1


# --- analysis -------------------------------------------------------------------------------------------------------
def cmd_reanalyze(args) -> int:
    from collections import Counter

    from webparity.bundle import Bundle, iter_bundles
    from webparity.known import Known
    from webparity.visit import reanalyze

    cfg = _config(args)
    known = Known.from_config(cfg)
    bundles = [Bundle.load(p) for p in args.bundles] if args.bundles else iter_bundles(cfg["data_dir"], args.since)
    only = args.detector.split(",") if args.detector else None
    gone_total, new_total, n = Counter(), Counter(), 0
    for bundle in bundles:
        gone, new, findings = reanalyze(bundle, known=known, only=only)
        n += 1
        for f in gone:
            if f["category"] == "error":
                gone_total[f["signature"]] += 1
        for f in new:
            if f["category"] == "error":
                new_total[f["signature"]] += 1
        if args.verbose and (gone or new):
            print(f"{bundle.data['url']}  -{len(gone)} +{len(new)}")
        if args.write:
            if only:
                findings = [f for f in bundle.data["findings"] if f.get("detector") not in only] + findings
            bundle.data["findings"] = findings
            bundle.save()
    print(f"{n} bundles")
    print("error findings gone:")
    for sig, c in gone_total.most_common(40):
        print(f"  {c:5}  {sig}")
    print("error findings new:")
    for sig, c in new_total.most_common(40):
        print(f"  {c:5}  {sig}")
    return 0


# --- operations -----------------------------------------------------------------------------------------------------
def cmd_worker(args) -> int:
    from webparity import worker

    return worker.run(_config(args), once=args.once, worker_name=args.name)


def cmd_nightly(args) -> int:
    from webparity import nightly

    return nightly.run(_config(args), only=args.only.split(",") if args.only else None)


def cmd_canary(args) -> int:
    """Runs the canary pages (all, or the named ones) and reports which detectors fired."""
    from webparity import canary
    from webparity.known import Known
    from webparity.proxy import Proxy
    from webparity.visit import visit

    cfg = _config(args)
    cfg["proxy"]["keep"] = "none"
    base, server = canary.start_server()
    if args.serve:
        print(f"serving the canary pages on {base}/c/<name>.html; Ctrl-C to stop")
        for e in canary.entries(base):
            print(f"  {e['url']}  expects {e['expect'] or 'nothing'}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
    proxy = None if not cfg["proxy"]["record"] else Proxy(cfg)
    handles = _handles(cfg, cfg["browsers"], proxy)
    known, bad = Known.from_config(cfg), 0
    try:
        for e in canary.entries(base, args.names or None):
            bundle = visit(e["url"], cfg, handles, proxy=proxy, known=known)
            sigs = [f["signature"] for f in bundle.data["findings"]]
            errors = [f["signature"] for f in bundle.data["findings"] if f["category"] == "error"]
            ok = all(any(s.startswith(x) for s in sigs) for x in e["expect"]) if e["expect"] else not errors
            bad += not ok
            expects = str(e["expect"] or "nothing")
            print(f"  {'OK  ' if ok else 'FAIL'} {e['name']:18} expects {expects:30} got {sigs[:6]}")
            for name, h in list(handles.items()):
                if not h.alive():
                    h.stop()
                    handles[name] = _handles(cfg, [name], proxy)[name]
    finally:
        for h in handles.values():
            h.stop()
        server.shutdown()
    print(f"canary: {bad} failed")
    return 1 if bad else 0


def cmd_sites(args) -> int:
    from webparity.sites import read_list

    cfg = _config(args)
    db = _db(cfg)
    if args.action == "add":
        added = 0
        for item in args.items:
            urls = read_list(item) if not item.startswith(("http://", "https://")) and "/" in item else [item]
            for url in urls:
                added += db.add_site(
                    url if "://" in url else "https://" + url, source=args.source, priority=args.priority
                )
        print(f"{added} sites added")
    elif args.action == "requeue":
        marks = ",".join("?" * len(args.items))
        n = db.requeue(f"domain IN ({marks})", args.items, priority=args.priority)
        print(f"{n} sites queued")
    else:
        for r in db.q("SELECT status, COUNT(*) n FROM sites GROUP BY status ORDER BY n DESC"):
            print(f"  {r['status']:10} {r['n']}")
        last = db.one(
            "SELECT COUNT(*) FROM visits WHERE source = 'webparity'"
            " AND started >= datetime('now', 'localtime', '-1 hour')"
        )
        print(f"  visits in the last hour: {last[0]}")
    return 0


def cmd_candidates(args) -> int:
    cfg = _config(args)
    db = _db(cfg)
    if args.set:
        sig, status = args.set
        n = db.x(
            "UPDATE candidates SET status = ?, issue = COALESCE(?, issue), note = COALESCE(?, note)"
            " WHERE signature = ?",
            (status, args.issue, args.note, sig),
        ).rowcount
        print(f"{n} updated")
        return 0 if n else 1
    where = "" if args.all else "WHERE status NOT IN ('dismissed', 'fixed', 'gone')"
    for r in db.q(f"SELECT * FROM candidates {where} ORDER BY sites DESC LIMIT ?", (args.limit,)):
        print(
            f"  {r['sites']:4}  {r['status']:13} {r['signature'][:70]:70}"
            f"  {(r['last_seen'] or '')[:10]}  {r['issue'] or ''}"
        )
    return 0


def cmd_import_old(args) -> int:
    from webparity.importer import import_old

    cfg = _config(args)
    print(json.dumps(import_old(_db(cfg), args.path, args.days)))
    return 0


def cmd_compare(args) -> int:
    from webparity.compare import compare, format_report

    cfg = _config(args)
    print(format_report(compare(_db(cfg), args.since)))
    return 0


# --- monitoring -----------------------------------------------------------------------------------------------------
def cmd_web(args) -> int:
    from webparity.web import serve

    cfg = _config(args)
    if args.listen:
        cfg["web"]["listen"] = args.listen
    return serve(cfg) or 0


def cmd_alerts(args) -> int:
    from webparity import alerts

    cfg = _config(args)
    if args.once:
        db = _db(cfg)
        for a in alerts.evaluate(cfg, db):
            print(a)
        return 0
    return alerts.run_loop(cfg) or 0


# --- issues ---------------------------------------------------------------------------------------------------------
def cmd_draft(args) -> int:
    from webparity.issues.drafts import check_draft, create_draft

    cfg = _config(args)
    if args.check:
        problems = check_draft(args.signature)
        for p in problems:
            print(f"  - {p}")
        return 1 if problems else 0
    print(create_draft(cfg, _db(cfg), args.signature, title=args.title, kind=args.kind))
    return 0


def cmd_register(args) -> int:
    from webparity.issues.register import register, register_comment

    cfg = _config(args)
    fn = register_comment if args.comment else register
    print(json.dumps(fn(cfg, _db(cfg), args.draft, args.number), ensure_ascii=False, default=str))
    return 0


def cmd_watch(args) -> int:
    from webparity.issues.github import watch

    cfg = _config(args)
    print(json.dumps(watch(cfg, _db(cfg)), ensure_ascii=False, default=str))
    return 0


def cmd_verify(args) -> int:
    from webparity.issues.verify import verify

    cfg = _config(args)
    numbers = [int(n.lstrip("#")) for n in args.numbers] or None
    from webparity.issues.verify import format_report

    print(format_report(verify(cfg, _db(cfg), numbers, include_verified=bool(numbers))))
    return 0


def cmd_index(args) -> int:
    from pathlib import Path

    from webparity.issues.index import generate_index

    cfg = _config(args)
    text = generate_index(cfg, _db(cfg), write=not args.output)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(args.output or Path(cfg["data_dir"]) / "INDEX.md")
    return 0


def cmd_import_gallery(args) -> int:
    from webparity.issues.import_gallery import import_gallery

    cfg = _config(args)
    print(json.dumps(import_gallery(cfg, _db(cfg), args.path), ensure_ascii=False, default=str))
    return 0


def cmd_reduce(args) -> int:
    from webparity.reduce import run

    return run(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="webparity", description="Compare how web engines render real websites.")
    parser.add_argument("--version", action="version", version=f"webparity {__version__}")
    parser.add_argument("--config", help="config file (default: $WEBPARITY_CONFIG, ./config.yaml)")
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("probe", help="run a script or test case in all browsers and print the results")
    p.add_argument("targets", nargs="+", help="URLs or local test case files (served on 127.0.0.1)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("-j", "--js", help="an expression, or statements with their own return")
    g.add_argument("--js-file", help="read the script from a file")
    p.add_argument("--browsers", default="ladybird,chromium,firefox", help="comma-separated (default: all three)")
    p.add_argument("--wait", type=float, help="seconds after loading (default 0.3 for test cases, 5 for sites)")
    p.add_argument("--mobile", action="store_true", help="a 390x844 viewport instead of 1440x900")
    p.add_argument("--fresh", action="store_true", help="a new browser session for every target")
    p.add_argument("--root", help="the directory served for local test cases (default: their common parent)")
    p.add_argument("--json", action="store_true", help="print the results as JSON")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--record", metavar="NAME", help="record the traffic into <data>/recordings/NAME.warc.gz")
    g.add_argument("--replay", metavar="NAME", help="answer only from that recording, offline")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("visit", help="visit sites in all browsers and save visit bundles")
    p.add_argument("urls", nargs="+")
    p.add_argument("--browsers", help="comma-separated (default: config browsers)")
    p.add_argument("--shallow", action="store_true", help="no interaction and no reload")
    p.add_argument("--no-record", action="store_true", help="don't record through the proxy")
    p.add_argument("--keep", choices=["findings", "all", "none"], help="which recordings to keep")
    p.add_argument("--record-db", action="store_true", help="also store the visit and its findings in the database")
    p.set_defaults(fn=cmd_visit)

    p = sub.add_parser("replay", help="replay a recorded visit offline and compare the DOM")
    p.add_argument("visit", help="a visit id or bundle directory")
    p.add_argument("--browsers")
    p.add_argument("--screenshot", action="store_true", help="save <browser>-replay.png in the bundle")
    p.add_argument("--show-missing", type=int, default=10)
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("reanalyze", help="run the detectors again on saved visit bundles and show the difference")
    p.add_argument("bundles", nargs="*", help="bundle directories (default: all under data/visits)")
    p.add_argument("--since", help="only bundles from this day on (YYYY-MM-DD)")
    p.add_argument("--detector", help="only these detectors (comma-separated)")
    p.add_argument("--write", action="store_true", help="save the new findings in the bundles")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_reanalyze)

    p = sub.add_parser("reduce", help="reduce a page to a small test case (ddmin over DOM, CSS and scripts)")
    from webparity.reduce import add_arguments

    add_arguments(p)
    p.set_defaults(fn=cmd_reduce)

    p = sub.add_parser("worker", help="visit queued sites until stopped")
    p.add_argument("--once", action="store_true", help="one visit, then exit")
    p.add_argument("--name", help="worker name (default: $WEBPARITY_WORKER or the host name)")
    p.set_defaults(fn=cmd_worker)

    p = sub.add_parser("nightly", help="the nightly chain: build, canary, regression watch, verification, requeue")
    p.add_argument("--only", help="only these steps (comma-separated)")
    p.set_defaults(fn=cmd_nightly)

    p = sub.add_parser("canary", help="visit the canary pages and check that every detector fires")
    p.add_argument("names", nargs="*", help="only these canaries")
    p.add_argument("--serve", action="store_true", help="only serve the pages, for manual checks")
    p.set_defaults(fn=cmd_canary)

    p = sub.add_parser("sites", help="the site queue: status, add, requeue")
    p.add_argument("action", nargs="?", default="status", choices=["status", "add", "requeue"])
    p.add_argument("items", nargs="*", help="URLs, domains or site list files")
    p.add_argument("--source", default="cli")
    p.add_argument("--priority", type=int, default=50)
    p.set_defaults(fn=cmd_sites)

    p = sub.add_parser("candidates", help="the candidate list (one row per error signature)")
    p.add_argument("--all", action="store_true")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--set", nargs=2, metavar=("SIGNATURE", "STATUS"))
    p.add_argument("--issue")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_candidates)

    p = sub.add_parser("import-old", help="import sites, candidates and recent findings from the previous explorer")
    p.add_argument("path")
    p.add_argument("--days", type=int, default=14, help="import the old visits and findings of the last N days")
    p.set_defaults(fn=cmd_import_old)

    p = sub.add_parser("compare", help="compare findings with the previous explorer, site by site")
    p.add_argument("--since", default="2000-01-01")
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("web", help="serve the monitoring web interface")
    p.add_argument("--listen", help="host:port (default: config web.listen, 127.0.0.1:8700)")
    p.set_defaults(fn=cmd_web)

    p = sub.add_parser("alerts", help="evaluate the alert rules every minute and notify")
    p.add_argument("--once", action="store_true")
    p.set_defaults(fn=cmd_alerts)

    p = sub.add_parser("draft", help="create an issue draft from a candidate (or --check a draft)")
    p.add_argument("signature")
    p.add_argument("--title")
    p.add_argument("--check", action="store_true", help="check the draft file given instead of a signature")
    p.add_argument("--kind", choices=["issue", "comment"], default="issue")
    p.set_defaults(fn=cmd_draft)

    p = sub.add_parser("register", help="link a filed draft to its issue number")
    p.add_argument("draft")
    p.add_argument("number")
    p.add_argument("--comment", action="store_true", help="the draft was posted as a comment on issue NUMBER")
    p.set_defaults(fn=cmd_register)

    p = sub.add_parser("watch", help="refresh filed issues from GitHub (anonymous, read-only)")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("verify", help="check filed issues' test cases against the current build")
    p.add_argument("numbers", nargs="*")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("index", help="write INDEX.md from the database")
    p.add_argument("--output")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("import-gallery", help="import filed issues from an existing gallery directory")
    p.add_argument("path")
    p.set_defaults(fn=cmd_import_gallery)
    return parser


COMMANDS = [
    "probe", "visit", "replay", "reanalyze", "reduce", "worker", "nightly", "canary", "sites", "candidates",
    "import-old", "compare", "web", "alerts", "draft", "register", "watch", "verify", "index", "import-gallery",
]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.fn(args)
