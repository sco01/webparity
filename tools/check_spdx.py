# SPDX-License-Identifier: BSD-2-Clause
"""Fail if a source file lacks an SPDX-License-Identifier in its first five lines.

Run from the repository root: python tools/check_spdx.py
"""

import subprocess
import sys
from pathlib import Path

CHECKED_SUFFIXES = {".conf", ".py", ".sh", ".yml", ".yaml", ".toml", ".js", ".css", ".html", ".md"}
CHECKED_NAMES = {"Dockerfile", ".gitignore", "packages.txt"}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"], capture_output=True, text=True, check=True
    ).stdout
    return [Path(line) for line in out.splitlines() if line]


def main() -> int:
    missing = []
    for path in tracked_files():
        if path.suffix not in CHECKED_SUFFIXES and path.name not in CHECKED_NAMES:
            continue
        head = path.read_text(encoding="utf-8", errors="replace").splitlines()[:5]
        if not any("SPDX-License-Identifier:" in line for line in head):
            missing.append(str(path))
    for name in missing:
        print(f"missing SPDX-License-Identifier: {name}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
