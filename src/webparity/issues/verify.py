# SPDX-License-Identifier: BSD-2-Clause
"""Verification of filed issues: reruns each issue's test case in Ladybird and the reference browsers and says whether
the bug is fixed, still there, needs a person, or could not be checked.

The conventions are those of the nightly verification that came before webparity:

- A test case writes its result into ``#out``; it is read with ``(document.getElementById("out") || {}).textContent``
  after the page has loaded and a fixed wait of 4 seconds. A test case without ``#out`` is **manual**, and so is one
  whose steps to reproduce need the mouse, wheel, clicks or keys, and one that is a server script (Python or ``#!``)
  rather than a page.
- The expected result is the code block under "Expected behavior" in the draft, the reported bug the one under "Actual
  behavior" (or ``expected.txt`` / ``actual.txt`` next to the test case).
- Results are compared after normalising whitespace; texts that differ only in numbers count as equal when every number
  is within 2 units or 2 % (rounding, font metrics).
- Chromium's answer only counts as a reference when it is not ``#out``'s placeholder text (the test case did not finish)
  and not the reported bug itself (the environment lacked something the test case needed).
- A test case may be one file or a folder with several files (``index.html`` is the page); test cases are copied to
  ``data_dir/verify/<N>/`` and probed ten at a time, so one that hangs only costs its batch.

Results: ``fixed``, ``fixed-build-older`` (right, but the build predates the closing), ``fixed-silently`` (open but
right), ``not-fixed`` (closed, built, still the reported bug), ``changed`` (closed, built, some third result),
``waiting-for-build`` (closed after the build), ``still-broken``, ``manual``, ``closed-without-fix`` and ``error``
(no usable answer).
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from webparity.db import DB, now
from webparity.issues.common import (
    code_block,
    data_dir,
    entry_file,
    load_signatures,
    resolve_path,
    section,
    test_case_files,
)
from webparity.probe import probe

READ_OUT = 'return (document.getElementById("out") || {}).textContent'
WAIT = 4.0
BATCH = 10
# Steps that need the wheel, clicks, keys or mouse movement cannot be checked by reading #out after load
INTERACTIVE = re.compile(r"\b(mouse|wheel|click|hover|drag|press|type into|tab key|keyboard)\b", re.I)
SERVER_SCRIPT = re.compile(r"\s*(#.*\n\s*)*(#!|import |from \w+ import )")
OUT_START = re.compile(r'id="?out"?[^>]*>([^<]*)<')
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

ORDER = [
    "fixed",
    "fixed-silently",
    "not-fixed",
    "changed",
    "fixed-build-older",
    "waiting-for-build",
    "error",
    "still-broken",
    "manual",
    "closed-without-fix",
]
GROUP = {
    "fixed": "fixed",
    "fixed-silently": "fixed",
    "fixed-build-older": "fixed",
    "not-fixed": "still broken",
    "still-broken": "still broken",
    "waiting-for-build": "still broken",
    "changed": "manual",
    "manual": "manual",
    "closed-without-fix": "skipped",
    "error": "error",
}


def norm(value: str | None) -> str:
    if value is None or str(value).strip() in ("null", "undefined", "None"):
        return ""
    return "\n".join(line.strip() for line in str(value).strip().splitlines() if line.strip())


def same(a: str, b: str) -> bool:
    """The same text, or the same text around the numbers with every number within 2 units or 2 %."""
    if not a or not b:
        return False
    if a == b:
        return True
    if NUMBER.sub("#", a) != NUMBER.sub("#", b):
        return False
    for x, y in zip(map(float, NUMBER.findall(a)), map(float, NUMBER.findall(b)), strict=False):
        if abs(x - y) > max(2.0, 0.02 * max(abs(x), abs(y))):
            return False
    return True


def assess(item: dict, build_date: str | None) -> str:
    """The verdict for one issue from its normalised answers (``ladybird``, ``chromium``, ``firefox``), ``expected``,
    ``actual``, ``start`` (``#out``'s initial text), ``state`` and ``closed``."""
    lb, expected, actual, start = item["ladybird"], item["expected"], item["actual"], item.get("start")

    def reliable(ref: str) -> bool:
        return bool(ref) and (ref != start or same(ref, expected)) and not same(ref, actual)

    cr, ff = item.get("chromium", ""), item.get("firefox", "")
    cr_reliable = reliable(cr)
    like_reference = (cr_reliable and same(lb, cr)) or (reliable(ff) and same(lb, ff))
    like_expected = same(lb, expected)
    like_bug = same(lb, actual) and not same(expected, actual)
    right = like_reference or like_expected
    closed = item["state"] == "closed"
    # The build contains the fix when the issue was closed before the build's date (unknown build date: assume it does)
    built = not build_date or bool(item.get("closed") and item["closed"] < build_date)
    if not lb or (lb == cr and not cr_reliable and not like_expected):
        return "error"
    if closed and not built:
        return "fixed-build-older" if right else "waiting-for-build"
    if closed:
        return "fixed" if right else "not-fixed" if like_bug else "changed"
    return "fixed-silently" if right else "still-broken"


def build_info(config: dict, db: DB) -> tuple[str, str | None]:
    """(commit, date) of the Ladybird build under test: the build info file, else the latest visit's build."""
    from webparity.visit import build_info as read_build_info

    if (config.get("ladybird") or {}).get("build_info"):
        info = read_build_info(config)  # JSON, or the build host's text file "<commit> <date> <time>"
        if isinstance(info, dict) and info.get("commit"):
            date = str(info.get("date") or info.get("built") or "")[:10] or None
            return str(info["commit"])[:11], date
    row = db.one("SELECT build, started FROM visits WHERE build IS NOT NULL ORDER BY started DESC LIMIT 1")
    return (row["build"][:11], None) if row else ("", None)


def result_text(text: str) -> str | None:
    """The result under "Expected behavior" or "Actual behavior": the code block, or, in a draft where the form's code
    field was filled without a fence, the field's text without a one-line lead-in ("As in Chromium and Firefox:")."""
    block = code_block(text)
    if block is not None:
        return block
    text = text.strip()
    if not text:
        return None
    first, sep, rest = text.partition("\n\n")
    if sep and "\n" not in first and first.rstrip().endswith(":") and rest.strip():
        text = rest.strip()
    return text


def _read(path: Path | None) -> str:
    try:
        return path.read_text(encoding="utf-8") if path else ""
    except OSError:
        return ""


def prepare(config: dict, row, stage: Path) -> dict:
    """What verification needs for one issue; ``result`` is set already when it cannot be run (manual, skipped)."""
    item = {
        "number": row["number"],
        "title": row["title"] or "",
        "state": row["state"],
        "closed": row["closed"],
        "previously": row["verified_result"],
    }
    if row["state"] in ("not-planned", "duplicate"):
        item["result"] = "closed-without-fix"
        return item
    draft_path = Path(row["draft"]) if row["draft"] else None
    draft = _read(draft_path)
    bases = [data_dir(config)] + ([draft_path.parent, draft_path.parent.parent] if draft_path else [])
    test_case = resolve_path(row["test_case"], *bases)
    entry = entry_file(test_case) if test_case else None
    if entry is None:
        item["result"], item["reason"] = "manual", "no test case"
        return item
    html = _read(entry)
    files = test_case_files(test_case)
    if entry.suffix == ".py" or SERVER_SCRIPT.match(html) or any(f.suffix == ".py" for f in files):
        item["result"], item["reason"] = "manual", "needs a server script"
        return item
    if 'id="out"' not in html and "id=out" not in html and "id='out'" not in html:
        item["result"], item["reason"] = "manual", "no #out in the test case"
        return item
    if INTERACTIVE.search(section(draft, "Steps to reproduce")):
        item["result"], item["reason"] = "manual", "interactive steps"
        return item
    side = test_case if test_case.is_dir() else test_case.parent
    expected = result_text(section(draft, "Expected behavior"))
    actual = result_text(section(draft, "Actual behavior"))
    if expected is None and (side / "expected.txt").is_file():
        expected = _read(side / "expected.txt")
    if actual is None and (side / "actual.txt").is_file():
        actual = _read(side / "actual.txt")
    item["expected"], item["actual"] = norm(expected), norm(actual)
    m = OUT_START.search(html)
    item["start"] = norm(m.group(1)) if m else None  # e.g. "running..." – the test case did not finish

    folder = stage / str(row["number"])
    if folder.exists():
        shutil.rmtree(folder)
    if test_case.is_dir():
        shutil.copytree(test_case, folder)
        item["file"] = folder / entry.relative_to(test_case)
    else:
        folder.mkdir(parents=True)
        shutil.copy2(test_case, folder / test_case.name)
        item["file"] = folder / test_case.name
    return item


def run_probe(items: list[dict], browsers: list[str], stage: Path, batch: int = BATCH) -> dict[int, dict[str, str]]:
    """The answers per issue number and browser. Each batch starts its own browser sessions."""
    answers: dict[int, dict[str, str]] = {}
    todo = [i for i in items if "file" in i]
    for start in range(0, len(todo), batch):
        part = todo[start : start + batch]
        targets = [str(i["file"]) for i in part]
        try:
            results = probe(targets, script=READ_OUT, browsers=browsers, wait=WAIT, root=str(stage))
        except Exception as exc:  # a batch that fails entirely costs only itself
            print(f"verify: batch {start // batch + 1} failed: {exc}", file=sys.stderr)
            continue
        by_url: dict[str, int] = {}
        for item in part:
            rel = Path(item["file"]).resolve().relative_to(stage.resolve()).as_posix()
            by_url[rel] = item["number"]
        for result in results:
            path = result.target.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
            number = by_url.get(path)
            if number is None:
                continue
            answers.setdefault(number, {})[result.browser] = result.value if result.error is None else ""
            if result.error:
                answers[number][f"{result.browser}_error"] = result.error
    return answers


def verify(
    config: dict, db: DB, numbers: list[int] | None = None, include_verified: bool = False, record: bool = True
) -> list[dict]:
    """Runs the test cases of filed issues (all, or ``numbers``) and records ``issues.verified``/``verified_result``.

    Issues already verified as fixed are skipped unless ``include_verified`` or they are named in ``numbers``.
    Returns one dict per issue with ``number``, ``title``, ``state``, ``result``, ``group`` (fixed / still broken /
    manual / error / skipped), the answers per browser, ``expected``, ``actual`` and ``reason`` where relevant.
    """
    browsers = ["ladybird", "chromium"] + (["firefox"] if "firefox" in (config.get("browsers") or []) else [])
    rows = db.q("SELECT * FROM issues WHERE number IS NOT NULL AND kind = 'issue' AND state != 'draft' ORDER BY number")
    if numbers:
        wanted = set(numbers)
        rows = [r for r in rows if r["number"] in wanted]
    elif not include_verified:
        rows = [r for r in rows if r["verified_result"] != "fixed"]
    commit, build_date = build_info(config, db)
    stage = data_dir(config) / "verify"
    stage.mkdir(parents=True, exist_ok=True)
    items = [prepare(config, row, stage) for row in rows]
    answers = run_probe(items, browsers, stage)
    for item in items:
        if "result" not in item:
            got = answers.get(item["number"], {})
            for browser in browsers:
                item[browser] = norm(got.get(browser))
            errors = [got[k] for k in got if k.endswith("_error")]
            if errors:
                item["reason"] = "; ".join(errors)[:300]
            item["result"] = assess(item, build_date)
        item["group"] = GROUP[item["result"]]
        item["build"] = commit
        item.pop("file", None)
    if record:
        _record(db, rows, items)
    return items


def _record(db: DB, rows, items: list[dict]) -> None:
    signatures = {r["number"]: load_signatures(r["signatures"]) for r in rows}
    with db.tx():
        for item in items:
            db.x(
                "UPDATE issues SET verified = ?, verified_result = ? WHERE number = ?",
                (now(), item["result"], item["number"]),
            )
            sigs = signatures.get(item["number"]) or []
            if not sigs:
                continue
            marks = ",".join("?" * len(sigs))
            if item["result"] == "fixed":
                db.x(
                    f"UPDATE candidates SET status = 'fixed' WHERE signature IN ({marks})"
                    " AND status IN ('fixed?', 'filed')",
                    sigs,
                )
            elif item["result"] == "not-fixed":
                db.x(
                    f"UPDATE candidates SET status = 'filed' WHERE signature IN ({marks}) AND status = 'fixed?'",
                    sigs,
                )


def format_report(items: list[dict], build: str = "") -> str:
    """A Markdown report grouped by result, in verifiera's order. Manual issues and issues closed without a fix are
    listed only while open or when not verified before (so the report shows what is new)."""
    lines = [f"# Verification {now()[:10]}", "", f"Build: {build or 'unknown'}. {len(items)} issues checked.", ""]
    for result in ORDER:
        group = [
            i
            for i in items
            if i["result"] == result
            and (result not in ("manual", "closed-without-fix") or i["state"] == "filed" or not i.get("previously"))
        ]
        if not group:
            continue
        lines += [f"## {result} ({len(group)})", ""]
        for i in group:
            lines.append(f"- #{i['number']} {i['title'][:90]}" + (f" ({i['reason']})" if i.get("reason") else ""))
            if result in ("not-fixed", "changed", "error"):
                for browser in ("ladybird", "chromium", "firefox"):
                    if browser in i:
                        lines.append(f"  - {browser}: `{(i[browser] or '').replace(chr(10), ' / ')[:300]}`")
        lines.append("")
    return "\n".join(lines)
