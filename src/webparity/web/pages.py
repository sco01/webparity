# SPDX-License-Identifier: BSD-2-Clause
"""The pages. Everything shown from the database or a bundle is untrusted (site titles, URLs, console text) and goes
through ``esc``; links to other pages are built with ``url_path``/``url_query``. No JavaScript."""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from webparity import __version__, metrics

CSS = """
:root{color-scheme:light dark;--bg:#f6f8f9;--panel:#fff;--fg:#17202a;--muted:#5b6773;--line:#dce2e7;--card:#eef2f4;
--accent:#0b7d93;--accent-soft:rgba(11,125,147,.1);--bad:#b3261e;--good:#1b7f3b;--warn:#8a5a00;--head:#fff;
--mono:ui-monospace,SFMono-Regular,"JetBrains Mono",Menlo,Consolas,monospace;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#141b20;--panel:#1a2329;--fg:#e3e8eb;--muted:#93a1ab;--line:#2a363e;
--card:#1e292f;--accent:#3cc3d8;--accent-soft:rgba(60,195,216,.12);--bad:#ff8a80;--good:#6fd394;--warn:#f0c060;
--head:#10161a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 var(--sans);min-height:100vh;display:flex;
flex-direction:column}
header{background:var(--head);border-bottom:1px solid var(--line);padding:0 20px;display:flex;flex-wrap:wrap;
gap:0 28px;align-items:center;min-height:52px;position:sticky;top:0;z-index:10}
.brand{display:flex;align-items:center;gap:9px;font:600 .82em/1 var(--mono);letter-spacing:.2em;
text-transform:uppercase;color:var(--accent)}
.brand:hover{text-decoration:none}.brand svg{width:22px;height:22px}
header nav{display:flex;flex-wrap:wrap;gap:2px}
header nav a{color:var(--muted);font-size:.92em;font-weight:500;padding:15px 10px 13px;border-bottom:2px solid transparent}
header nav a:hover{color:var(--fg);text-decoration:none}
header nav a[aria-current=page]{color:var(--fg);border-bottom-color:var(--accent)}
main{padding:22px 20px 48px;max-width:1240px;width:100%;flex:1}
footer{border-top:1px solid var(--line);padding:12px 20px;color:var(--muted);font-size:.8em}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:1.6em;font-weight:700;letter-spacing:-.01em;margin:.1em 0 .7em}
h2{font:600 .74em/1.3 var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--accent);
margin:2.4em 0 .8em}
h1+p,h1+.cards{margin-top:0}
table{border-collapse:collapse;width:100%;font-size:.92em}
.scroll{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
.scroll+.scroll{margin-top:12px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--accent-soft)}
th{font:600 .72em/1.4 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
background:var(--card)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
@media (max-width:700px){header{position:static}main{padding-top:16px}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;
border-top:3px solid var(--line)}
.card .v{font-size:1.75em;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.card .l{font:500 .7em/1.4 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-top:4px}
.card:has(.v.bad){border-top-color:var(--bad)}.card:has(.v.good){border-top-color:var(--good)}
.card:has(.v.warn){border-top-color:var(--warn)}
.muted{color:var(--muted)}.bad{color:var(--bad)}.good{color:var(--good)}.warn{color:var(--warn)}
code,pre,.mono{font-family:var(--mono);font-size:.88em}
pre{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;
padding:12px 14px;overflow-x:auto;white-space:pre-wrap;word-break:break-word}
.wrap{word-break:break-all}
.shots{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.shots figure{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px}
.shots img{width:100%;height:auto;border-radius:6px;display:block}
.shots figcaption{font:500 .72em/1.4 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
padding:8px 2px 0}
form.filters{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin-bottom:16px;background:var(--panel);
border:1px solid var(--line);border-radius:10px;padding:12px 14px}
form.filters label{display:flex;flex-direction:column;gap:4px;font:500 .7em/1.4 var(--mono);letter-spacing:.1em;
text-transform:uppercase;color:var(--muted)}
select,input,button{font:inherit;font-size:14px;letter-spacing:0;text-transform:none;color:var(--fg);
background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 9px}
form.filters button{background:var(--accent);border-color:var(--accent);color:var(--head);font-weight:600;cursor:pointer;padding:6px 16px}
select:focus,input:focus,button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 16px}
.tabs a{font-size:.88em;padding:4px 12px;border:1px solid var(--line);border-radius:999px;color:var(--muted);
background:var(--panel)}
.tabs a:hover{color:var(--fg);border-color:var(--muted);text-decoration:none}
.tabs a[aria-current=page]{color:var(--accent);border-color:var(--accent);background:var(--accent-soft)}
.status{display:inline-block;font:500 .7em/1.7 var(--mono);letter-spacing:.06em;text-transform:uppercase;
padding:0 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.s-new{color:var(--accent);border-color:var(--accent)}.s-investigating,.s-review{color:var(--warn);border-color:var(--warn)}
.s-filed,.s-fixed{color:var(--good);border-color:var(--good)}
svg.spark{width:100%;max-width:560px;height:70px;display:block;background:var(--panel);border:1px solid var(--line);
border-radius:10px;padding:8px 10px}
svg.spark rect{fill:var(--accent)}svg.spark text{fill:var(--muted);font-size:9px;font-family:var(--mono)}
"""

