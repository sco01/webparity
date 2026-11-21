# SPDX-License-Identifier: BSD-2-Clause
"""Registering what a person filed: links a draft to the GitHub issue (or comment) number they created.

webparity never files anything itself. After a person has reviewed a draft and filed it on GitHub, ``register`` records
the number, so the watcher can follow the issue and the verification can rerun its test case.
"""

from __future__ import annotations

import re
from pathlib import Path

from webparity.db import DB, now
from webparity.issues.common import (
    data_dir,
    draft_title,
    dump_signatures,
    load_signatures,
    split_front_matter,
    today,
    update_front_matter,
)


def _number(number: int | str) -> int:
    m = re.fullmatch(r"\s*#?(\d+)\s*", str(number))
    if not m:
        raise ValueError(f"not an issue number: {number!r}")
    return int(m.group(1))


def find_draft(config: dict, draft: str | Path) -> Path:
    """A draft given as a path, a file name or a slug (looked up in ``data_dir/drafts``)."""
    p = Path(draft).expanduser()
    if p.is_file():
        return p.resolve()
    folder = data_dir(config) / "drafts"
    for candidate in (folder / p.name, folder / f"{p.name}.md", folder / "imported" / p.name):
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"no draft {draft} (looked in {folder})")


def _issue_row(db: DB, path: Path):
    return db.one("SELECT * FROM issues WHERE draft = ?", (str(path),)) or db.one(
        "SELECT * FROM issues WHERE draft LIKE ? ORDER BY id DESC LIMIT 1", (f"%/{path.name}",)
    )


def register(config: dict, db: DB, draft: str | Path, number: int | str, filed: str | None = None) -> dict:
    """Marks the draft as filed as ``#number``: the issues row gets the number, state ``filed`` and the filed date, and
    the candidates of its signatures get status ``filed`` and ``issue = '#N'``. Returns the issues row as a dict."""
    n = _number(number)
    path = find_draft(config, draft)
    text = path.read_text(encoding="utf-8")
    meta, _ = split_front_matter(text)
    signatures = meta.get("signatures") or []
    if isinstance(signatures, str):
        signatures = load_signatures(signatures)
    filed = filed or today()
    title = draft_title(text)
    with db.tx():
        other = db.one("SELECT id, draft FROM issues WHERE number = ?", (n,))
        row = _issue_row(db, path)
        if other is not None and (row is None or other["id"] != row["id"]):
            raise ValueError(f"#{n} is already registered for {other['draft']}")
        values = (
            n,
            str(path),
            title,
            meta.get("kind") or "issue",
            dump_signatures(signatures) if signatures else (row["signatures"] if row else None),
            meta.get("test_case") or (row["test_case"] if row else None),
            filed,
        )
        if row is None:
            db.x(
                "INSERT INTO issues (number, draft, title, kind, signatures, test_case, filed, state, created)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'filed', ?)",
                (*values, now()),
            )
        else:
            db.x(
                "UPDATE issues SET number = ?, draft = ?, title = COALESCE(?, title), kind = ?, signatures = ?,"
                " test_case = ?, filed = ?, state = 'filed' WHERE id = ?",
                (*values, row["id"]),
            )
        for signature in signatures:
            db.x("UPDATE candidates SET status = 'filed', issue = ? WHERE signature = ?", (f"#{n}", signature))
    update_front_matter(path, issue=n, filed=filed)
    return dict(db.one("SELECT * FROM issues WHERE number = ?", (n,)))


def register_comment(config: dict, db: DB, draft: str | Path, number: int | str, posted: str | None = None) -> dict:
    """Marks a comment draft as posted on issue ``#number``. Comments are rows of kind ``comment`` and state ``posted``;
    ``issues.number`` stays NULL (it is unique per issue), the issue it belongs to is kept in ``note`` as
    ``comment on #N`` (and in a ``target`` column when the database has one). Candidates are not changed."""
    n = _number(number)
    path = find_draft(config, draft)
    text = path.read_text(encoding="utf-8")
    meta, _ = split_front_matter(text)
    posted = posted or today()
    has_target = any(c["name"] == "target" for c in db.q("PRAGMA table_info(issues)"))
    note = f"comment on #{n}"
    with db.tx():
        row = _issue_row(db, path)
        if row is None:
            cur = db.x(
                "INSERT INTO issues (draft, title, kind, state, signatures, test_case, filed, note, created)"
                " VALUES (?, ?, 'comment', 'posted', ?, ?, ?, ?, ?)",
                (
                    str(path),
                    draft_title(text),
                    dump_signatures(meta.get("signatures") or []),
                    meta.get("test_case"),
                    posted,
                    note,
                    now(),
                ),
            )
            row_id = cur.lastrowid
        else:
            row_id = row["id"]
            db.x(
                "UPDATE issues SET kind = 'comment', state = 'posted', filed = ?, note = ? WHERE id = ?",
                (posted, note, row_id),
            )
        if has_target:
            db.x("UPDATE issues SET target = ? WHERE id = ?", (n, row_id))
    update_front_matter(path, kind="comment", comment_on=n, posted=posted)
    return dict(db.one("SELECT * FROM issues WHERE id = ?", (row_id,)))
