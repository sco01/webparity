# SPDX-License-Identifier: BSD-2-Clause
"""Known differences and bugs: findings that are explained, so they don't make a site a candidate.

Three sources, all ending in ``finding.known`` (a reason with the issue, e.g. ``"#14574: logical -webkit- aliases are
dropped"``):

- **Signature entries** in ``known/*.yaml`` (an entry without ``js``/``log``): a signature prefix or regex that is
  explained everywhere, e.g. ``css-dropped:margin-block-end:`` (#14574).
- **Page tests** in ``known/*.yaml`` (an entry with ``id`` and ``js`` or ``log``): a known bug whose signatures are too
  general to match on (``layout:``, ``image-differs``), so a test decides whether the page has the pattern. The JS tests
  run during the visit (``webparity.measure``, at the end of the reload step) only when a preliminary analysis has an
  error finding the pattern can explain - in the reference browser by default (its DOM is the reference; Ladybird may
  have crashed), in Ladybird with ``run_in: ladybird``, in a 390 x 844 viewport with ``mobile: true`` (when all matched
  signatures are mobile ones) or ``mobile: always``. ``async: true`` tests get a ``done`` callback. ``log`` tests are a
  regex on the visit's Ladybird log, evaluated here. The results are in ``browsers.<name>.reload.known_tests``.
- **Site exceptions** in the database table ``site_exceptions`` (domain, signature prefix, reason): explained on one
site.

An entry with ``fixed: <commit>`` only applies to builds without that commit (2026-11-13: otherwise a pattern could
mark new bugs as known after the fix). Whether a build has the commit is decided with ``git merge-base --is-ancestor``
in the Ladybird source (``config.ladybird.source``, default ``/src``); when that can't be decided the entry applies.

Entry keys: ``issue``, ``text``, ``prefix`` (list) or ``regex``, optional ``fixed``; page tests also ``id``,
``run_in``, ``mobile``, ``async``, ``js`` or ``log``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "known"


@dataclass
class Entry:
    issue: str
    text: str
    prefix: tuple[str, ...] = ()
    regex: str | None = None
    fixed: str | None = None
    id: str | None = None
    run_in: str = "reference"
    mobile: bool | str = False
    is_async: bool = False
    js: str | None = None
    log: str | None = None
    source: str = ""
    _compiled: re.Pattern | None = field(default=None, repr=False, compare=False)

    @property
    def is_test(self) -> bool:
        return bool(self.js or self.log)

    @property
    def reason(self) -> str:
        return f"{self.issue}: {self.text}"

    def matches(self, signature: str) -> bool:
        if self.prefix:
            return signature.startswith(self.prefix)
        if self.regex:
            if self._compiled is None:
                self._compiled = re.compile(self.regex)
            return bool(self._compiled.search(signature))
        return False

    def script(self) -> str:
        """The JS as a statement body for ``Handle.js``."""
        if self.is_async:
            return "return await new Promise(done => {\n" + (self.js or "") + "\n});"
        return self.js or ""

    def wants_mobile(self, signatures: list[str]) -> bool:
        return self.mobile == "always" or (self.mobile is True and all(s.startswith("mobile-") for s in signatures))

    @classmethod
    def from_dict(cls, d: dict, source: str = "") -> Entry:
        prefix = d.get("prefix") or ()
        if isinstance(prefix, str):
            prefix = (prefix,)
        return cls(
            issue=str(d["issue"]),
            text=str(d.get("text") or ""),
            prefix=tuple(str(p) for p in prefix),
            regex=d.get("regex"),
            fixed=str(d["fixed"]) if d.get("fixed") else None,
            id=d.get("id"),
            run_in=d.get("run_in") or "reference",
            mobile=d.get("mobile") or False,
            is_async=bool(d.get("async")),
            js=d.get("js"),
            log=d.get("log"),
            source=source,
        )


def load_entries(directory: str | Path | None = None) -> list[Entry]:
    import yaml

    directory = Path(directory) if directory else DEFAULT_DIR
    entries: list[Entry] = []
    for path in sorted(directory.glob("*.yaml")):
        for d in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            entries.append(Entry.from_dict(d, path.name))
    return entries


def domain(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


class BuildCheck:
    """Whether a build contains a commit: ``git merge-base --is-ancestor`` in the Ladybird source, cached."""

    def __init__(self, source: str | None = "/src"):
        self.source = source
        self._cache: dict[tuple[str, str], bool] = {}

    def has(self, build_commit: str | None, commit: str) -> bool:
        if not build_commit or not self.source or not re.fullmatch(r"[0-9a-f]{7,40}", build_commit):
            return False
        key = (build_commit, commit)
        if key not in self._cache:
            answer = False
            try:
                answer = (
                    subprocess.run(
                        ["git", "-C", self.source, "merge-base", "--is-ancestor", commit, build_commit],
                        capture_output=True,
                        timeout=20,
                    ).returncode
                    == 0
                )
            except (OSError, subprocess.SubprocessError):
                pass
            self._cache[key] = answer
        return self._cache[key]


def build_commit(bundle) -> str | None:
    build = bundle.data.get("build") or {}
    commit = build.get("commit") or build.get("git_commit")
    if not commit and isinstance(build.get("version"), str):  # "Linux <hash> <date>" like the old LB_BYGGE
        parts = build["version"].split()
        commit = parts[1] if len(parts) >= 2 else None
    return commit


class Known:
    def __init__(self, entries: list[Entry] | None = None, db=None, build_check: BuildCheck | None = None):
        self.entries = entries if entries is not None else load_entries()
        self.db = db
        self.build_check = build_check or BuildCheck(None)

    @classmethod
    def load(cls, directory: str | Path | None = None, db=None, source: str | None = None) -> Known:
        return cls(load_entries(directory), db=db, build_check=BuildCheck(source))

    @classmethod
    def from_config(cls, config: dict, db=None) -> Known:
        known_dir = (config.get("known") or {}).get("dir") if isinstance(config.get("known"), dict) else None
        source = (config.get("ladybird") or {}).get("source", "/src")
        return cls.load(known_dir, db=db, source=source)

    # --- which entries apply ------------------------------------------------------------------------------------------
    def applies(self, entry: Entry, commit: str | None) -> bool:
        return not (entry.fixed and self.build_check.has(commit, entry.fixed))

    @property
    def signature_entries(self) -> list[Entry]:
        return [e for e in self.entries if not e.is_test]

    @property
    def tests(self) -> list[Entry]:
        return [e for e in self.entries if e.is_test]

    def page_tests_for(self, signatures: list[str], commit: str | None) -> list[tuple[Entry, list[str]]]:
        """The JS page tests to run for these error signatures: (entry, the signatures it can explain)."""
        out = []
        for e in self.tests:
            if not e.js or not self.applies(e, commit):
                continue
            matched = [s for s in signatures if e.matches(s)]
            if matched:
                out.append((e, matched))
        return out

    # --- marking ------------------------------------------------------------------------------------------------------
    def site_exceptions(self, bundle) -> dict[str, str]:
        if self.db is None:
            return {}
        out: dict[str, str] = {}
        for d in ("*", domain(bundle.data.get("url") or "")):
            try:
                out.update(self.db.site_exceptions(d) or {})
            except Exception:  # a broken database must not lose the visit
                pass
        return out

    def test_results(self, bundle) -> dict[str, object]:
        """Truthy page-test results from every browser's reload step."""
        out: dict[str, object] = {}
        for measurements in (bundle.data.get("browsers") or {}).values():
            for key, value in ((measurements.get("reload") or {}).get("known_tests") or {}).items():
                # a dict is a test that failed to run ({"error": ...}), never a match
                if value and not isinstance(value, dict) and key not in out:
                    out[key] = value
        return out

    def mark(self, bundle, findings) -> None:
        commit = build_commit(bundle)
        exceptions = self.site_exceptions(bundle)
        results = self.test_results(bundle)
        log_text = None
        for e in self.tests:
            if not self.applies(e, commit):
                continue
            errors = [f for f in findings if f.category == "error" and not f.known and e.matches(f.signature)]
            if not errors:
                continue
            if e.log:
                if log_text is None:
                    log_text = (bundle.read_file("ladybird.log") or b"").decode("utf-8", "replace")
                m = re.search(e.log, log_text)
                result = m.group(0)[:120] if m else None
            else:
                result = results.get(e.id)
            if result:
                for f in errors:
                    f.known = e.reason
                    f.data.setdefault("known_test", {"id": e.id, "result": result})
        for f in findings:
            if f.known:
                continue
            for e in self.signature_entries:
                if self.applies(e, commit) and e.matches(f.signature):
                    f.known = e.reason
                    break
            else:
                for prefix, reason in exceptions.items():
                    if f.signature.startswith(prefix):
                        f.known = f"site exception: {reason}" if reason else "site exception"
                        break