# Two offset frames: one page, two engines.
BRAND = (
    '<a class="brand" href="/" aria-label="webparity home"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="2" aria-hidden="true"><rect x="2.5" y="2.5" width="13" height="13" rx="2.5"/>'
    '<rect x="8.5" y="8.5" width="13" height="13" rx="2.5" stroke-opacity=".55"/></svg>webparity</a>'
)

NAV = [
    ("/", "Health"),
    ("/findings", "Findings"),
    ("/candidates", "Candidates"),
    ("/issues", "Issues"),
    ("/review", "Review"),
    ("/metrics", "Metrics"),
]


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def url_path(*parts: str) -> str:
    return "/" + "/".join(urllib.parse.quote(str(p), safe="") for p in parts)


def url_query(path: str, **params) -> str:
    params = {k: v for k, v in params.items() if v not in (None, "")}
    return path + ("?" + urllib.parse.urlencode(params) if params else "")


def page(title: str, body: str, current: str = "") -> str:
    nav = "".join(
        f'<a href="{href}"{" aria-current=page" if href == current else ""}>{esc(label)}</a>' for href, label in NAV
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)} · webparity</title><style>{CSS}</style></head><body>"
        f"<header>{BRAND}<nav>{nav}</nav></header><main>{body}</main>"
        f"<footer>webparity {esc(__version__)} · compares how web engines render real websites</footer></body></html>"
    )


def error_page(status: int, message: str) -> str:
    return page(f"{status}", f"<h1>{status}</h1><p>{esc(message)}</p>")


def table(headers: list[str], rows: list[list[str]], numeric: set[int] | None = None, empty: str = "None.") -> str:
    """A table; cells are HTML (escape them before)."""
    if not rows:
        return f'<p class="muted">{esc(empty)}</p>'
    numeric = numeric or set()
    head = "".join(f"<th{' class=n' if i in numeric else ''}>{esc(h)}</th>" for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f"<td{' class=n' if i in numeric else ''}>{c}</td>" for i, c in enumerate(r)) + "</tr>"
        for r in rows
    )
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def card(value, label: str, css: str = "") -> str:
    return f'<div class="card"><div class="v {css}">{esc(value)}</div><div class="l">{esc(label)}</div></div>'


def status_chip(status) -> str:
    """A candidate status as a small coloured label."""
    return f'<span class="status s-{esc(status or "none")}">{esc(status)}</span>'


def issue_link(config: dict, number) -> str:
    if number in (None, ""):
        return ""
    text = str(number).lstrip("#")
    if not text.isdigit():
        return esc(number)
    repo = (config.get("github") or {}).get("repository") or ""
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo):
        return f"#{text}"
    return f'<a href="https://github.com/{esc(repo)}/issues/{text}" rel="noreferrer">#{text}</a>'


def visit_link(visit_id, text=None) -> str:
    return f'<a href="{url_path("visit", visit_id)}" class="mono">{esc(text or visit_id)}</a>'


