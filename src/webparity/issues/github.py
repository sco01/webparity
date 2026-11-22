# SPDX-License-Identifier: BSD-2-Clause
"""The issue watcher: follows filed issues on GitHub, anonymously and read only.

It uses the public REST API without authentication (60 requests an hour per address), sends the configured
``github.user_agent``, keeps ETags in ``data_dir/github-cache.json`` so unchanged issues cost a ``304`` (which GitHub
does not count against the limit), and stops when the rate limit is used up. It never posts anything.

When an issue closes as completed, its candidates become ``fixed?`` (pending verification) and the sites with those
signatures are queued again, so the next visits show whether the finding is gone.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from webparity.db import DB, now
from webparity.issues.common import data_dir, load_signatures

API = "https://api.github.com"
REQUEUE_PRIORITY = 90


class RateLimited(Exception):
    def __init__(self, reset: int | None):
        super().__init__(f"GitHub rate limit used up; resets at {time.strftime('%H:%M', time.localtime(reset or 0))}")
        self.reset = reset


class Client:
    """A tiny anonymous GitHub REST client with an ETag cache."""

    def __init__(self, config: dict, cache_path: Path | None = None):
        github = config.get("github", {})
        self.api = (github.get("api") or API).rstrip("/")
        self.user_agent = github.get("user_agent") or "webparity"
        self.cache_path = cache_path or data_dir(config) / "github-cache.json"
        try:
            self.cache: dict[str, dict] = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.remaining: int | None = None
        self.reset: int | None = None
        self.requests = 0

    def get(self, path: str) -> Any:
        if self.remaining is not None and self.remaining <= 0 and (self.reset or 0) > time.time():
            raise RateLimited(self.reset)
        url = path if path.startswith("http") else f"{self.api}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self.user_agent,
        }
        cached = self.cache.get(url)
        if cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        request = urllib.request.Request(url, headers=headers, method="GET")
        self.requests += 1
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                self._rate(response.headers)
                body = json.loads(response.read().decode("utf-8"))
                etag = response.headers.get("ETag")
                if etag:
                    self.cache[url] = {"etag": etag, "body": body}
                return body
        except urllib.error.HTTPError as exc:
            self._rate(exc.headers)
            if exc.code == 304 and cached:
                return cached["body"]
            if exc.code in (403, 429) and (self.remaining == 0 or exc.headers.get("Retry-After")):
                retry = exc.headers.get("Retry-After")
                raise RateLimited(int(time.time()) + int(retry) if retry and retry.isdigit() else self.reset) from exc
            raise

    def _rate(self, headers) -> None:
        if headers is None:
            return
        remaining, reset = headers.get("X-RateLimit-Remaining"), headers.get("X-RateLimit-Reset")
        if remaining is not None and remaining.isdigit():
            self.remaining = int(remaining)
        if reset is not None and reset.isdigit():
            self.reset = int(reset)

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.cache_path)


def closing_reference(client: Client, repository: str, number: int, closed_at: str | None) -> str | None:
    """What closed the issue: ``#PR`` for a merged pull request that references it, else ``commit <sha>`` from the
    closed event, else None. Costs one timeline request (only made when an issue is newly closed)."""
    try:
        timeline = client.get(f"/repos/{repository}/issues/{number}/timeline?per_page=100")
    except RateLimited:
        raise
    except (urllib.error.URLError, OSError, ValueError):
        return None
    commit, prs = None, []
    for event in timeline if isinstance(timeline, list) else []:
        if event.get("event") == "closed" and event.get("commit_id"):
            commit = event["commit_id"]
        if event.get("event") == "cross-referenced":
            source = (event.get("source") or {}).get("issue") or {}
            merged = (source.get("pull_request") or {}).get("merged_at")
            if merged and (not closed_at or merged <= closed_at):
                prs.append((merged, source.get("number")))
    if prs:
        return f"#{max(prs)[1]}"
    return f"commit {commit[:11]}" if commit else None


def _state(issue: dict) -> str:
    if issue.get("state") != "closed":
        return "filed"
    reason = issue.get("state_reason") or "completed"
    return {"completed": "closed", "not_planned": "not-planned", "duplicate": "duplicate"}.get(reason, "closed")


def on_closed(db: DB, signatures: list[str]) -> int:
    """An issue closed as completed: candidates become ``fixed?`` and their sites are queued again.

    Returns the number of sites queued."""
    if not signatures:
        return 0
    marks = ",".join("?" * len(signatures))
    db.x(
        f"UPDATE candidates SET status = 'fixed?' WHERE signature IN ({marks})"
        " AND status IN ('filed', 'investigating')",
        signatures,
    )
    return db.requeue(
        f"id IN (SELECT DISTINCT site FROM findings WHERE signature IN ({marks}))", signatures, REQUEUE_PRIORITY
    )


def on_reopened(db: DB, number: int, signatures: list[str]) -> None:
    if signatures:
        marks = ",".join("?" * len(signatures))
        db.x(
            f"UPDATE candidates SET status = 'filed', issue = ? WHERE signature IN ({marks}) AND status = 'fixed?'",
            [f"#{number}", *signatures],
        )


def watch(config: dict, db: DB, numbers: list[int] | None = None) -> list[dict]:
    """Updates state, closed and closed_by of every filed issue (or just ``numbers``) from GitHub.

    Returns one dict per issue that changed: ``{"number", "title", "old", "new", "closed", "closed_by", "requeued"}``.
    Stops quietly (keeping what it learned) when the rate limit is used up.
    """
    repository = config.get("github", {}).get("repository") or "LadybirdBrowser/ladybird"
    client = Client(config)
    sql = "SELECT * FROM issues WHERE number IS NOT NULL AND kind = 'issue' AND state != 'draft'"
    # Open issues first: if the rate limit runs out, what is left are closed issues (which seldom change)
    rows = db.q(sql + " ORDER BY state != 'filed', closed_by IS NOT NULL, number")
    if numbers:
        wanted = set(numbers)
        rows = [r for r in rows if r["number"] in wanted]
    changes: list[dict] = []
    try:
        for row in rows:
            n = row["number"]
            try:
                issue = client.get(f"/repos/{repository}/issues/{n}")
            except RateLimited:
                raise
            except urllib.error.HTTPError as exc:
                note = f"GitHub: HTTP {exc.code}"
                db.x("UPDATE issues SET last_checked = ?, note = ? WHERE id = ?", (now(), note, row["id"]))
                continue
            except (urllib.error.URLError, OSError, ValueError):
                continue
            state = _state(issue)
            closed = (issue.get("closed_at") or "")[:10] or None
            closed_by = row["closed_by"]
            requeued = 0
            signatures = load_signatures(row["signatures"])
            newly_closed = state != "filed" and row["state"] in ("filed", "draft")
            if newly_closed or (state == "closed" and not closed_by):
                closed_by = closing_reference(client, repository, n, issue.get("closed_at")) or closed_by
            if state == "filed":
                closed, closed_by = None, None
            with db.tx():
                db.x(
                    "UPDATE issues SET state = ?, closed = ?, closed_by = ?, last_checked = ?,"
                    " title = COALESCE(title, ?) WHERE id = ?",
                    (state, closed, closed_by, now(), issue.get("title"), row["id"]),
                )
                if newly_closed and state == "closed":
                    requeued = on_closed(db, signatures)
                elif state == "filed" and row["state"] != "filed":
                    on_reopened(db, n, signatures)
            if state != row["state"] or closed_by != row["closed_by"]:
                changes.append(
                    {
                        "number": n,
                        "title": row["title"] or issue.get("title"),
                        "old": row["state"],
                        "new": state,
                        "closed": closed,
                        "closed_by": closed_by,
                        "requeued": requeued,
                    }
                )
    except RateLimited as exc:
        changes.append({"number": None, "error": str(exc)})
    finally:
        client.save()
    return changes
