# SPDX-License-Identifier: BSD-2-Clause
"""Shared helpers for the issue workflow: draft front matter, Markdown sections, signature lists, test case paths."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# The heading of the draft section that stays local: everything under it is never part of the filed issue.
NOTES_HEADING = "Notes (not for the issue)"
# The headings of Ladybird's bug report form, in order (the draft uses them so it can be pasted field by field).
SOURCE_HEADING = "HTML/SVG/etc. source for a reduced test case"


def today() -> str:
    return time.strftime("%Y-%m-%d")


def data_dir(config: dict) -> Path:
    return Path(config["data_dir"])


# --- front matter ----------------------------------------------------------------------------------------------------
def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """``---``-delimited ``key: value`` lines at the top of a draft; values starting with ``[`` or ``{`` are JSON."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or line.startswith("#"):
            continue
        value = value.strip()
        if value[:1] in "[{":
            try:
                meta[key.strip()] = json.loads(value)
                continue
            except ValueError:
                pass
        meta[key.strip()] = value
    return meta, text[end + 5 :]


def join_front_matter(meta: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list | dict):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}: {'' if value is None else value}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def update_front_matter(path: Path, **changes: Any) -> None:
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    meta.update(changes)
    path.write_text(join_front_matter(meta, body), encoding="utf-8")


# --- Markdown sections -----------------------------------------------------------------------------------------------
def section(text: str, heading: str) -> str:
    """The body of ``## heading`` (matched as a prefix), up to the next ``## `` heading."""
    m = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def code_block(text: str) -> str | None:
    m = re.search(r"```[^\n]*\n(.*?)```", text, re.S)
    return m.group(1) if m else None


def issue_part(text: str) -> str:
    """The part of a draft that goes to GitHub: no front matter, no notes section, no text before the title."""
    _, body = split_front_matter(text)
    m = re.search(rf"^## {re.escape(NOTES_HEADING)}[^\n]*\n.*?(?=^## |\Z)", body, re.M | re.S)
    if m:
        body = body[: m.start()] + body[m.end() :]
    start = body.find("## Title")
    return body[start:] if start >= 0 else body


def draft_title(text: str) -> str | None:
    m = re.search(r"^## Title[ \t]*\n+(.+?)\n", text, re.M)
    return m.group(1).strip() if m else None


# --- signatures and rows --------------------------------------------------------------------------------------------
def load_signatures(value: str | None) -> list[str]:
    """``issues.signatures`` is a JSON list; older rows may hold a comma- or newline-separated string."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(s) for s in parsed]
    except ValueError:
        pass
    return [s.strip() for s in re.split(r"[\n,]", value) if s.strip()]


def dump_signatures(signatures: list[str]) -> str:
    return json.dumps(list(dict.fromkeys(signatures)), ensure_ascii=False)


# --- test cases ------------------------------------------------------------------------------------------------------
def resolve_path(value: str | None, *bases: Path) -> Path | None:
    """An absolute path, or the first base under which the relative path exists (else under the first base)."""
    if not value:
        return None
    p = Path(value).expanduser()
    if p.is_absolute():
        return p
    for base in bases:
        if (base / p).exists():
            return base / p
    return bases[0] / p if bases else p


def entry_file(test_case: Path) -> Path | None:
    """The page to load for a test case: the file itself, or a folder's index.html / test.html / only .html file."""
    if test_case.is_file():
        return test_case
    if not test_case.is_dir():
        return None
    for name in ("index.html", "test.html", f"{test_case.name}.html"):
        if (test_case / name).is_file():
            return test_case / name
    pages = sorted(test_case.glob("*.htm*")) + sorted(test_case.glob("*.svg")) + sorted(test_case.glob("*.xhtml"))
    return pages[0] if len(pages) == 1 else (pages[0] if pages else None)


def test_case_files(test_case: Path) -> list[Path]:
    if test_case.is_file():
        return [test_case]
    if test_case.is_dir():
        return sorted(p for p in test_case.rglob("*") if p.is_file())
    return []


def split_source(source: str) -> list[tuple[str | None, str]]:
    """A test case source with several files, separated by lines ``<!-- name.ext -->`` (as in the gallery's drafts).

    Returns ``[(name, content), ...]`` where the first entry is the test case itself (as in verifiera: the first marked
    file; text before the first marker is dropped). A source without markers is one file with name ``None``.
    """
    parts = re.split(r"^<!--\s*([\w.-]+\.\w+)\s*-->\s*$", source, flags=re.M)
    if len(parts) < 3:
        return [(None, source.strip() + "\n")]
    return [(name, content.strip() + "\n") for name, content in zip(parts[1::2], parts[2::2], strict=False)]


def write_source(folder: Path, source: str) -> Path:
    """Writes a (possibly multi-file) test case source into ``folder``; the test case page is ``index.html`` (and also
    kept under its own name when the source names it, so the other files can refer to it). Returns the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    files = split_source(source)
    for i, (name, content) in enumerate(files):
        if i == 0:
            (folder / "index.html").write_text(content, encoding="utf-8")
            if name and name != "index.html":
                (folder / name).write_text(content, encoding="utf-8")
        elif name:
            (folder / name).write_text(content, encoding="utf-8")
    return folder
