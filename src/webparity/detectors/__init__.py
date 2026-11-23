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
import re
import traceback
from urllib.parse import urlsplit

from webparity.findings import ERROR, Finding
from webparity.sites import registrable


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


# What the proxy reports that isn't the server's or the network's doing: the browser cancelled the request (a
# navigation, an aborted fetch), or the name doesn't resolve (an old tracker host; the same in both browsers)
NOT_UPSTREAM = re.compile(
    r"stream reset by client|client disconnected|name resolution|no address associated|name or service not known|"
    r"nodename nor servname|errno -[235]\b",
    re.I,
)


# Errors about reaching the host at all (2026-11-22: a Cloudflare challenge host unreachable over IPv6 in both browsers,
# with a new random path per request): judged per host. Other errors (the server closed or reset one connection) are
# judged per address: a host that closed connections in both browsers can still have cost each browser other files.
HOST_LEVEL = re.compile(r"connect call failed|network is unreachable|errno 101|errno 110|errno 111|timed out", re.I)


def upstream_errors(bundle) -> list[dict]:
    """The proxy's own 502s that came from the server or the network (closed or reset connections, failed connects,
    timeouts) and can make the browsers differ: GET requests (not beacons and telemetry POSTs) that failed in one
    browser only - per address, or per host for errors about reaching the host."""
    errors = [
        e
        for e in bundle.data.get("proxy_upstream_errors") or []
        if not NOT_UPSTREAM.search(e.get("error") or "") and (e.get("method") or "GET") == "GET"
    ]

    # Not reaching a third-party host (a tracker) loses the same in both browsers, so those count per host; on the
    # page's own site the browsers lose different files when its server refuses connections (2026-11-23: udance.se
    # refused both, and Ladybird lost Elementor's style sheets), so those count per address
    page_site = registrable(urlsplit(bundle.data.get("url") or "").hostname or "")

    def where(e: dict) -> str:
        url = e.get("url") or ""
        host = urlsplit(url).hostname or ""
        if HOST_LEVEL.search(e.get("error") or "") and registrable(host) != page_site:
            return host
        return url

    browsers: dict[str, set[bool]] = {}
    for e in errors:
        browsers.setdefault(where(e), set()).add("Ladybird" in (e.get("user_agent") or ""))
    return [e for e in errors if len(browsers[where(e)]) == 1]


# The server refusing or not answering connections (not a reset stream): it may be limiting connections per address
REFUSED = re.compile(r"connect call failed|errno 111|errno 110|connection refused", re.I)


def server_refused(bundle) -> bool:
    """The page's own site refused connections during the visit, in either browser: its server (or a firewall in front
    of it) is limiting us, so the worker leaves that server alone for a while instead of visiting it again at once."""
    page_site = registrable(urlsplit(bundle.data.get("url") or "").hostname or "")
    for e in bundle.data.get("proxy_upstream_errors") or []:
        host = urlsplit(e.get("url") or "").hostname or ""
        if page_site and registrable(host) == page_site and REFUSED.search(e.get("error") or ""):
            return True
    return False


def proxy_errors(bundle, findings: list[Finding]) -> None:
    """When the proxy answered requests with its own 502 (the server closed or reset the connection), a browser lost
    resources the other one got, so the visit's error findings aren't evidence: they are marked, and the worker queues
    the site again."""
    errors = upstream_errors(bundle)
    if not errors and server_refused(bundle):
        errors = [e for e in bundle.data.get("proxy_upstream_errors") or [] if REFUSED.search(e.get("error") or "")]
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


__all__ = [
    "Detector",
    "Finding",
    "ERROR",
    "PROXY_KNOWN",
    "all_detectors",
    "analyze",
    "server_refused",
    "upstream_errors",
]
