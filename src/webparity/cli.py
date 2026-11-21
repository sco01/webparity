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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="webparity", description="Compare how web engines render real websites.")
    parser.add_argument("--version", action="version", version=f"webparity {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")
    for name, help_text in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    print(f"webparity {args.command}: not implemented yet", file=sys.stderr)
    return 2
