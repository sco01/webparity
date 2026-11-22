# SPDX-License-Identifier: BSD-2-Clause
"""The HTTP server: routing, security headers, the read-only database connection and the bundle file guard.

Everything is read-only except the review page's three actions (save a draft's fields, reset them, mark a draft as
filed), which are POSTs of JSON to ``/api/review/<id>/<action>`` with the header ``X-Webparity-Review: 1`` and, when
the browser sends one, an ``Origin`` equal to this server's (``http://`` or ``https://`` and the ``Host``, so it also
works behind a reverse proxy that terminates TLS). A page on another origin cannot send that header without
a CORS preflight, which this server never answers, so it cannot make these requests."""

from __future__ import annotations

import json
import logging
import re
import socket
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from webparity import metrics
from webparity.web import pages

log = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}

# Bundle files that may be served, and how. Anything else (DOM snapshots, recordings) is not served: site content
# must never be rendered on our origin.
FILE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".json": "text/plain; charset=utf-8",
    ".jsonl": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
VISIT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")


class ReadOnlyDB:
    """A read-only SQLite connection with the ``q``/``one`` helpers of ``webparity.db.DB``; one per request."""

    def __init__(self, path: Path):
        uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("PRAGMA query_only=ON")

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args=()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(args)).fetchone()

    def close(self) -> None:
        self.conn.close()


def bundle_dir(config: dict, visit_row) -> Path | None:
    """The visit's bundle directory: ``visits.path`` if it exists here, otherwise derived from the visit id."""
    if visit_row is None:
        return None
    if visit_row["path"]:
        p = Path(visit_row["path"])
        if p.is_dir():
            return p
    vid = visit_row["id"]
    if VISIT_ID.match(vid) and len(vid) >= 8:
        p = Path(config["data_dir"]) / "visits" / f"{vid[0:4]}-{vid[4:6]}-{vid[6:8]}" / vid
        if p.is_dir():
            return p
    return None


def safe_file(directory: Path, name: str) -> Path | None:
    """``directory/name`` if ``name`` is a plain file name of a servable type inside ``directory``, else None."""
    if not SAFE_NAME.match(name) or ".." in name:
        return None
    suffix = Path(name).suffix.lower()
    if suffix not in FILE_TYPES:
        return None
    root = directory.resolve()
    p = (root / name).resolve()
    if p.parent != root or not p.is_file():
        return None
    return p