def sparkline(points: list[tuple[str, int]]) -> str:
    """Inline SVG bars, one per hour."""
    width, height, bottom = 480, 60, 12
    n = max(len(points), 1)
    peak = max([c for _, c in points] + [1])
    bar = width / n
    parts = []
    for i, (label, count) in enumerate(points):
        h = (height - bottom - 2) * count / peak
        x = i * bar
        parts.append(
            f'<rect x="{x + 1:.1f}" y="{height - bottom - h:.1f}" width="{bar - 2:.1f}" height="{h:.1f}">'
            f"<title>{esc(label)}: {count}</title></rect>"
        )
        if i % 6 == 0:
            parts.append(f'<text x="{x + 1:.1f}" y="{height - 2}">{esc(label)}</text>')
    total = sum(c for _, c in points)
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" preserveAspectRatio="none"'
        f' aria-label="Visits per hour, last 24 hours: {total} in total, peak {peak}">{"".join(parts)}</svg>'
    )


# --- pages ---------------------------------------------------------------------------------------------------------


# Keys in a nightly step's summary that aren't results (per-site details, file paths)
NIGHTLY_SKIP = {"seconds", "signatures", "path", "date"}


def _nightly_value(key: str, value) -> str | None:
    label = key.replace("_", " ")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return f"{label} {value:g}"
    if isinstance(value, str):
        return f"{label} {value}"
    if isinstance(value, (list, tuple)):
        return f"{label} {len(value)}"
    if isinstance(value, dict):
        parts = [_nightly_value(k, v) for k, v in value.items() if k not in NIGHTLY_SKIP]
        parts = [x for x in parts if x]
        return f"{label}: " + ", ".join(parts) if parts else None
    return None


def nightly_steps(summary: str | None) -> list[tuple[str, str, str]] | None:
    """The nightly run's summary (JSON, one object per step) as (step, result, seconds) rows; None if it isn't JSON."""
    try:
        data = json.loads(summary or "")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    rows = []
    for step, value in data.items():
        if isinstance(value, dict):
            parts = [_nightly_value(k, v) for k, v in value.items() if k not in NIGHTLY_SKIP]
            seconds = value.get("seconds")
            rows.append((step, ", ".join(x for x in parts if x) or "-", "" if seconds is None else f"{seconds:g}"))
        elif step == "failed_steps":
            rows.append((step.replace("_", " "), ", ".join(map(str, value)) or "none", ""))
        else:
            text = _nightly_value(step, value)
            rows.append((step, text or "-", ""))
    return rows


