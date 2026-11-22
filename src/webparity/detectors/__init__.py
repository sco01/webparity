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
    proxy_errors(bundle, findings)
    return findings


PROXY_KNOWN = "proxy upstream errors (the visit is repeated)"


def proxy_errors(bundle, findings: list[Finding]) -> None:
    """When the proxy answered requests with its own 502 (the server closed or reset the connection), a browser lost
    resources the other one got, so the visit's error findings aren't evidence: they are marked, and the worker queues
    the site again."""
    errors = bundle.data.get("proxy_upstream_errors") or []
    if not errors:
        return
    for f in findings:
        if f.category == ERROR and not f.known:
            f.known = PROXY_KNOWN
    per_browser: dict[str, int] = {}
    for e in errors:
        name = "ladybird" if "Ladybird" in (e.get("user_agent") or "") else "other"
        per_browser[name] = per_browser.get(name, 0) + 1
    findings.append(
        Finding(
            "proxy-upstream-errors",
            "info",
            f"The proxy answered {len(errors)} requests with its own 502 ({errors[0].get('error') or 'error'})",
            "proxy",
            {"count": len(errors), "per_browser": per_browser, "examples": errors[:10]},
        )
    )


__all__ = ["Detector", "Finding", "ERROR", "PROXY_KNOWN", "all_detectors", "analyze"]
