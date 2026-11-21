# SPDX-License-Identifier: BSD-2-Clause
"""The review page's data: drafts waiting to be filed, their GitHub form fields, edits, and marking them as filed.

A draft is a Markdown file: an internal note before a ``---`` line, then one ``## Heading`` per field of Ladybird's
bug report form, in the form's order (a comment draft has its text after the ``---`` instead). The page shows one
draft at a time with every field editable; edits are stored in ``issues.fields`` (JSON) and never written back to the
draft file, so "reset" returns to the file's text. Marking a draft as filed records the issue number (state
``filed``; a comment becomes ``posted``) and can be undone until the draft is registered from its source.

Screenshots: ``name.png (caption)`` in the Screenshots field. The image files are copied next to the draft when it is
imported, and the field itself becomes one caption line per image, ready to paste above the dragged-in images
(GitHub resets prefilled fields when they are edited, so the link leaves that field empty).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from webparity.db import DB, now
from webparity.issues.common import split_front_matter, today

# The bug report form (bug_report.yml): field id, heading in the draft, kind (input, input-code, text, code), group
FIELDS = [
    ("title", "Title", "input", ""),
    ("summary", "Summary", "text", ""),
    ("os", "Operating system", "input", "narrow"),
    ("distro-package", "Distro/package (if Linux)", "input", "narrow"),
    ("architecture", "Architecture", "input", "narrow"),
    ("commit", "Commit hash", "input-code", "narrow"),
    ("reproduction-steps", "Steps to reproduce", "text", ""),
    ("expected-behavior", "Expected behavior", "code", ""),
    ("actual-behavior", "Actual behavior", "code", ""),
    ("reduced-test-case-url", "URL for a reduced test case", "input", ""),
    ("reduced-test-case-source", "HTML/SVG/etc. source for a reduced test case", "code", ""),
    ("log-output", "Log output and (if possible) backtrace", "code", ""),
    ("screenshots", "Screenshots or screen recordings", "text", ""),
    ("build-flags", "Build flags or config settings", "text", ""),
]
COMMENT_FIELDS = [("body", "Comment", "code", "")]
HEADINGS = {heading: field_id for field_id, heading, _, _ in FIELDS}
IMAGE = re.compile(r"([\w.-]+\.(?:png|jpe?g|webp))(?:\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\))?", re.I)
QUEUE_POSITION = re.compile(r"^gallery queue #(\d+)")


def parse_draft(text: str, kind: str = "issue") -> dict:
    """``{"note", "fields", "images", "unknown"}`` from a draft's text (front matter, if any, is skipped)."""
    _, body = split_front_matter(text)
    head, sep, rest = body.partition("\n---\n")
    if not sep:
        head, rest = "", body
    note = re.sub(r"^.*?NOTE \(not for the (?:issue|comment)\):\s*", "", head, flags=re.S).strip()
    if kind == "comment":
        fields = {"body": rest.strip()}
        images = [
            {"file": f, "text": "Chromium" if f.endswith("-cr.png") else "Ladybird" if f.endswith("-lb.png") else ""}
            for f in sorted(set(m.group(1) for m in IMAGE.finditer(rest)))
        ]
        return {"note": note, "fields": fields, "images": images, "unknown": []}
    fields, unknown = {}, []
    for m in re.finditer(r"^## (.+?)\n(.*?)(?=^## |\Z)", rest, re.S | re.M):
        field_id = HEADINGS.get(m.group(1).strip())
        if field_id is None:
            unknown.append(m.group(1).strip())
            continue
        fields[field_id] = m.group(2).strip()
    images = [
        {"file": m.group(1), "text": (m.group(2) or "").strip()} for m in IMAGE.finditer(fields.get("screenshots", ""))
    ]
    if images:  # one caption line per image, to paste above the dragged-in images
        fields["screenshots"] = "\n\n".join((x["text"] or x["file"]) + ":" for x in images)
    return {"note": note, "fields": fields, "images": images, "unknown": unknown}


def draft_path(config: dict, draft: str | None) -> Path | None:
    """The draft's file, only if it is a file under ``data_dir/drafts`` (a draft path elsewhere is never read)."""
    if not draft:
        return None
    root = (Path(config["data_dir"]) / "drafts").resolve()
    p = Path(draft)
    p = (p if p.is_absolute() else root / p).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        return None
    return p