def health(config: dict, db, now: float | None = None) -> str:
    now = time.time() if now is None else now
    last_hour = metrics.visits_last_hour(db, now)
    minimum = int((config.get("alerts") or {}).get("min_visits_per_hour") or 0)
    workers = metrics.active_workers(db, now)
    queue = metrics.queue_length(db)
    open_alerts = db.q("SELECT * FROM alerts WHERE resolved IS NULL ORDER BY started DESC")
    build = metrics.current_build(db)
    nightly = metrics.latest_nightly(db)
    last_ok = metrics.last_successful_nightly(db)
    statuses = metrics.visits_by_status(db, now - 86400)
    errors = db.one(
        "SELECT COALESCE(SUM(errors), 0), COUNT(*) FILTER (WHERE errors > 0) FROM own_visits WHERE started >= ?",
        (metrics.ts(now - 86400),),
    )

    if nightly is None:
        nightly_text, nightly_css = "never", "warn"
    else:
        nightly_text = nightly["status"] or "?"
        nightly_css = {"ok": "good", "running": ""}.get(nightly["status"], "bad")

    out = ["<h1>Health</h1>", '<div class="cards">']
    out.append(card(last_hour, "visits in the last hour", "bad" if minimum and last_hour < minimum else ""))
    out.append(card(len(workers), "active workers (15 min)"))
    out.append(card(queue, "sites queued"))
    out.append(card(nightly_text, "last nightly run", nightly_css))
    out.append(card(len(open_alerts), "open alerts", "bad" if open_alerts else "good"))
    out.append(card(errors[0], "error findings (24 h)"))
    out.append("</div>")

    out.append("<h2>Visits per hour (24 h)</h2>")
    out.append(sparkline(metrics.visits_per_hour(db, now)))

    out.append("<h2>Ladybird build</h2>")
    out.append(f'<p class="mono wrap">{esc(build)}</p>' if build else '<p class="muted">No build recorded yet.</p>')

    out.append("<h2>Open alerts</h2>")
    out.append(
        table(
            ["Rule", "Since", "Message"],
            [[esc(a["rule"]), esc(a["started"]), esc(a["message"])] for a in open_alerts],
            empty="No open alerts.",
        )
    )

    out.append("<h2>Active workers</h2>")
    out.append(
        table(
            ["Worker", "Visits (15 min)", "Last visit"],
            [[esc(w["worker"] or "(unnamed)"), esc(w["visits"]), esc(w["last"])] for w in workers],
            numeric={1},
            empty="No visits in the last 15 minutes.",
        )
    )

    out.append("<h2>Nightly run</h2>")
    if nightly is None:
        out.append('<p class="muted">No nightly run recorded.</p>')
    else:
        out.append(
            table(
                ["Run", "Started", "Finished", "Status"],
                [
                    [
                        esc(nightly["id"]),
                        esc(nightly["started"]),
                        esc(nightly["finished"]),
                        f'<span class="{nightly_css}">{esc(nightly["status"])}</span>',
                    ]
                ],
            )
        )
        steps = nightly_steps(nightly["summary"])
        if steps is None:
            out.append(f'<pre class="wrap">{esc((nightly["summary"] or "")[:500])}</pre>')
        elif steps:
            out.append(
                table(
                    ["Step", "Result", "Seconds"],
                    [[esc(name), esc(text), esc(seconds)] for name, text, seconds in steps],
                    numeric={2},
                )
            )
        out.append(f'<p class="muted">Last successful: {esc(metrics.ts(last_ok)) if last_ok else "never"}</p>')

    out.append("<h2>Visits by status (24 h)</h2>")
    out.append(
        table(
            ["Status", "Visits"],
            [[esc(s), esc(n)] for s, n in statuses.items()],
            numeric={1},
            empty="No visits in the last 24 hours.",
        )
    )
    out.append(f'<p class="muted">{esc(errors[1])} visits with error findings in the last 24 hours.</p>')
    recent = db.q(
        "SELECT v.id, v.url, v.started, v.status, v.errors, v.first_error FROM own_visits v"
        " WHERE v.started >= ? AND (v.errors > 0 OR COALESCE(v.status, '') NOT IN ('ok', 'done'))"
        " ORDER BY v.started DESC LIMIT 20",
        (metrics.ts(now - 86400),),
    )
    out.append("<h2>Recent visits with errors</h2>")
    out.append(
        table(
            ["Visit", "URL", "Status", "Errors", "First error"],
            [
                [
                    visit_link(r["id"], r["started"]),
                    f'<span class="wrap">{esc(r["url"])}</span>',
                    esc(r["status"]),
                    esc(r["errors"]),
                    f'<span class="mono wrap">{esc(r["first_error"])}</span>',
                ]
                for r in recent
            ],
            numeric={3},
        )
    )
    return page("Health", "".join(out), "/")


def _options(values: list[str], selected: str) -> str:
    opts = ['<option value="">all</option>']
    for v in values:
        opts.append(f'<option value="{esc(v)}"{" selected" if v == selected else ""}>{esc(v)}</option>')
    return "".join(opts)


def findings(config: dict, db, family: str = "", detector: str = "", limit: int = 200) -> str:
    where = ["f.category = 'error'", "(f.known IS NULL OR f.known = '')"]
    args: list = []
    if family:
        where.append("substr(f.signature, 1, ?) = ?")
        args += [len(family) + 1, family + ":"]
    if detector:
        where.append("f.detector = ?")
        args.append(detector)
    rows = db.q(
        "SELECT f.id, f.visit, f.signature, f.detector, f.text, v.url, v.started FROM findings f"
        " JOIN own_visits v ON v.id = f.visit WHERE " + " AND ".join(where) + " ORDER BY v.started DESC, f.id DESC"
        " LIMIT ?",
        args + [limit],
    )
    families = [
        r[0]
        for r in db.q(
            "SELECT DISTINCT CASE WHEN instr(signature, ':') > 0 THEN substr(signature, 1, instr(signature, ':') - 1)"
            " ELSE signature END FROM findings WHERE category = 'error' AND (known IS NULL OR known = '') ORDER BY 1"
        )
    ]
    detectors = [
        r[0]
        for r in db.q(
            "SELECT DISTINCT detector FROM findings WHERE category = 'error' AND (known IS NULL OR known = '')"
            " AND detector IS NOT NULL AND detector != '' ORDER BY 1"
        )
    ]
    out = ["<h1>Findings</h1>", '<p class="muted">Latest error findings that are not known differences.</p>']
    out.append(
        '<form class="filters" method="get" action="/findings">'
        f'<label>Family<select name="family">{_options(families, family)}</select></label>'
        f'<label>Detector<select name="detector">{_options(detectors, detector)}</select></label>'
        '<button type="submit">Filter</button></form>'
    )
    out.append(
        table(
            ["Visit", "Signature", "Detector", "Text", "URL"],
            [
                [
                    visit_link(r["visit"], r["started"] or r["visit"]),
                    f'<a class="mono wrap" href="{url_path("candidates", r["signature"])}">{esc(r["signature"])}</a>',
                    esc(r["detector"]),
                    esc((r["text"] or "")[:300]),
                    f'<span class="wrap">{esc(r["url"])}</span>',
                ]
                for r in rows
            ],
            empty="No findings match.",
        )
    )
    return page("Findings", "".join(out), "/findings")


