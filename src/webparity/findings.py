# SPDX-License-Identifier: BSD-2-Clause
"""Findings: what a detector reports about a visit.

A finding has a signature (``crash:WebContent:SIGSEGV``, ``layout:missing:main``), a category and a text for people.
Only ``error`` findings make a site a candidate; ``info`` findings are kept and counted but are not enough.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

ERROR, INFO = "error", "info"


@dataclass
class Finding:
    signature: str
    category: str  # ERROR or INFO
    text: str
    detector: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    # Set when a known difference or a filed bug matches (see webparity.known)
    known: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Finding:
        return cls(**{k: d.get(k) for k in ("signature", "category", "text", "detector", "data", "known") if k in d})

    @property
    def family(self) -> str:
        """The first part of the signature (``crash``, ``layout`` ...), used for grouping."""
        return self.signature.split(":", 1)[0]
