# SPDX-License-Identifier: BSD-2-Clause
"""Imports issues filed before webparity from a gallery directory, so tracking starts from today's state.

The gallery layout this reads: ``issues/INDEX.md`` with a table row per filed issue
(``| [#N](url) | filed | status | title | [draft.md](draft.md) |``) and a comments table
(``| [#N](url) | [draft.md](draft.md) … posted … |``), and ``issues/<draft>.md`` drafts in the bug report form layout
with the test case under "HTML/SVG/etc. source for a reduced test case" (several files separated by
``<!-- name.ext -->`` lines), or a ``testfall/...`` path mentioned in the draft.

The filing queue (a section whose heading starts with "Postkö" or "Queue", rows ``| pos | class | [draft.md](draft.md) |
effect |``) and comments marked as not yet posted ("utkast", "ej postad", "draft") come in as rows in state
``draft``, so they show on the review page; when a
queued draft is later filed or posted, the same row is updated, and a queued draft that left the queue without being
filed gets state ``withdrawn``.
Drafts are copied to ``data_dir/drafts/imported/`` and test cases to ``data_dir/test-cases/imported/<N>/`` (a queued
draft's under its file name), so the data directory is self-contained. Running it again updates the same rows.
This works on the user's local data only; nothing it reads belongs in the repository.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from webparity.db import DB, now
from webparity.issues.common import (
    SOURCE_HEADING,
    data_dir,
    draft_title,
    dump_signatures,
    join_front_matter,
    section,
    split_front_matter,
    write_source,
)

ISSUE_ROW = re.compile(r"^\| \[#(\d+)\]\([^)]*\) \| ([^|]*) \| ([^|]*) \| (.*) \| \[([^\]]+\.md)\]")
QUEUE_ROW = re.compile(r"^\| (\d+) \| (\d+) \| \[([^\]]+\.md)\]\([^)]*\) \| (.*?) \|\s*$")
QUEUE_NOTE = "gallery queue"
# A comment row counts as waiting only when it says so; older rows without a mark are left alone
UNPOSTED = re.compile(r"(?i)\butkast\b|\bej postad\b|\bdraft\b|\bnot posted\b")
STALE = re.compile(r"(?i)\binaktuell\b|\bwithdrawn\b|\bstale\b")
COMMENT_ROW = re.compile(r"^\| \[#(\d+)\]\([^)]*\) \| \[([^\]]+\.md)\]\([^)]*\)(.*)$")
POSTED = re.compile(r"(?<!ej )(?<!not )\b(posted|postad)\b[^0-9]*(\d{4}-\d{2}-\d{2})?", re.I)
IMAGE_FILE = re.compile(r"\b([\w.-]+\.(?:png|jpe?g|webp))\b", re.I)
TEST_CASE_MENTION = re.compile(r"\btestfall/[\w./-]+")


def parse_status(status: str) -> dict:
    """State, closed date, closed_by and verification from a gallery status cell."""
    out: dict = {"state": "filed", "closed": None, "closed_by": None, "verified": None, "verified_result": None}
    m = re.match(r"\s*closed \(([^)]*)\)\s*(\d{4}-\d{2}-\d{2})?", status)
    if m:
        reason = m.group(1).strip().replace(" ", "_")
        out["state"] = {"not_planned": "not-planned", "duplicate": "duplicate"}.get(reason, "closed")
        out["closed"] = m.group(2)
    if out["state"] != "filed":
        pr = re.search(r"(?:fixed by |PR \[?)#(\d+)", status)
        commit = re.search(r"(?:rättad i |\()([0-9a-f]{10,12})\b", status)
        if pr:
            out["closed_by"] = f"#{pr.group(1)}"
        elif commit:
            out["closed_by"] = f"commit {commit.group(1)}"
    v = re.search(r"verifierad[^0-9]*?(\d{4}-\d{2}-\d{2})?", status)
    if v and "fortfarande" not in status:
        dates = re.findall(r"\((\d{4}-\d{2}-\d{2})", status[v.start() :]) or re.findall(
            r"(\d{4}-\d{2}-\d{2})", status[v.start() :]
        )
        out["verified_result"] = "fixed"
        out["verified"] = dates[0] if dates else None
    elif "fortfarande" in status:  # "rättad i X men testfallet ger fortfarande felet"
        out["verified_result"] = "not-fixed"
    return out


def _test_case(gallery: Path, draft_text: str, number: int, target_root: Path) -> str | None:
    """Writes the draft's test case into ``target_root/<N>/`` and returns that path relative to the data directory."""
    folder = target_root / str(number)
    source = section(draft_text, SOURCE_HEADING)
    source = "\n".join(line for line in source.splitlines() if not line.startswith("```")).strip()
    if source and source.upper() not in ("N/A", "NA", "(LEAVE EMPTY)"):
        if folder.exists():
            shutil.rmtree(folder)
        write_source(folder, source)
        return folder.as_posix()
    for mention in TEST_CASE_MENTION.findall(draft_text):
        path = gallery / mention.rstrip(".,/")
        if path.is_file() or (path.is_dir() and path.parent.name == "testfall"):
            if folder.exists():
                shutil.rmtree(folder)
            if path.is_dir():
                shutil.copytree(path, folder)
                return folder.as_posix()
            folder.mkdir(parents=True)
            shutil.copy2(path, folder / path.name)
            return (folder / path.name).as_posix()
    return None