CANDIDATE_STATUSES = ["new", "investigating", "filed", "known", "dismissed", "fixed"]


def candidates(config: dict, db, status: str = "") -> str:
    args: list = []
    sql = "SELECT * FROM candidates"
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    rows = db.q(sql + " ORDER BY sites DESC, last_seen DESC LIMIT 1000", args)
    statuses = sorted(set(CANDIDATE_STATUSES) | {r[0] for r in db.q("SELECT DISTINCT status FROM candidates")})
    links = " ".join(
        f'<a href="{url_query("/candidates", status=s)}"{" aria-current=page" if s == status else ""}>{esc(s or "all")}'
        "</a>"
        for s in [""] + statuses
    )
    out = ["<h1>Candidates</h1>", f'<nav class="tabs" aria-label="Status">{links}</nav>']
    out.append(
        table(
            ["Signature", "Sites", "Status", "Issue", "First seen", "Last seen", "Text"],
            [
                [
                    f'<a class="mono wrap" href="{url_path("candidates", r["signature"])}">{esc(r["signature"])}</a>',
                    esc(r["sites"]),
                    status_chip(r["status"]),
                    issue_link(config, r["issue"]),
                    esc(r["first_seen"]),
                    esc(r["last_seen"]),
                    esc((r["text"] or "")[:200]),
                ]
                for r in rows
            ],
            numeric={1},
            empty="No candidates.",
        )
    )
    return page("Candidates", "".join(out), "/candidates")


def screenshots(visit_id: str, directory: Path | None, step: str | None = None) -> str:
    """Screenshots side by side: per step (``load``, ``idle`` ...), one figure per browser."""
    if directory is None or not directory.is_dir():
        return '<p class="muted">The visit bundle is not available here.</p>'
    groups: dict[str, list[tuple[str, str]]] = {}
    for p in sorted(directory.iterdir()):
        m = re.match(r"^([a-z0-9]+)-([A-Za-z0-9_.-]+)\.png$", p.name)
        if not m or (step and m.group(2) != step):
            continue
        groups.setdefault(m.group(2), []).append((m.group(1), p.name))
    if not groups:
        return '<p class="muted">No screenshots.</p>'
    order = ["load", "idle", "scroll", "mobile", "reload"]
    out = []
    for name in sorted(groups, key=lambda s: (order.index(s) if s in order else len(order), s)):
        figures = "".join(
            f'<figure><a href="{url_path("visit", visit_id, "file", f)}">'
            f'<img src="{url_path("visit", visit_id, "file", f)}" alt="{esc(b)} {esc(name)}" loading="lazy"></a>'
            f"<figcaption>{esc(b)}</figcaption></figure>"
            for b, f in groups[name]
        )
        out.append(f'<h3>{esc(name)}</h3><div class="shots">{figures}</div>')
    return "".join(out)


