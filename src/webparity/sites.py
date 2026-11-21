# SPDX-License-Identifier: BSD-2-Clause
"""Site lists and site keys."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

# Second-level labels under which the registrable domain has three labels (co.uk, com.au ...). Short on purpose: it
# only decides which visits count as the same site for politeness and grouping.
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "edu", "ac", "or", "ne", "go"}


def domain_of(url: str) -> str:
    """The host without ``www.``, lower case; the key that makes two URLs the same site."""
    try:
        host = urllib.parse.urlsplit(url if "://" in url else "https://" + url).hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def registrable(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def read_list(path: str | Path) -> list[str]:
    """One URL or host per line; ``#`` starts a comment."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        out.append(line if "://" in line else "https://" + line)
    return out
