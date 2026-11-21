# SPDX-License-Identifier: BSD-2-Clause
"""Detectors: each looks at a visit bundle and returns findings.

A detector is a module in this package with a ``Detector`` subclass. Detectors only read the bundle; they never talk
to a browser, so they can run again on saved bundles (``webparity reanalyze``). Each has fixtures in
``tests/fixtures/detectors/<name>/``: a ``visit.json`` (and any files the detector reads) with ``expected.json``, the
signatures it must report. Every false finding we discover becomes a negative fixture before the detector is fixed.
"""

from __future__ import annotations

import importlib
import pkgutil
import traceback

from webparity.findings import ERROR, Finding


class Detector:
    name = "base"
    description = ""

    def run(self, bundle) -> list[Finding]:  # pragma: no cover - interface
        raise NotImplementedError


_REGISTRY: list[Detector] | None = None


def all_detectors() -> list[Detector]:
    global _REGISTRY
    if _REGISTRY is None:
        found = []
        for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
            if info.name.startswith("_"):
                continue
            module = importlib.import_module(f"{__name__}.{info.name}")
            for value in vars(module).values():
                if isinstance(value, type) and issubclass(value, Detector) and value is not Detector:
                    if value.__module__ == module.__name__:
                        found.append(value())
        _REGISTRY = found
    return _REGISTRY


def analyze(bundle, only: list[str] | None = None, known=None) -> list[Finding]:
    """Runs every detector (or ``only`` these) on a bundle; then marks findings that match known differences."""
    findings: list[Finding] = []
    for detector in all_detectors():
        if only and detector.name not in only:
            continue
        try:
            for f in detector.run(bundle) or []:
                f.detector = f.detector or detector.name
                findings.append(f)
        except Exception as exc:  # a broken detector must not lose the visit
            findings.append(
                Finding(
                    f"detector-error:{detector.name}",
                    "info",
                    f"{detector.name} failed: {exc}",
                    detector.name,
                    {"traceback": traceback.format_exc()[-2000:]},
                )
            )
    if known is not None:
        known.mark(bundle, findings)
    return findings


__all__ = ["Detector", "Finding", "ERROR", "all_detectors", "analyze"]