def candidate(config: dict, db, signature: str, bundle_dir: Callable) -> str | None:
    row = db.one("SELECT * FROM candidates WHERE signature = ?", (signature,))
    sites = db.q(
        "SELECT s.domain, f.site, COUNT(*) AS n, MAX(v.started) AS last, "
        " (SELECT v2.id FROM findings f2 JOIN own_visits v2 ON v2.id = f2.visit WHERE f2.signature = f.signature"
        "  AND f2.site = f.site ORDER BY v2.started DESC LIMIT 1) AS visit"
        " FROM findings f LEFT JOIN sites s ON s.id = f.site LEFT JOIN own_visits v ON v.id = f.visit"
        " WHERE f.signature = ? GROUP BY f.site ORDER BY last DESC LIMIT 500",
        (signature,),
    )
    if row is None and not sites:
        return None
    visits = db.q(
        "SELECT v.*, f.text AS finding FROM findings f JOIN own_visits v ON v.id = f.visit WHERE f.signature = ?"
        " ORDER BY v.started DESC LIMIT 20",
        (signature,),
    )
    out = [f'<h1 class="mono wrap">{esc(signature)}</h1>']
    if row is not None:
        out.append(
            table(
                ["Sites", "Status", "Issue", "First seen", "Last seen"],
                [
                    [
                        esc(row["sites"]),
                        esc(row["status"]),
                        issue_link(config, row["issue"]),
                        esc(row["first_seen"]),
                        esc(row["last_seen"]),
                    ]
                ],
            )
        )
        if row["text"]:
            out.append(f"<pre>{esc(row['text'])}</pre>")
        if row["note"]:
            out.append(f"<p>{esc(row['note'])}</p>")
    if visits:
        newest = visits[0]
        out.append(f"<h2>Newest visit: {visit_link(newest['id'], newest['started'])}</h2>")
        out.append(f'<p class="wrap">{esc(newest["url"])}</p>')
        out.append(screenshots(newest["id"], bundle_dir(newest), "load"))
    out.append(f"<h2>Sites ({len(sites)})</h2>")
    out.append(
        table(
            ["Site", "Findings", "Last seen", "Latest visit"],
            [
                [
                    esc(s["domain"] or s["site"]),
                    esc(s["n"]),
                    esc(s["last"]),
                    visit_link(s["visit"]) if s["visit"] else "",
                ]
                for s in sites
            ],
            numeric={1},
        )
    )
    out.append("<h2>Latest visits</h2>")
    out.append(
        table(
            ["Visit", "URL", "Build", "Text"],
            [
                [
                    visit_link(v["id"], v["started"]),
                    f'<span class="wrap">{esc(v["url"])}</span>',
                    f'<span class="mono">{esc((v["build"] or "")[:12])}</span>',
                    esc((v["finding"] or "")[:200]),
                ]
                for v in visits
            ],
        )
    )
    return page(signature, "".join(out), "/candidates")


def _console_errors(directory: Path | None, limit: int = 200) -> list[dict]:
    if directory is None:
        return []
    p = directory / "events.jsonl"
    if not p.is_file():
        return []
    out = []
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            if e.get("level") == "error" or e.get("type") == "javascript":
                out.append(e)
                if len(out) >= limit:
                    break
    return out


def _kv_table(data: dict) -> str:
    rows = []
    for k, v in data.items():
        if isinstance(v, dict):
            v = ", ".join(f"{bk}: {bv}" for bk, bv in v.items())
        rows.append([esc(k), esc(v)])
    return table(["Step", "Seconds"], rows, numeric={1})


