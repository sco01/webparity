# SPDX-License-Identifier: BSD-2-Clause
"""The database: SQLite in WAL mode, shared by the workers, the web interface and the CLI.

Schema changes are migrations, applied in order and recorded in ``meta.schema_version``. Never edit an applied
migration; add a new one.

Sites are the queue: ``status`` is ``queued``, ``visiting`` (claimed by a worker), ``clean`` (no error findings; not
visited again unless requeued), ``candidate`` (has error findings), ``blocked`` (a bot wall in the reference),
``failed`` (tried again up to three times), ``dead`` or ``retired``.
A site is taken by priority, then by id; ``recheck`` marks a site queued again because of a candidate or a fix.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # 1: the core tables
    """
    CREATE TABLE sites (
        id INTEGER PRIMARY KEY,
        url TEXT NOT NULL,
        domain TEXT NOT NULL UNIQUE,
        source TEXT,
        priority INTEGER NOT NULL DEFAULT 50,
        status TEXT NOT NULL DEFAULT 'queued',
        added TEXT,
        visited TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        worker TEXT,
        claimed_at TEXT,
        recheck INTEGER NOT NULL DEFAULT 0,
        last_visit TEXT,
        template TEXT,
        note TEXT
    );
    CREATE INDEX sites_queue ON sites(status, priority DESC, id);

    CREATE TABLE visits (
        id TEXT PRIMARY KEY,
        site INTEGER NOT NULL,
        url TEXT NOT NULL,
        started TEXT NOT NULL,
        seconds REAL,
        worker TEXT,
        build TEXT,
        browsers TEXT,
        status TEXT,
        errors INTEGER NOT NULL DEFAULT 0,
        infos INTEGER NOT NULL DEFAULT 0,
        first_error TEXT,
        path TEXT,
        recording INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'webparity'
    );
    CREATE INDEX visits_site ON visits(site);
    CREATE INDEX visits_started ON visits(started);

    CREATE TABLE findings (
        id INTEGER PRIMARY KEY,
        visit TEXT NOT NULL,
        site INTEGER NOT NULL,
        signature TEXT NOT NULL,
        category TEXT NOT NULL,
        detector TEXT,
        text TEXT,
        data TEXT,
        known TEXT
    );
    CREATE INDEX findings_signature ON findings(signature);
    CREATE INDEX findings_visit ON findings(visit);
    CREATE INDEX findings_site ON findings(site);

    -- One row per error signature: the work list. status: new, investigating, filed, known, dismissed, fixed
    CREATE TABLE candidates (
        signature TEXT PRIMARY KEY,
        text TEXT,
        first_seen TEXT,
        last_seen TEXT,
        sites INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'new',
        issue TEXT,
        note TEXT
    );

    -- Features Ladybird reports as missing (FIXME / not implemented), counted by site
    CREATE TABLE missing_features (
        signature TEXT PRIMARY KEY,
        first_seen TEXT,
        last_seen TEXT,
        sites INTEGER NOT NULL DEFAULT 0
    );

    -- Per-site exceptions: this signature on this site is explained (site bug, bot wall, ...)
    CREATE TABLE site_exceptions (
        id INTEGER PRIMARY KEY,
        domain TEXT NOT NULL,
        signature TEXT NOT NULL,
        reason TEXT,
        added TEXT,
        UNIQUE(domain, signature)
    );

    -- Issues: drafts we wrote and issues filed upstream
    CREATE TABLE issues (
        id INTEGER PRIMARY KEY,
        number INTEGER UNIQUE,
        draft TEXT UNIQUE,
        title TEXT,
        kind TEXT NOT NULL DEFAULT 'issue',
        state TEXT NOT NULL DEFAULT 'draft',
        signatures TEXT,
        test_case TEXT,
        created TEXT,
        filed TEXT,
        closed TEXT,
        closed_by TEXT,
        last_checked TEXT,
        verified TEXT,
        verified_result TEXT,
        note TEXT
    );

    -- Nightly runs and other jobs, for the monitoring page and alerts
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        started TEXT NOT NULL,
        finished TEXT,
        status TEXT,
        summary TEXT
    );
    CREATE INDEX runs_kind ON runs(kind, started);

    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY,
        rule TEXT NOT NULL,
        started TEXT NOT NULL,
        resolved TEXT,
        message TEXT,
        notified TEXT
    );
    """,
    # 2: the issue a comment was posted on
    """
    ALTER TABLE issues ADD COLUMN target INTEGER
    """,
]


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=60, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=60000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    @classmethod
    def open(cls, config: dict) -> DB:
        return cls(Path(config["data_dir"]) / "webparity.db")

    # --- schema ---------------------------------------------------------------------------------------------------
    def migrate(self) -> int:
        self.conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        version = int(row[0]) if row else 0
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _split(sql):
                    self.conn.execute(statement)
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(i),))
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return len(MIGRATIONS)

    # --- helpers --------------------------------------------------------------------------------------------------
    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(args)).fetchone()

    def x(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(args))

    def tx(self):
        return _Tx(self.conn)

    # --- sites and the queue --------------------------------------------------------------------------------------
    def add_site(self, url: str, source: str = "", priority: int = 50) -> bool:
        from webparity.sites import domain_of

        d = domain_of(url)
        if not d:
            return False
        cur = self.x(
            "INSERT OR IGNORE INTO sites (url, domain, source, priority, added) VALUES (?, ?, ?, ?, ?)",
            (url, d, source, priority, now()),
        )
        return cur.rowcount > 0

    def claim(self, worker: str | None = None) -> sqlite3.Row | None:
        """Takes the next queued site for this worker, atomically."""
        worker = worker or socket.gethostname()
        with self.tx():
            row = self.one("SELECT * FROM sites WHERE status = 'queued' ORDER BY priority DESC, id LIMIT 1")
            if row is None:
                return None
            self.x(
                "UPDATE sites SET status = 'visiting', worker = ?, claimed_at = ?, attempts = attempts + 1"
                " WHERE id = ?",
                (worker, now(), row["id"]),
            )
        return self.one("SELECT * FROM sites WHERE id = ?", (row["id"],))

    def release_stale(self, minutes: int = 30) -> int:
        """Sites claimed by a worker that died go back to the queue."""
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - minutes * 60))
        return self.x(
            "UPDATE sites SET status = 'queued', worker = NULL WHERE status = 'visiting' AND claimed_at < ?", (cutoff,)
        ).rowcount

    def finish_site(self, site_id: int, status: str, visit_id: str | None) -> None:
        self.x(
            "UPDATE sites SET status = ?, visited = ?, last_visit = ?, worker = NULL, recheck = 0 WHERE id = ?",
            (status, now(), visit_id, site_id),
        )

    def requeue(self, where: str, args: Iterable[Any] = (), priority: int | None = None) -> int:
        sql = "UPDATE sites SET status = 'queued', recheck = 1" + (", priority = ?" if priority is not None else "")
        params = ([priority] if priority is not None else []) + list(args)
        return self.x(sql + f" WHERE status != 'retired' AND ({where})", params).rowcount

    # --- visits and findings --------------------------------------------------------------------------------------
    def record_visit(self, site_id: int, bundle, worker: str, status: str, seconds: float) -> None:
        findings = bundle.data.get("findings", [])
        errors = [f for f in findings if f["category"] == "error" and not f.get("known")]
        with self.tx():
            self.x(
                "INSERT OR REPLACE INTO visits (id, site, url, started, seconds, worker, build, browsers, status,"
                " errors,"
                " infos, first_error, path, recording) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bundle.id,
                    site_id,
                    bundle.data["url"],
                    bundle.data.get("started") or now(),
                    round(seconds, 1),
                    worker,
                    (bundle.data.get("build") or {}).get("commit"),
                    ",".join(bundle.data.get("browsers", {})),
                    status,
                    len(errors),
                    sum(1 for f in findings if f["category"] == "info"),
                    errors[0]["signature"] if errors else None,
                    str(bundle.path),
                    int(bundle.has("recording.warc.gz")),
                ),
            )
            self.x("DELETE FROM findings WHERE visit = ?", (bundle.id,))
            for f in findings:
                self.x(
                    "INSERT INTO findings (visit, site, signature, category, detector, text, data, known)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        bundle.id,
                        site_id,
                        f["signature"],
                        f["category"],
                        f.get("detector"),
                        f.get("text"),
                        json.dumps(f.get("data") or {}, ensure_ascii=False),
                        f.get("known"),
                    ),
                )
            for f in findings:
                table = "candidates" if f["category"] == "error" and not f.get("known") else None
                if f["signature"].startswith("missing-feature:"):
                    table = "missing_features"
                if table is None:
                    continue
                sites = self.one(
                    "SELECT COUNT(DISTINCT site) FROM findings WHERE signature = ? AND category = ?",
                    (f["signature"], f["category"]),
                )[0]
                if table == "candidates":
                    self.x(
                        "INSERT INTO candidates (signature, text, first_seen, last_seen, sites) VALUES (?, ?, ?, ?, ?)"
                        " ON CONFLICT(signature) DO UPDATE SET last_seen = excluded.last_seen, sites = excluded.sites",
                        (f["signature"], f.get("text"), now(), now(), sites),
                    )
                else:
                    self.x(
                        "INSERT INTO missing_features (signature, first_seen, last_seen, sites) VALUES (?, ?, ?, ?)"
                        " ON CONFLICT(signature) DO UPDATE SET last_seen = excluded.last_seen, sites = excluded.sites",
                        (f["signature"], now(), now(), sites),
                    )

    def site_exceptions(self, domain: str) -> dict[str, str]:
        return {
            r["signature"]: r["reason"] for r in self.q("SELECT * FROM site_exceptions WHERE domain = ?", (domain,))
        }

    # --- runs -----------------------------------------------------------------------------------------------------
    def start_run(self, kind: str) -> int:
        return self.x("INSERT INTO runs (kind, started, status) VALUES (?, ?, 'running')", (kind, now())).lastrowid

    def finish_run(self, run_id: int, status: str, summary: dict | str) -> None:
        if not isinstance(summary, str):
            summary = json.dumps(summary, ensure_ascii=False)
        self.x("UPDATE runs SET finished = ?, status = ?, summary = ? WHERE id = ?", (now(), status, summary, run_id))


class _Tx:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        return False


def _split(sql: str) -> list[str]:
    """Splits a migration into statements (no semicolons inside our statements, comments are dropped)."""
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
