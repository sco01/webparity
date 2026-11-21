# SPDX-License-Identifier: BSD-2-Clause
"""The webparity command line. Phase 0: the commands exist but are not implemented yet."""

import argparse
import sys

from webparity import __version__

# The commands from the plan, in the order of the pipeline.
COMMANDS = {
    "visit": "visit a site in all browsers and save a visit bundle",
    "reanalyze": "run the detectors again on saved visit bundles",
    "probe": "run a script or test case in all browsers and print the results",
    "reduce": "reduce a finding to a small test case (ddmin over DOM, CSS and scripts)",
    "verify": "check filed issues against the latest build",
    "draft": "create an issue draft from a candidate",
    "register": "link a filed draft to its issue number",
    "nightly": "run the nightly chain: build, canary, regression watch, verification",
}


IMPLEMENTED = {"probe"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="webparity", description="Compare how web engines render real websites.")
    parser.add_argument("--version", action="version", version=f"webparity {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")
    for name, help_text in COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        if name == "probe":
            p.add_argument("targets", nargs="+", help="URLs or local test case files (served on 127.0.0.1)")
            group = p.add_mutually_exclusive_group()
            group.add_argument("-j", "--js", help="an expression, or statements with their own return")
            group.add_argument("--js-file", help="read the script from a file")
            p.add_argument(
                "--browsers", default="ladybird,chromium,firefox", help="comma-separated (default: all three)"
            )
            p.add_argument("--wait", type=float, help="seconds after loading (default 0.3 for test cases, 5 for sites)")
            p.add_argument("--mobile", action="store_true", help="a 390x844 viewport instead of 1440x900")
            p.add_argument("--fresh", action="store_true", help="a new browser session for every target")
            p.add_argument("--root", help="the directory served for local test cases (default: their common parent)")
            p.add_argument("--json", action="store_true", help="print the results as JSON")
    return parser


def run_probe(args: argparse.Namespace) -> int:
    from webparity.probe import format_results, probe

    script = open(args.js_file, encoding="utf-8").read() if args.js_file else args.js
    results = probe(
        args.targets,
        script=script,
        browsers=[b.strip() for b in args.browsers.split(",") if b.strip()],
        wait=args.wait,
        mobile=args.mobile,
        fresh=args.fresh,
        root=args.root,
    )
    print(format_results(results, as_json=args.json))
    return 1 if any(r.error for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "probe":
        return run_probe(args)
    print(f"webparity {args.command}: not implemented yet", file=sys.stderr)
    return 2