def visit(config: dict, db, row, directory: Path | None) -> str:
    data: dict = {}
    if directory is not None and (directory / "visit.json").is_file():
        try:
            data = json.loads((directory / "visit.json").read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    site = db.one("SELECT domain FROM sites WHERE id = ?", (row["site"],))
    out = [f"<h1>Visit <span class=mono>{esc(row['id'])}</span></h1>"]
    out.append(
        table(
            ["", ""],
            [
                ["URL", f'<span class="wrap">{esc(row["url"])}</span>'],
                ["Site", esc(site["domain"] if site else row["site"])],
                ["Started", esc(row["started"])],
                ["Seconds", esc(row["seconds"])],
                ["Status", esc(row["status"])],
                ["Worker", esc(row["worker"])],
                ["Build", f'<span class="mono wrap">{esc(row["build"])}</span>'],
                ["Browsers", esc(row["browsers"])],
                ["Errors / infos", f"{esc(row['errors'])} / {esc(row['infos'])}"],
            ],
        )
    )
    if data.get("timings"):
        out.append("<h2>Timings</h2>")
        out.append(_kv_table(data["timings"]))
    if data.get("steps"):
        out.append("<h2>Steps per browser</h2>")
        out.append(_kv_table(data["steps"]))

    found = db.q("SELECT * FROM findings WHERE visit = ? ORDER BY category, signature", (row["id"],))
    out.append(f"<h2>Findings ({len(found)})</h2>")
    out.append(
        table(
            ["Category", "Signature", "Detector", "Text", "Known"],
            [
                [
                    f'<span class="{"bad" if f["category"] == "error" and not f["known"] else ""}">'
                    f"{esc(f['category'])}</span>",
                    f'<a class="mono wrap" href="{url_path("candidates", f["signature"])}">{esc(f["signature"])}</a>',
                    esc(f["detector"]),
                    esc((f["text"] or "")[:500]),
                    esc(f["known"]),
                ]
                for f in found
            ],
        )
    )
    out.append("<h2>Screenshots</h2>")
    out.append(screenshots(row["id"], directory))

    errors = _console_errors(directory)
    out.append(f"<h2>Console errors ({len(errors)})</h2>")
    out.append(
        table(
            ["Browser", "Step", "Frame", "Text"],
            [
                [
                    esc(e.get("browser")),
                    esc(e.get("step")),
                    "top" if e.get("top") else "sub",
                    f"<pre>{esc(str(e.get('text') or '')[:2000])}"
                    + (("\n" + esc("\n".join(map(str, e["stack"])))) if isinstance(e.get("stack"), list) else "")
                    + "</pre>",
                ]
                for e in errors
            ],
            empty="No console errors.",
        )
    )
    if directory is not None and directory.is_dir():
        from webparity.web.server import FILE_TYPES

        files = [p.name for p in sorted(directory.iterdir()) if p.is_file() and p.suffix.lower() in FILE_TYPES]
        out.append("<h2>Files</h2><p>")
        out.append(
            " · ".join(f'<a class="mono" href="{url_path("visit", row["id"], "file", n)}">{esc(n)}</a>' for n in files)
        )
        out.append("</p>")
    return page(f"Visit {row['id']}", "".join(out))


ISSUE_STATES = ["draft", "filed", "closed", "verified"]


def issues(config: dict, db) -> str:
    rows = db.q(
        "SELECT * FROM issues ORDER BY CASE state WHEN 'draft' THEN 0 WHEN 'filed' THEN 1 WHEN 'closed' THEN 2"
        " ELSE 3 END, COALESCE(filed, created) DESC"
    )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    out = ["<h1>Issues</h1>", '<div class="cards">']
    for s in ISSUE_STATES + sorted(set(counts) - set(ISSUE_STATES)):
        out.append(card(counts.get(s, 0), s))
    out.append("</div><h2>All</h2>")
    out.append(
        table(
            ["Issue", "Title", "Kind", "State", "Signatures", "Created", "Filed", "Closed", "Verified"],
            [
                [
                    issue_link(config, r["number"]) or '<span class="muted">draft</span>',
                    esc(r["title"]),
                    esc(r["kind"]),
                    esc(r["state"]),
                    "<br>".join(
                        f'<a class="mono wrap" href="{url_path("candidates", s)}">{esc(s)}</a>'
                        for s in _signatures(r["signatures"])
                    ),
                    esc(r["created"]),
                    esc(r["filed"]),
                    esc(r["closed"]),
                    esc(" ".join(x for x in (r["verified"], r["verified_result"]) if x)),
                ]
                for r in rows
            ],
            empty="No issues yet.",
        )
    )
    return page("Issues", "".join(out), "/issues")


def _signatures(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(s) for s in parsed]
    except ValueError:
        pass
    return [s.strip() for s in re.split(r"[\n,]", value) if s.strip()]


REVIEW_CSS = """
main.review{max-width:1400px}
.review-wrap{display:grid;grid-template-columns:280px minmax(0,1fr);gap:22px}
@media (max-width:860px){.review-wrap{grid-template-columns:minmax(0,1fr)}.queue{position:static!important;max-height:none!important}}
.queue{position:sticky;top:68px;align-self:start;max-height:calc(100vh - 84px);overflow:auto;background:var(--panel);
border:1px solid var(--line);border-radius:10px;padding:14px 10px}
.queue h1{font:600 .74em/1.3 var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin:0 8px 6px}
.queue p{margin:0 8px 12px}
.queue ol{list-style:none;margin:0;padding:0;display:grid;gap:2px}
.queue button{width:100%;font:inherit;font-size:.9em;text-align:left;display:grid;grid-template-columns:24px minmax(0,1fr);gap:8px;align-items:start;
border:1px solid transparent;background:none;color:var(--fg);font-size:.9em;padding:7px 8px;border-radius:6px;cursor:pointer}
.queue button:hover{background:var(--card)}.queue button[aria-current=true]{background:var(--accent-soft);border-color:var(--accent)}
.queue .nr{font-family:ui-monospace,Menlo,monospace;color:var(--muted);font-size:.85em;padding-top:2px}
.queue .name{overflow-wrap:anywhere}
.chip{display:inline-block;font:500 .68em/1.6 var(--mono);letter-spacing:.08em;text-transform:uppercase;padding:0 7px;
border-radius:999px;background:var(--card);border:1px solid var(--line);color:var(--muted);margin-top:4px}
.chip.changed{color:var(--warn)}.chip.done{color:var(--good)}
#view{display:grid;gap:16px;align-content:start;min-width:0}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 20px;min-width:0}
.top{display:grid;gap:10px}
.meta,.actions,.post{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.meta{color:var(--muted);font-size:.9em}
.button{display:inline-flex;align-items:center;font-size:.92em;font-weight:500;padding:6px 12px;border-radius:6px;
border:1px solid var(--line);background:var(--bg);color:var(--fg);cursor:pointer;text-decoration:none}
.button:hover{border-color:var(--muted);text-decoration:none}
.button.primary{background:var(--accent);border-color:var(--accent);color:var(--head);font-weight:600}
.button.small{font-size:.82em;padding:2px 8px;font-weight:400}
.button[disabled]{opacity:.45;cursor:default}
.note{font-size:.88em;color:var(--muted);margin:0}.note.warn{color:var(--warn)}
.images{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media (max-width:700px){.images{grid-template-columns:minmax(0,1fr)}}
.images figure{margin:0;display:grid;gap:6px;min-width:0}
.images img{width:100%;border:1px solid var(--line);border-radius:4px;cursor:zoom-in}
.images figcaption{font-size:.85em;color:var(--muted);display:flex;flex-wrap:wrap;gap:6px;justify-content:space-between}
details.background summary{cursor:pointer;font-weight:500}
details.background pre{margin:10px 0 0;color:var(--muted)}
.fields{display:grid;gap:14px}
.f{display:grid;gap:4px;min-width:0}
.f .row{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.f label{font-weight:600;font-size:.92em}
.f input:focus,.f textarea:focus{outline:2px solid var(--accent);outline-offset:1px}
.f .id{font-family:ui-monospace,Menlo,monospace;font-size:.75em;color:var(--muted)}
.f input,.f textarea{width:100%;font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:7px 9px}
.f textarea{resize:vertical;min-height:60px}
.f .code{font-family:ui-monospace,Menlo,monospace;font-size:.85em}
.f.title input{font-size:1.1em;font-weight:600}
.narrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}
.post input{width:100px}
.empty{color:var(--muted);padding:30px 0}
.zoom{position:fixed;inset:0;background:rgba(10,12,16,.85);display:grid;place-items:center;z-index:20;padding:16px;cursor:zoom-out}
.zoom img{max-width:100%;max-height:100%}
[hidden]{display:none!important}
"""


def review(config: dict, db) -> str:
    """The review page's shell; static/review.js loads the drafts from /api/review and does the rest."""
    repo = (config.get("github") or {}).get("repository") or "LadybirdBrowser/ladybird"
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo):
        repo = "LadybirdBrowser/ladybird"
    body = (
        f'<style>{REVIEW_CSS}</style><div id="review" class="review-wrap" data-repository="{esc(repo)}">'
        '<nav class="queue" aria-label="Filing queue"><h1>Review</h1>'
        '<p class="note" id="queue-summary">Loading the drafts …</p><ol id="queue"></ol></nav>'
        '<div id="view"><noscript><p class="empty">The review page needs JavaScript.</p></noscript></div></div>'
        '<div class="zoom" id="zoom" hidden><img id="zoom-img" alt=""></div>'
        '<script src="/static/review.js" defer></script>'
    )
    return page("Review", body, "/review").replace("<main>", '<main class="review">', 1)
