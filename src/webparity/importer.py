# SPDX-License-Identifier: BSD-2-Clause
"""Import from the previous explorer's database (the Swedish-named SQLite schema), so phase 3 starts from today's state.

Imported: sites (with their status), candidates (with status, issue and note), per-site exceptions, missing features,
and the old explorer's recent visits and findings as ``source = 'old'`` rows, used by ``webparity compare``.
Signatures and statuses are translated to English with ``webparity.signatures``.
"""

from __future__ import annotations

import json
import sqlite3

from webparity.db import DB, now

SITE_STATUS = {
    "kö": "queued",
    "pågår": "queued",
    "ren": "clean",
    "kandidat": "candidate",
    "blockerad": "blocked",
    "död": "dead",
    "fel": "failed",
    "borta": "clean",
}
CANDIDATE_STATUS = {
    "ny": "new",
    "undersöks": "investigating",
    "rapporterad": "filed",
    "avfärdad": "dismissed",
    "rättad": "fixed",
    "borta": "gone",
}
CATEGORY = {"fel": "error", "info": "info"}


def _translate(signature: str) -> str:
    try:
        from webparity.signatures import translate_signature
    except ImportError:  # pragma: no cover - before the mapping exists
        return signature
    return translate_signature(signature)


def import_old(db: DB, old_path: str, findings_days: int = 14) -> dict:
    old = sqlite3.connect(f"file:{old_path}?mode=ro", uri=True)
    old.row_factory = sqlite3.Row
    counts = {"sites": 0, "candidates": 0, "site_exceptions": 0, "missing_features": 0, "visits": 0, "findings": 0}
    site_ids: dict[int, int] = {}
    with db.tx():
        for r in old.execute("SELECT * FROM sajter"):
            status = SITE_STATUS.get(r["status"], "queued")
            db.x(
                "INSERT INTO sites (url, domain, source, priority, status, added, visited, attempts, template, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(domain) DO NOTHING",
                (
                    r["url"],
                    r["doman"],
                    r["kalla"],
                    r["prio"],
                    status,
                    r["tillagd"],
                    r["besokt"],
                    r["forsok"],
                    r["mall"],
                    r["kommentar"],
                ),
            )
            row = db.one("SELECT id FROM sites WHERE domain = ?", (r["doman"],))
            site_ids[r["id"]] = row["id"]
            counts["sites"] += 1
        for r in old.execute("SELECT * FROM kandidater"):
            db.x(
                "INSERT INTO candidates (signature, text, first_seen, last_seen, sites, status, issue, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(signature) DO UPDATE SET"
                " status = excluded.status, issue = COALESCE(excluded.issue, candidates.issue),"
                " note = COALESCE(excluded.note, candidates.note), sites = MAX(candidates.sites, excluded.sites)",
                (
                    _translate(r["sig"]),
                    r["text"],
                    r["forst"],
                    r["senast"],
                    r["antal_sajter"] or 0,
                    CANDIDATE_STATUS.get(r["status"], "new"),
                    r["issue"],
                    r["anteckning"],
                ),
            )
            counts["candidates"] += 1
        for r in old.execute("SELECT * FROM kanda"):
            db.x(
                "INSERT OR IGNORE INTO site_exceptions (domain, signature, reason, added) VALUES (?, ?, ?, ?)",
                (r["doman"], _translate(r["sig"]), r["motivering"], r["datum"] or now()),
            )
            counts["site_exceptions"] += 1
        for r in old.execute("SELECT * FROM saknade"):
            db.x(
                "INSERT OR IGNORE INTO missing_features (signature, first_seen, last_seen, sites) VALUES (?, ?, ?, ?)",
                (_translate(r["sig"]), r["forst"], r["senast"], r["antal_sajter"] or 0),
            )
            counts["missing_features"] += 1
        # The old explorer's recent visits and findings, for comparing the two explorers side by side
        db.x("DELETE FROM findings WHERE visit LIKE 'old-%'")
        db.x("DELETE FROM visits WHERE source = 'old'")
        cutoff = f"-{int(findings_days)} days"
        for v in old.execute("SELECT * FROM besok WHERE start >= datetime('now', ?)", (cutoff,)):
            if v["sajt"] not in site_ids:
                continue
            vid = f"old-{v['id']}"
            db.x(
                "INSERT OR REPLACE INTO visits (id, site, url, started, seconds, worker, build, browsers, status,"
                " errors,"
                " infos, first_error, path, source) VALUES (?, ?, (SELECT url FROM sites WHERE id = ?), ?, ?, ?, ?,"
                " 'ladybird,chromium', ?, 0, 0, NULL, ?, 'old')",
                (
                    vid,
                    site_ids[v["sajt"]],
                    site_ids[v["sajt"]],
                    v["start"],
                    v["sekunder"],
                    v["arbetare"],
                    v["bygge"],
                    SITE_STATUS.get(v["status"], v["status"]),
                    v["mapp"],
                ),
            )
            counts["visits"] += 1
            for f in old.execute("SELECT * FROM fynd WHERE besok = ?", (v["id"],)):
                db.x(
                    "INSERT INTO findings (visit, site, signature, category, detector, text, data)"
                    " VALUES (?, ?, ?, ?, 'old', ?, ?)",
                    (
                        vid,
                        site_ids[v["sajt"]],
                        _translate(f["sig"]),
                        CATEGORY.get(f["kat"], f["kat"]),
                        f["text"],
                        f["data"] or json.dumps({}),
                    ),
                )
                counts["findings"] += 1
        db.x(
            "UPDATE visits SET errors = (SELECT COUNT(*) FROM findings WHERE visit = visits.id AND category = 'error'),"
            " infos = (SELECT COUNT(*) FROM findings WHERE visit = visits.id AND category = 'info')"
            " WHERE source = 'old'"
        )
    old.close()
    return counts
