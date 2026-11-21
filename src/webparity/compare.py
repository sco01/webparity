# SPDX-License-Identifier: BSD-2-Clause
"""Compare webparity's findings with the previous explorer's, site by site (phase 3: running alongside).

For every site visited by both since a date, the latest visit of each is compared by error signature family and by
full signature. The report lists agreement, findings only the old explorer had (possible misses) and findings only
webparity had (possible new noise or new detection), grouped by signature family.
"""

from __future__ import annotations

from collections import Counter

from webparity.db import DB


def latest_errors(db: DB, source: str, since: str) -> dict[int, set[str]]:
    rows = db.q(
        "SELECT v.site, v.id FROM visits v JOIN (SELECT site, MAX(started) s FROM visits"
        " WHERE source = ? AND started >= ?"
        " GROUP BY site) l ON l.site = v.site AND l.s = v.started WHERE v.source = ?",
        (source, since, source),
    )
    out: dict[int, set[str]] = {}
    for r in rows:
        sigs = db.q(
            "SELECT signature FROM findings WHERE visit = ? AND category = 'error' AND known IS NULL", (r["id"],)
        )
        out[r["site"]] = {s["signature"] for s in sigs}
    return out


def compare(db: DB, since: str) -> dict:
    old = latest_errors(db, "old", since)
    new = latest_errors(db, "webparity", since)
    both = sorted(set(old) & set(new))
    agree = only_old = only_new = 0
    fam_old: Counter = Counter()
    fam_new: Counter = Counter()
    examples: dict[str, list] = {}
    for site in both:
        o = {s.split(":", 1)[0] for s in old[site]}
        n = {s.split(":", 1)[0] for s in new[site]}
        if (not old[site]) == (not new[site]):
            agree += 1
        for fam in o - n:
            only_old += 1
            fam_old[fam] += 1
            examples.setdefault(f"old:{fam}", []).append(site)
        for fam in n - o:
            only_new += 1
            fam_new[fam] += 1
            examples.setdefault(f"new:{fam}", []).append(site)
    domains = {r["id"]: r["domain"] for r in db.q("SELECT id, domain FROM sites")}
    return {
        "sites_compared": len(both),
        "same_verdict": agree,
        "only_old": dict(fam_old.most_common()),
        "only_new": dict(fam_new.most_common()),
        "examples": {k: [domains.get(s, s) for s in v[:5]] for k, v in examples.items()},
    }


def format_report(r: dict) -> str:
    lines = [
        f"sites visited by both: {r['sites_compared']}",
        f"same verdict (both clean or both with errors): {r['same_verdict']}",
        "only the old explorer (possible misses):",
    ]
    lines += [
        f"  {fam:24} {n:5}  e.g. {', '.join(r['examples'].get('old:' + fam, []))}" for fam, n in r["only_old"].items()
    ]
    lines.append("only webparity (new detection or new noise):")
    lines += [
        f"  {fam:24} {n:5}  e.g. {', '.join(r['examples'].get('new:' + fam, []))}" for fam, n in r["only_new"].items()
    ]
    return "\n".join(lines)