def _relative(config: dict, path: str | None) -> str | None:
    if path is None:
        return None
    try:
        return Path(path).relative_to(data_dir(config)).as_posix()
    except ValueError:
        return path


def _copy_draft(config: dict, gallery_draft: Path, meta: dict) -> Path:
    folder = data_dir(config) / "drafts" / "imported"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / gallery_draft.name
    _, body = split_front_matter(gallery_draft.read_text(encoding="utf-8"))
    target.write_text(join_front_matter(meta, body), encoding="utf-8")
    return target


def import_gallery(config: dict, db: DB, gallery_dir: str | Path) -> dict:
    """Imports filed issues and posted comments from ``gallery_dir``. Returns counts:
    ``{"issues": n, "comments": n, "test_cases": n, "missing_drafts": [...]}``."""
    gallery = Path(gallery_dir).expanduser()
    index = gallery / "issues" / "INDEX.md"
    if not index.is_file():
        raise FileNotFoundError(f"no issues/INDEX.md in {gallery}")
    cases_root = data_dir(config) / "test-cases" / "imported"
    counts: dict = {"issues": 0, "comments": 0, "queued": 0, "test_cases": 0, "missing_drafts": []}
    queued: set[str] = set()
    section_name = ""
    for line in index.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section_name = line[3:].strip().lower()
        m = ISSUE_ROW.match(line)
        if m and not section_name:
            number, filed, status, title, name = m.groups()
            _import_issue(config, db, gallery, cases_root, int(number), filed.strip(), status, title, name, counts)
            continue
        q = QUEUE_ROW.match(line)
        if q and section_name.startswith(("postkö", "queue", "filing queue")):
            position, issue_class, name, effect = q.groups()
            note = f"{QUEUE_NOTE} #{position}, class {issue_class}: {effect.strip()}"[:500]
            _import_queued(config, db, gallery, cases_root, name, "issue", None, note, counts, queued)
            continue
        c = COMMENT_ROW.match(line)
        if c and section_name.startswith(("kommentar", "comment")):
            posted = POSTED.search(c.group(3))  # "ej postad" is not posted; "utkast … – POSTAD" is
            unposted = not posted and UNPOSTED.search(c.group(3))
            if posted:
                _import_comment(config, db, gallery, int(c.group(1)), c.group(2), posted.group(2), counts)
            elif unposted and not STALE.search(c.group(3)):
                note = f"{QUEUE_NOTE}: comment on #{c.group(1)}"
                _import_queued(
                    config, db, gallery, cases_root, c.group(2), "comment", int(c.group(1)), note, counts, queued
                )
    # Queued drafts that left the queue without being filed or posted
    for row in db.q("SELECT id, draft FROM issues WHERE state = 'draft' AND note LIKE ?", (f"{QUEUE_NOTE}%",)):
        if row["draft"] not in queued:
            db.x("UPDATE issues SET state = 'withdrawn' WHERE id = ?", (row["id"],))
    return counts


