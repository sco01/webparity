# SPDX-License-Identifier: BSD-2-Clause
"""New sites from the visits: the domains a page links to, and two sub pages of a site's front page.

The probe keeps both for every visit (``link_domains`` and ``internal_links``). After a visit the worker queues:

- linked domains in the configured top-level domains, at most ``per_visit`` of them, each as its front page;
- for a front page, ``subpages`` of its own pages, chosen to have other templates than the front page.

No registrable domain gets more than ``per_domain`` rows, counting every row it has, retired ones too (2026-11-12 in
the previous explorer: one site added 3,541 subdomains and took a fifth of the visits). Excluded domains
(``worker.exclude``) and ``skip_domains`` are never added; excluded servers are retired when they come up in the queue.
"""

from __future__ import annotations

import re
import urllib.parse
from collections import Counter

from webparity.db import DB, now
from webparity.sites import domain_of, registrable

# Pages, not handlers or sign-in flows (from the previous explorer, 2026-11-09)
NOT_A_PAGE = re.compile(
    r"\.(ashx|axd|asmx|cgi|php\d?\?|jsp\?)$|/(auth|sso|oauth2?|saml|freja|bankid|initiate|callback|redirect|wp-json|api)"
    r"(/|$)|[&=;]",
    re.I,
)


def choose_subpages(urls: list[str], n: int = 2) -> list[str]:
    """Sub pages with other templates than the front page: first the one most like an article (a deep path with a
    long slug), then pages with another first path segment (two levels deep preferred)."""
    cands = []
    for u in urls or []:
        p = urllib.parse.urlsplit(u)
        seg = [x for x in p.path.split("/") if x]
        if not seg or len(u) > 250 or not any(re.search(r"[A-Za-z]", x) for x in seg) or NOT_A_PAGE.search(p.path):
            continue
        cands.append((u, seg))
    if not cands or n <= 0:
        return []
    chosen = [max(cands, key=lambda c: (len(c[1]) >= 2 or len(c[1][-1]) >= 20, c[1][-1].count("-"), len(c[1][-1])))]
    first = {chosen[0][1][0]}
    for u, seg in sorted(cands, key=lambda c: ({2: 0, 1: 1}.get(len(c[1]), 2), len(c[0]))):
        if len(chosen) >= n:
            break
        if seg[0] not in first:
            chosen.append((u, seg))
            first.add(seg[0])
    return [u for u, _ in chosen]


def page_key(url: str) -> str:
    """A sub page's site key: its host and path ("host/path"), as the queue keeps sub pages."""
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    return domain_of(url) + path


class Discoverer:
    """Adds the sites a visit leads to. ``counts`` caches the rows per registrable domain (for many visits at once)."""

    def __init__(self, db: DB, config: dict, cache: bool = False):
        self.db = db
        d = config.get("discover") or {}
        self.enabled = bool(d.get("enabled", True))
        self.tlds = tuple("." + t.strip(".").lower() for t in d.get("tlds") or ["se"])
        self.per_visit = int(d.get("per_visit", 15))
        self.per_domain = int(d.get("per_domain", 5))
        self.subpages = int(d.get("subpages", 2))
        self.priority = int(d.get("priority", 30))
        self.subpage_priority = int(d.get("subpage_priority", 25))
        excluded = (config.get("worker") or {}).get("exclude") or {}
        self.skip = {s.lower().strip(".") for s in [*(d.get("skip_domains") or []), *(excluded.get("domains") or [])]}
        self.counts: Counter | None = None
        if cache:
            self.counts = Counter(registrable(r[0].split("/")[0]) for r in db.q("SELECT domain FROM sites"))

    def _count(self, reg: str) -> int:
        if self.counts is not None:
            return self.counts[reg]
        return self.db.one(
            "SELECT COUNT(*) FROM sites WHERE domain = ? OR domain LIKE ? OR domain LIKE ? OR domain LIKE ?",
            (reg, "%." + reg, reg + "/%", "%." + reg + "/%"),
        )[0]

    def _skipped(self, host: str) -> bool:
        return any(host == s or host.endswith("." + s) for s in self.skip)

    def _insert(self, url: str, key: str, reg: str, source: str, priority: int) -> bool:
        if self._count(reg) >= self.per_domain:
            return False
        added = self.db.x(
            "INSERT OR IGNORE INTO sites (url, domain, source, priority, added) VALUES (?, ?, ?, ?, ?)",
            (url, key, source, priority, now()),
        ).rowcount
        if added and self.counts is not None:
            self.counts[reg] += 1
        return bool(added)

    def visit(self, data: dict) -> dict:
        """Queues what one visit (a bundle's ``visit.json``) leads to; returns the counts."""
        out = {"domains": 0, "subpages": 0}
        if not self.enabled:
            return out
        probe = {}
        for name in ("ladybird", "chromium", "firefox"):
            p = ((data.get("browsers") or {}).get(name) or {}).get("probe")
            if isinstance(p, dict) and (p.get("link_domains") or p.get("internal_links")):
                probe = p
                break
        page = data.get("url") or ""
        page_host = domain_of(page)
        if not page_host:
            return out
        for h in probe.get("link_domains") or []:
            if out["domains"] >= self.per_visit:
                break
            host = domain_of("https://" + str(h))
            if not host or not host.endswith(self.tlds) or self._skipped(host):
                continue
            reg = registrable(host)
            if reg == registrable(page_host):
                continue
            if self.db.one("SELECT 1 FROM sites WHERE domain = ?", (host,)):
                continue
            out["domains"] += self._insert(f"https://{host}/", host, reg, f"länk:{page_host}", self.priority)
        # Sub pages of a front page only, so pages don't lead to pages
        if urllib.parse.urlsplit(page).path in ("", "/") and self.subpages > 0:
            reg = registrable(page_host)
            for u in choose_subpages(
                [x for x in probe.get("internal_links") or [] if domain_of(x) == page_host], self.subpages
            ):
                out["subpages"] += self._insert(u, page_key(u), reg, f"undersida:{page_host}", self.subpage_priority)
        return out