class Handler(BaseHTTPRequestHandler):
    server_version = "webparity"
    sys_version = ""
    config: dict[str, Any] = {}
    db_path: Path = Path()

    # --- plumbing ------------------------------------------------------------------------------------------------
    def log_message(self, format: str, *args) -> None:
        log.info("%s %s", self.address_string(), format % args)

    def send(self, status: int, body: bytes | str, content_type: str = "text/html; charset=utf-8", extra=None) -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def not_found(self, what: str = "Not found") -> None:
        self.send(404, pages.error_page(404, what))

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        m = re.match(r"^/api/review/(\d+)/(fields|reset|mark)$", urllib.parse.urlsplit(self.path).path)
        if not m:
            return self.send(405, pages.error_page(405, "This interface is read-only."), extra={"Allow": "GET, HEAD"})
        return self.review_action(int(m.group(1)), m.group(2))

    def do_PUT(self) -> None:
        self.send(405, pages.error_page(405, "This interface is read-only."), extra={"Allow": "GET, HEAD"})

    do_DELETE = do_PATCH = do_PUT

    def send_json(self, status: int, value) -> None:
        self.send(status, json.dumps(value, ensure_ascii=False), "application/json; charset=utf-8")

    def review_action(self, issue_id: int, action: str) -> None:
        from webparity.db import DB
        from webparity.issues import review

        if self.headers.get("X-Webparity-Review") != "1":
            return self.send_json(403, {"error": "missing X-Webparity-Review header"})
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        # https when a reverse proxy terminates TLS in front of this server
        if origin and origin not in (f"http://{host}", f"https://{host}"):
            return self.send_json(403, {"error": "cross-origin request"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send_json(415, {"error": "JSON only"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            return self.send_json(413, {"error": "too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send_json(400, {"error": "not JSON"})
        db = DB(self.db_path)
        try:
            if action == "fields":
                fields = body.get("fields")
                if not isinstance(fields, dict):
                    return self.send_json(400, {"error": "fields must be an object"})
                return self.send_json(200, review.save_fields(self.config, db, issue_id, fields))
            if action == "reset":
                return self.send_json(200, review.reset(self.config, db, issue_id))
            number = body.get("number")
            if number is not None and (not isinstance(number, int) or isinstance(number, bool) or number < 0):
                return self.send_json(400, {"error": "number must be a positive integer or null"})
            return self.send_json(200, review.mark(self.config, db, issue_id, number))
        except KeyError as e:
            return self.send_json(404, {"error": str(e).strip("'")})
        except ValueError as e:
            return self.send_json(409, {"error": str(e)})
        except sqlite3.Error as e:
            log.exception("review action failed")
            return self.send_json(503, {"error": f"database error: {e}"})
        finally:
            db.conn.close()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        if path == "/healthz":
            return self.healthz()
        try:
            db = ReadOnlyDB(self.db_path)
        except sqlite3.Error as e:
            return self.send(503, pages.error_page(503, f"Database unavailable: {e}"))
        try:
            self.route(db, path, query)
        except sqlite3.Error as e:
            log.exception("database error on %s", path)
            self.send(503, pages.error_page(503, f"Database error: {e}"))
        except BrokenPipeError:
            pass
        except Exception:
            log.exception("error on %s", path)
            self.send(500, pages.error_page(500, "Internal error"))
        finally:
            db.close()

    # --- routes --------------------------------------------------------------------------------------------------
    def route(self, db: ReadOnlyDB, path: str, query: dict[str, str]) -> None:
        c = self.config
        if path == "/":
            return self.send(200, pages.health(c, db))
        if path == "/metrics":
            return self.send(200, metrics.render(db), "text/plain; version=0.0.4; charset=utf-8")
        if path == "/findings":
            return self.send(200, pages.findings(c, db, query.get("family", ""), query.get("detector", "")))
        if path == "/candidates":
            return self.send(200, pages.candidates(c, db, query.get("status", "")))
        if path.startswith("/candidates/"):
            signature = urllib.parse.unquote(path[len("/candidates/") :])
            page = pages.candidate(c, db, signature, lambda row: bundle_dir(c, row))
            return self.send(200, page) if page else self.not_found("No such candidate")
        if path == "/issues":
            return self.send(200, pages.issues(c, db))
        if path == "/review":
            return self.send(200, pages.review(c, db))
        if path == "/api/review":
            from webparity.issues import review

            return self.send_json(200, review.items(c, db))
        if path == "/static/review.js":
            js = (Path(__file__).parent / "static" / "review.js").read_bytes()
            return self.send(200, js, "text/javascript; charset=utf-8")
        m = re.match(r"^/review/image/(\d+)/([^/]+)$", path)
        if m:
            from webparity.issues.review import draft_path

            row = db.one("SELECT draft FROM issues WHERE id = ?", (int(m.group(1)),))
            draft = draft_path(c, row["draft"]) if row else None
            folder = draft.parent if draft else None
            return self.bundle_file(folder, urllib.parse.unquote(m.group(2)))
        m = re.match(r"^/visit/([^/]+)(?:/file/([^/]+))?$", path)
        if m:
            vid = urllib.parse.unquote(m.group(1))
            row = db.one("SELECT * FROM visits WHERE id = ?", (vid,))
            if row is None:
                return self.not_found("No such visit")
            directory = bundle_dir(c, row)
            if m.group(2) is not None:
                return self.bundle_file(directory, urllib.parse.unquote(m.group(2)))
            return self.send(200, pages.visit(c, db, row, directory))
        return self.not_found()

    def bundle_file(self, directory: Path | None, name: str) -> None:
        p = safe_file(directory, name) if directory else None
        if p is None:
            return self.not_found("No such file")
        content_type = FILE_TYPES[p.suffix.lower()]
        self.send(200, p.read_bytes(), content_type, extra={"Cache-Control": "private, max-age=3600"})

    def healthz(self) -> None:
        out: dict[str, Any] = {"ok": False, "visits_last_hour": None, "db": "ok"}
        try:
            db = ReadOnlyDB(self.db_path)
            try:
                out["visits_last_hour"] = metrics.visits_last_hour(db)
                out["ok"] = True
            finally:
                db.close()
        except sqlite3.Error as e:
            out["db"] = f"error: {e}"
        self.send(200 if out["ok"] else 503, json.dumps(out), "application/json")


def _listen(config: dict) -> tuple[str, int]:
    listen = str((config.get("web") or {}).get("listen") or "127.0.0.1:8700")
    host, _, port = listen.rpartition(":")
    return (host.strip("[]") or "127.0.0.1"), int(port)


def make_server(config: dict, db_path: str | Path | None = None, address: tuple[str, int] | None = None):
    """The server, not yet serving. Creates the database (schema) if it does not exist yet."""
    from webparity.db import DB

    path = Path(db_path) if db_path else Path(config["data_dir"]) / "webparity.db"
    if not path.exists():
        DB(path).conn.close()
    handler = type("BoundHandler", (Handler,), {"config": config, "db_path": path})
    host, port = address or _listen(config)
    if ":" in host:
        server_class = type("Server6", (ThreadingHTTPServer,), {"address_family": socket.AF_INET6})
    else:
        server_class = ThreadingHTTPServer
    server = server_class((host, port), handler)
    server.daemon_threads = True
    return server


def serve(config: dict, db_path: str | Path | None = None) -> None:
    """Serves the web interface until interrupted."""
    server = make_server(config, db_path)
    host, port = server.server_address[:2]
    log.warning("webparity web interface on http://%s:%s/", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