def _import_queued(config, db, gallery, cases_root, draft_name, kind, comment_on, note, counts, queued) -> None:
    """A draft waiting in the gallery's filing queue (or a comment not yet posted): an ``issues`` row in state draft."""
    gallery_draft = gallery / "issues" / draft_name
    if not gallery_draft.is_file():
        counts["missing_drafts"].append(draft_name)
        return
    text = gallery_draft.read_text(encoding="utf-8")
    test_case = None
    if kind == "issue":
        test_case = _relative(config, _test_case(gallery, text, Path(draft_name).stem, cases_root))
        counts["test_cases"] += test_case is not None
    meta = {"kind": kind, "test_case": test_case or ""}
    if comment_on:
        meta["comment_on"] = comment_on
    target = _copy_draft(config, gallery_draft, meta)
    draft = str(target)
    queued.add(draft)
    for image in sorted(set(IMAGE_FILE.findall(text))):  # screenshots, for the review page
        source = gallery / "issues" / image
        dest = target.parent / image
        if source.is_file() and (not dest.exists() or dest.stat().st_mtime < source.stat().st_mtime):
            shutil.copy2(source, target.parent / image)
    title = draft_title(text) or (f"Comment on #{comment_on}" if comment_on else Path(draft_name).stem)
    row = db.one("SELECT id, state FROM issues WHERE draft = ?", (draft,))
    if row is None:
        db.x(
            "INSERT INTO issues (draft, title, kind, state, test_case, note, target, created)"
            " VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)",
            (draft, title, kind, test_case, note, comment_on, now()),
        )
    elif row["state"] in ("draft", "withdrawn"):
        db.x(
            "UPDATE issues SET title = ?, kind = ?, state = 'draft', test_case = ?, note = ?, target = ? WHERE id = ?",
            (title, kind, test_case, note, comment_on, row["id"]),
        )
    counts["queued"] += 1


def _import_issue(config, db, gallery, cases_root, number, filed, status, title, draft_name, counts) -> None:
    title = title.strip().replace("\\|", "|")
    info = parse_status(status)
    gallery_draft = gallery / "issues" / draft_name
    text = gallery_draft.read_text(encoding="utf-8") if gallery_draft.is_file() else ""
    if not text:
        counts["missing_drafts"].append(draft_name)
    test_case = _relative(config, _test_case(gallery, text, number, cases_root)) if text else None
    counts["test_cases"] += test_case is not None
    signatures = [r["signature"] for r in db.q("SELECT signature FROM candidates WHERE issue = ?", (f"#{number}",))]
    meta = {"signatures": signatures, "test_case": test_case or "", "kind": "issue", "issue": number, "filed": filed}
    draft = str(_copy_draft(config, gallery_draft, meta)) if text else None
    row = db.one("SELECT id FROM issues WHERE number = ?", (number,)) or (
        db.one("SELECT id FROM issues WHERE draft = ? AND number IS NULL", (draft,)) if draft else None
    )
    values = (
        draft,
        title,
        info["state"],
        dump_signatures(signatures),
        test_case,
        filed or None,
        info["closed"],
        info["closed_by"],
        info["verified"],
        info["verified_result"],
    )
    if row is None:
        db.x(
            "INSERT INTO issues (draft, title, state, signatures, test_case, filed, closed, closed_by, verified,"
            " verified_result, number, kind, created, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'issue', ?,"
            " 'imported')",
            (*values, number, now()),
        )
    else:
        db.x(
            "UPDATE issues SET draft = ?, title = ?, state = ?, signatures = ?, test_case = ?, filed = ?, closed = ?,"
            " closed_by = ?, verified = ?, verified_result = ?, number = ?, note = 'imported' WHERE id = ?",
            (*values, number, row["id"]),
        )
    counts["issues"] += 1


def _import_comment(config, db, gallery, number, draft_name, posted, counts) -> None:
    gallery_draft = gallery / "issues" / draft_name
    if not gallery_draft.is_file():
        counts["missing_drafts"].append(draft_name)
        return
    draft = str(_copy_draft(config, gallery_draft, {"kind": "comment", "comment_on": number, "posted": posted or ""}))
    note = f"comment on #{number}"
    row = db.one("SELECT id, state FROM issues WHERE draft = ?", (draft,))
    if row is None:
        db.x(
            "INSERT INTO issues (draft, kind, state, filed, note, created) VALUES (?, 'comment', 'posted', ?, ?, ?)",
            (draft, posted, note, now()),
        )
    elif row["state"] in ("draft", "withdrawn"):
        db.x("UPDATE issues SET state = 'posted', filed = ?, note = ? WHERE id = ?", (posted, note, row["id"]))
    counts["comments"] += 1