def _edits(row) -> dict:
    try:
        value = json.loads(row["fields"] or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _comment_on(row) -> int | None:
    if row["target"]:
        return int(row["target"])
    m = re.search(r"comment on #(\d+)", row["note"] or "")
    return int(m.group(1)) if m else None


def _position(row) -> int:
    m = QUEUE_POSITION.match(row["note"] or "")
    return int(m.group(1)) if m else 10_000


def item(config: dict, row) -> dict:
    """One draft for the page: the file's fields, the edited fields, images, note, state."""
    path = draft_path(config, row["draft"])
    text = path.read_text(encoding="utf-8", errors="replace") if path else ""
    kind = row["kind"] or "issue"
    parsed = parse_draft(text, kind) if text else {"note": "", "fields": {}, "images": [], "unknown": []}
    edits = _edits(row)
    fields = {**parsed["fields"], **edits}
    changed = any((fields.get(k) or "") != (parsed["fields"].get(k) or "") for k in set(fields) | set(parsed["fields"]))
    folder = path.parent if path else None
    images = [{**x, "available": bool(folder and (folder / x["file"]).is_file())} for x in parsed["images"]]
    return {
        "id": row["id"],
        "kind": kind,
        "title": fields.get("title") or row["title"] or str(row["id"]),
        "file": path.name if path else (Path(row["draft"]).name if row["draft"] else None),
        "found": bool(text),
        "position": _position(row),
        "queue_note": row["note"] or "",
        "state": row["state"],
        "number": row["number"],
        "comment_on": _comment_on(row),
        "note": parsed["note"],
        "original": parsed["fields"],
        "fields": fields,
        "changed": changed,
        "images": images,
        "unknown_headings": parsed["unknown"],
        "test_case": row["test_case"],
    }


def rows(db) -> list:
    """Drafts waiting for review, and those marked as filed here that are still in the queue (so marking can be
    undone): issues by queue position, then comments."""
    return db.q(
        "SELECT * FROM issues WHERE state = 'draft'"
        " OR (review_mark IS NOT NULL AND state IN ('filed', 'posted') AND note LIKE 'gallery queue%')"
        " ORDER BY created, id"
    )


def items(config: dict, db) -> list[dict]:
    out = [item(config, r) for r in rows(db)]
    out.sort(key=lambda x: (x["kind"] == "comment", x["position"], x["id"]))
    return out


def _row(db, issue_id: int):
    row = db.one("SELECT * FROM issues WHERE id = ?", (issue_id,))
    if row is None:
        raise KeyError(f"no draft {issue_id}")
    return row


def save_fields(config: dict, db: DB, issue_id: int, fields: dict) -> dict:
    """Merges edited fields into ``issues.fields``; a field equal to the file's text is dropped from the edits."""
    row = _row(db, issue_id)
    allowed = {f[0] for f in (COMMENT_FIELDS if row["kind"] == "comment" else FIELDS)}
    current = item(config, row)
    edits = _edits(row)
    for key, value in fields.items():
        if key not in allowed or not isinstance(value, str):
            raise ValueError(f"not a field: {key}")
        if value == (current["original"].get(key) or ""):
            edits.pop(key, None)
        else:
            edits[key] = value[:200_000]
    db.x(
        "UPDATE issues SET fields = ? WHERE id = ?",
        (json.dumps(edits, ensure_ascii=False) if edits else None, row["id"]),
    )
    return item(config, _row(db, issue_id))


def reset(config: dict, db: DB, issue_id: int) -> dict:
    db.x("UPDATE issues SET fields = NULL WHERE id = ?", (_row(db, issue_id)["id"],))
    return item(config, _row(db, issue_id))


def mark(config: dict, db: DB, issue_id: int, number: int | None) -> dict:
    """Marks a draft as filed as ``#number`` (a comment: as posted), or with ``None`` undoes that."""
    row = _row(db, issue_id)
    if number is None:
        if not row["review_mark"]:
            raise ValueError("only a draft marked here can be unmarked")
        db.x(
            "UPDATE issues SET state = 'draft', number = NULL, filed = NULL, review_mark = NULL WHERE id = ?",
            (row["id"],),
        )
        return item(config, _row(db, issue_id))
    if row["state"] != "draft":
        raise ValueError(f"the draft is {row['state']}, not waiting")
    mark_value = json.dumps({"at": now(), "number": number})
    if row["kind"] == "comment":
        db.x(
            "UPDATE issues SET state = 'posted', filed = ?, review_mark = ? WHERE id = ?",
            (today(), mark_value, row["id"]),
        )
    else:
        if number <= 0:
            raise ValueError("not an issue number")
        other = db.one("SELECT id FROM issues WHERE number = ? AND id != ?", (number, row["id"]))
        if other is not None:
            raise ValueError(f"#{number} is already recorded for another draft")
        db.x(
            "UPDATE issues SET state = 'filed', number = ?, filed = ?, review_mark = ? WHERE id = ?",
            (number, today(), mark_value, row["id"]),
        )
    return item(config, _row(db, issue_id))
