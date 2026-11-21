# SPDX-License-Identifier: BSD-2-Clause
"""Issue drafts: ``create_draft`` writes a Markdown draft from what the database knows about a signature, and
``check_draft`` lists what keeps a draft from being ready for a person to review and file.

A draft is ``data_dir/drafts/<slug>.md``: a front matter block (signatures, test case, kind), then the fields of
Ladybird's bug report form in order, then a notes section that never goes into the issue. Nothing here talks to GitHub.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from webparity.db import DB, now
from webparity.issues.common import (
    NOTES_HEADING,
    SOURCE_HEADING,
    data_dir,
    draft_title,
    dump_signatures,
    issue_part,
    join_front_matter,
    resolve_path,
    section,
    split_front_matter,
    test_case_files,
)

TODO = "TODO"

TEMPLATE = """\
## Title
{title}

## Summary
{TODO}: one or two sentences: what Ladybird does, what Chromium and Firefox do, and the visible effect on sites.

## Operating system
{TODO}

## Commit hash
{build}

## Steps to reproduce
1. Open the test case below.

## Expected behavior
As in Chromium (and Firefox):
```
{TODO}: the test case's output in Chromium
```

## Actual behavior
Ladybird:
```
{TODO}: the test case's output in Ladybird
```

## URL for a reduced test case
N/A

## {source_heading}
{TODO}: paste the test case ({test_case}) here, without external URLs. It should write its result into
`<pre id="out">` so it can be verified automatically.

## Log output and (if possible) backtrace
N/A

## {notes_heading}
Signature: `{signature}`
Finding: {text}
Seen on {sites} site(s), first {first_seen}, last {last_seen}. Examples: {examples}.
Latest Ladybird build seen: {build}.
Searched for existing issues: {TODO} (search terms, date).
"""


def slugify(text: str, limit: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "draft"


def _unique_path(folder: Path, slug: str) -> Path:
    path, n = folder / f"{slug}.md", 2
    while path.exists():
        path, n = folder / f"{slug}-{n}.md", n + 1
    return path


def create_draft(config: dict, db: DB, signature: str, title: str | None = None, kind: str = "issue") -> Path:
    """Writes ``data_dir/drafts/<slug>.md`` for ``signature`` and inserts an ``issues`` row in state ``draft``.

    The candidate's status moves from ``new`` to ``investigating`` and its ``issue`` column points at the draft.
    """
    candidate = db.one("SELECT * FROM candidates WHERE signature = ?", (signature,))
    finding = db.one("SELECT * FROM findings WHERE signature = ? ORDER BY id DESC LIMIT 1", (signature,))
    examples = [
        r["domain"]
        for r in db.q(
            "SELECT s.domain, COUNT(*) AS n FROM findings f JOIN sites s ON s.id = f.site WHERE f.signature = ?"
            " GROUP BY s.domain ORDER BY n DESC, s.domain LIMIT 5",
            (signature,),
        )
    ]
    build_row = db.one(
        "SELECT v.build FROM visits v JOIN findings f ON f.visit = v.id WHERE f.signature = ? AND v.build IS NOT NULL"
        " ORDER BY v.started DESC LIMIT 1",
        (signature,),
    ) or db.one("SELECT build FROM visits WHERE build IS NOT NULL ORDER BY started DESC LIMIT 1")
    build = build_row["build"] if build_row else TODO
    text = (candidate["text"] if candidate else None) or (finding["text"] if finding else None) or signature
    title = (title or "").strip() or f"{TODO}: {text}"[:99]
    slug = slugify(title if not title.startswith(TODO) else signature)

    folder = data_dir(config) / "drafts"
    folder.mkdir(parents=True, exist_ok=True)
    path = _unique_path(folder, slug)
    test_case = f"test-cases/{path.stem}/"
    body = TEMPLATE.format(
        TODO=TODO,
        title=title,
        build=build,
        test_case=test_case,
        source_heading=SOURCE_HEADING,
        notes_heading=NOTES_HEADING,
        signature=signature,
        text=text,
        sites=candidate["sites"] if candidate else len(examples),
        first_seen=(candidate["first_seen"] if candidate else None) or "?",
        last_seen=(candidate["last_seen"] if candidate else None) or "?",
        examples=", ".join(examples) or "none recorded",
    )
    meta = {"signatures": [signature], "test_case": test_case, "kind": kind, "created": now()}
    path.write_text(join_front_matter(meta, "\n" + body), encoding="utf-8")

    db.x(
        "INSERT INTO issues (draft, title, kind, state, signatures, test_case, created)"
        " VALUES (?, ?, ?, 'draft', ?, ?, ?)",
        (str(path), title, kind, dump_signatures([signature]), test_case, now()),
    )
    if candidate is not None:
        db.x(
            "UPDATE candidates SET status = CASE WHEN status = 'new' THEN 'investigating' ELSE status END,"
            " issue = COALESCE(issue, ?) WHERE signature = ?",
            (f"draft:{path.stem}", signature),
        )
    return path


# --- checks ----------------------------------------------------------------------------------------------------------
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.I)
LOCAL_PATH = re.compile(r"(?:/Users/|/home/|/root/|[A-Za-z]:\\Users\\|~/)[^\s`'\")]*")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
ALLOWED_IPS = {"127.0.0.1", "0.0.0.0", "255.255.255.255"}
URL = re.compile(r"(?:https?:)?//[^\s\"'<>()`]+", re.I)
# URLs that are names, not fetches (XML namespaces) or the probe's own local server
HARMLESS_URL = re.compile(r"^(?:https?:)?//(?:www\.w3\.org/|127\.0\.0\.1[:/]|localhost[:/]|\[::1\])", re.I)
SWEDISH = re.compile(r"[åäöÅÄÖ]")
TEXT_SUFFIXES = {".html", ".htm", ".xhtml", ".svg", ".js", ".mjs", ".css", ".json", ".xml", ".txt", ".vtt"}


def _personal(text: str, where: str) -> list[str]:
    problems = []
    for m in sorted(set(EMAIL.findall(text))):
        problems.append(f"{where}: contains an email address ({m})")
    for m in sorted(set(LOCAL_PATH.findall(text))):
        problems.append(f"{where}: contains a local path ({m})")
    for m in sorted(set(IPV4.findall(text))):
        if m not in ALLOWED_IPS and all(0 <= int(p) <= 255 for p in m.split(".")):
            problems.append(f"{where}: contains an IP address ({m})")
    return problems


def _external_urls(text: str) -> list[str]:
    urls = []
    for m in URL.finditer(text):
        url = m.group(0)
        start = m.start()
        # "//" inside a JS comment or a regex is not a URL: require a host-like part
        if not re.match(r"^(?:https?:)?//[\w-]+(?:\.[\w-]+)+", url) or HARMLESS_URL.match(url):
            continue
        if url.startswith("//") and start > 0 and text[start - 1] not in "\"'(=":
            continue
        urls.append(url)
    return sorted(set(urls))


def check_draft(path: str | Path) -> list[str]:
    """What is still wrong with a draft; an empty list means it is ready for a person's review."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    meta, _ = split_front_matter(text)
    issue = issue_part(text)
    problems: list[str] = []

    title = draft_title(issue)
    if not title:
        problems.append("no title (a '## Title' section with one line)")
    else:
        if len(title) >= 100:
            problems.append(f"title is {len(title)} characters (keep it under 100)")
        if TODO in title:
            problems.append("title still has a TODO")

    steps = section(issue, "Steps to reproduce").strip()
    if not steps or TODO in steps:
        problems.append("no steps to reproduce")
    if TODO in issue:
        problems.append(f"the issue text still has {issue.count(TODO)} TODO placeholder(s)")
    if SWEDISH.search(issue):
        problems.append("the issue text has å/ä/ö: write it in English")
    problems += _personal(issue, "issue text")

    kind = meta.get("kind", "issue")
    test_case = resolve_path(meta.get("test_case"), path.parent, path.parent.parent, path.parent.parent.parent)
    if kind == "issue":
        if test_case is None:
            problems.append("no test case in the front matter (test_case: ...)")
        elif not test_case_files(test_case):
            problems.append(f"the test case {meta.get('test_case')} does not exist")
    if test_case is not None:
        for f in test_case_files(test_case):
            if f.suffix.lower() not in TEXT_SUFFIXES:
                continue
            content = f.read_text(encoding="utf-8", errors="replace")
            for url in _external_urls(content):
                problems.append(f"test case {f.name}: external URL {url}")
            problems += _personal(content, f"test case {f.name}")
    source = section(issue, SOURCE_HEADING)
    for url in _external_urls(source):
        problems.append(f"test case source in the draft: external URL {url}")
    if kind == "issue" and not meta.get("signatures"):
        problems.append("no signatures in the front matter")
    return list(dict.fromkeys(problems))
