# SPDX-License-Identifier: BSD-2-Clause
"""mitmproxy addon: record traffic to WARC, or replay it offline.

Modes, switched over HTTP on 127.0.0.1:8791 (WEBPARITY_PROXY_CONTROL_PORT):

    POST /pass               pass traffic through, record nothing (the default)
    POST /record?name=NAME   append every exchange to /data/recordings/NAME.warc.gz
    POST /replay?name=NAME   answer only from NAME.warc.gz; no connection leaves the proxy, and requests that are not
                             in the recording get a 404 and are listed as missing
    GET  /status             the mode, the recording's name, records written or loaded, and missing URLs

The same URL can get different responses in different browsers (sites look at the user agent), so a replay first uses
the response recorded for the same user agent, then any response for that URL.

At start the addon copies mitmproxy's CA certificate (not its key, which stays in the proxy's private confdir) to
$WEBPARITY_CA_EXPORT/proxy-ca.pem and writes the SHA-256 hash of its public key (for Chromium's
--ignore-certificate-errors-spki-list) to proxy-ca.spki.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mitmproxy import ctx, http
from warcio.archiveiterator import ArchiveIterator
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

RECORDINGS = Path(os.environ.get("WEBPARITY_RECORDINGS", "/data/recordings"))
CONTROL_PORT = int(os.environ.get("WEBPARITY_PROXY_CONTROL_PORT", "8791"))
# Hop-by-hop and framing headers that don't describe the stored body
DROP_HEADERS = {"transfer-encoding", "connection", "keep-alive", "proxy-connection"}
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mode = "pass"
        self.name: str | None = None
        self.file = None
        self.writer: WARCWriter | None = None
        self.records = 0
        self.index: dict[tuple[str, str], list[dict]] = {}
        self.missing: list[str] = []
        # Exchanges mitmproxy answered with its own 502 (the server closed the connection, reset it, timed out, ...)
        self.upstream_errors: list[dict] = []

    # --- control --------------------------------------------------------------------------------------------------
    def _close(self) -> None:
        if self.file is not None:
            self.file.close()
        self.file, self.writer = None, None

    def set_pass(self) -> dict:
        """Stops recording or replaying; the reply has the finished recording's status under ``previous``."""
        with self.lock:
            previous = self.status_locked()
            self._close()
            self.mode, self.name, self.records, self.index, self.missing = "pass", None, 0, {}, []
            self.upstream_errors = []
            return {**self.status_locked(), "previous": previous}

    def set_record(self, name: str) -> dict:
        with self.lock:
            self._close()
            RECORDINGS.mkdir(parents=True, exist_ok=True)
            self.file = open(RECORDINGS / f"{name}.warc.gz", "ab")
            self.writer = WARCWriter(self.file, gzip=True)
            self.mode, self.name, self.records, self.index, self.missing = "record", name, 0, {}, []
            self.upstream_errors = []
            return self.status_locked()

    def set_replay(self, name: str) -> dict:
        index: dict[tuple[str, str], list[dict]] = {}
        requests: dict[str, dict] = {}
        count = 0
        with open(RECORDINGS / f"{name}.warc.gz", "rb") as f:
            for record in ArchiveIterator(f):
                rec_type = record.rec_type
                rid = record.rec_headers.get_header("WARC-Record-ID")
                if rec_type == "request":
                    requests[rid] = {
                        "method": record.http_headers.protocol,
                        "ua": record.http_headers.get_header("User-Agent") or "",
                    }
                elif rec_type == "response":
                    url = record.rec_headers.get_header("WARC-Target-URI")
                    status = int(record.http_headers.get_statuscode())
                    headers = [(k, v) for k, v in record.http_headers.headers if k.lower() not in DROP_HEADERS]
                    body = record.raw_stream.read()
                    request_id = record.rec_headers.get_header("WARC-Concurrent-To")
                    entry = {"status": status, "headers": headers, "body": body, "request": request_id}
                    method = record.rec_headers.get_header("WARC-Webparity-Method") or "GET"
                    index.setdefault((method, url), []).append(entry)
                    count += 1
        for entries in index.values():
            for entry in entries:
                entry["ua"] = requests.get(entry.pop("request"), {}).get("ua", "")
        with self.lock:
            self._close()
            self.mode, self.name, self.records, self.index, self.missing = "replay", name, count, index, []
            return self.status_locked()

    def status_locked(self) -> dict:
        return {
            "mode": self.mode,
            "name": self.name,
            "records": self.records,
            "missing": self.missing[-200:],
            "upstream_errors": self.upstream_errors[-200:],
        }

    def status(self) -> dict:
        with self.lock:
            return self.status_locked()

    def note_error(self, flow: http.HTTPFlow) -> None:
        with self.lock:
            if self.mode == "record" and len(self.upstream_errors) < 1000:
                self.upstream_errors.append(
                    {
                        "url": flow.request.pretty_url[:300],
                        "error": str(flow.error.msg if flow.error else "")[:200],
                        "user_agent": flow.request.headers.get("User-Agent", "")[:200],
                    }
                )

    # --- traffic --------------------------------------------------------------------------------------------------
    def write(self, flow: http.HTTPFlow) -> None:
        req, resp = flow.request, flow.response
        url = req.pretty_url
        reason = resp.reason or (
            HTTPStatus(resp.status_code).phrase if resp.status_code in HTTPStatus._value2member_map_ else ""
        )
        resp_headers = [(k, v) for k, v in resp.headers.items(multi=True) if k.lower() not in DROP_HEADERS]
        req_headers = [(k, v) for k, v in req.headers.items(multi=True) if k.lower() not in DROP_HEADERS]
        with self.lock:
            if self.writer is None:
                return
            request = self.writer.create_warc_record(
                url,
                "request",
                payload=io.BytesIO(req.raw_content or b""),
                http_headers=StatusAndHeaders(f"{req.method} {req.path} HTTP/1.1", req_headers, is_http_request=True),
            )
            response = self.writer.create_warc_record(
                url,
                "response",
                payload=io.BytesIO(resp.raw_content or b""),
                http_headers=StatusAndHeaders(f"{resp.status_code} {reason}", resp_headers, protocol="HTTP/1.1"),
                warc_headers_dict={"WARC-Webparity-Method": req.method},
            )
            response.rec_headers.add_header("WARC-Concurrent-To", request.rec_headers.get_header("WARC-Record-ID"))
            self.writer.write_record(request)
            self.writer.write_record(response)
            self.records += 1
            self.file.flush()

    def answer(self, flow: http.HTTPFlow) -> None:
        req = flow.request
        key = (req.method, req.pretty_url)
        ua = req.headers.get("User-Agent", "")
        with self.lock:
            entries = self.index.get(key) or []
            entry = next((e for e in entries if e["ua"] == ua), entries[0] if entries else None)
            if entry is None:
                self.missing.append(f"{req.method} {req.pretty_url}")
                flow.response = http.Response.make(404, b"not in the recording", {"X-Webparity-Missing": "1"})
                flow.response.http_version = req.http_version
                return
        headers = [(k.encode("latin-1", "replace"), v.encode("latin-1", "replace")) for k, v in entry["headers"]]
        response = http.Response.make(entry["status"], b"", headers)
        response.raw_content = entry["body"]  # stored as sent: still encoded (gzip, br) as Content-Encoding says
        response.http_version = req.http_version  # an HTTP/1.1 response on an HTTP/2 stream breaks strict clients
        response.headers["content-length"] = str(len(entry["body"]))  # make() set it for the empty placeholder body
        flow.response = response


RECORDER = Recorder()


class Control(BaseHTTPRequestHandler):
    def _reply(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self) -> bool:
        # The control port shares 127.0.0.1 with the browsers, so pages could reach it. A custom header can't be sent
        # cross-origin without a CORS preflight, which this server never answers, so only the worker gets through.
        if self.headers.get("X-Webparity-Control") != "1":
            self._reply(403, {"error": "missing X-Webparity-Control header"})
            return False
        return True

    def do_GET(self):  # noqa: N802
        if not self._allowed():
            return
        if self.path == "/status":
            self._reply(200, RECORDER.status())
        else:
            self._reply(404, {"error": "unknown path"})

    def do_POST(self):  # noqa: N802
        if not self._allowed():
            return
        parsed = urllib.parse.urlsplit(self.path)
        name = (urllib.parse.parse_qs(parsed.query).get("name") or [""])[0]
        try:
            if parsed.path == "/pass":
                self._reply(200, RECORDER.set_pass())
            elif parsed.path in ("/record", "/replay"):
                if not NAME_RE.match(name):
                    self._reply(400, {"error": "name must match [A-Za-z0-9._-]{1,120}"})
                elif parsed.path == "/record":
                    self._reply(200, RECORDER.set_record(name))
                else:
                    self._reply(200, RECORDER.set_replay(name))
            else:
                self._reply(404, {"error": "unknown path"})
        except FileNotFoundError:
            self._reply(404, {"error": f"no recording named {name}"})

    def log_message(self, *args):
        pass


def _export_ca() -> None:
    """Publishes the CA certificate (never the key) for the browsers: proxy-ca.pem, and proxy-ca.spki for Chromium."""
    confdir = Path(ctx.options.confdir).expanduser()
    export = Path(os.environ.get("WEBPARITY_CA_EXPORT", "/export"))
    cert = confdir / "mitmproxy-ca-cert.pem"
    if not cert.exists():
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    export.mkdir(parents=True, exist_ok=True)
    public = x509.load_pem_x509_certificate(cert.read_bytes()).public_key()
    der = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    (export / "proxy-ca.spki.tmp").write_text(base64.b64encode(hashlib.sha256(der).digest()).decode() + "\n")
    (export / "proxy-ca.spki.tmp").replace(export / "proxy-ca.spki")
    shutil.copyfile(cert, export / "proxy-ca.pem.tmp")
    (export / "proxy-ca.pem.tmp").replace(export / "proxy-ca.pem")


def _private(host: str) -> bool:
    import ipaddress

    host = host.strip("[]").lower()
    if host in ("localhost",) or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified


class WebparityProxy:
    def running(self) -> None:
        _export_ca()
        server = ThreadingHTTPServer(("127.0.0.1", CONTROL_PORT), Control)
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def tls_clienthello(self, data) -> None:
        # Normally mitmproxy completes the server's TLS handshake first to learn its certificate and ALPN; a replay has
        # no server, so answer the browser's handshake from the client hello alone.
        if RECORDER.mode == "replay":
            data.establish_server_tls_first = False

    def server_connect(self, data) -> None:
        if RECORDER.mode == "replay":  # a replay never reaches the network
            ctx.log.warn(f"webparity replay: blocked a server connection to {data.server.address}")
            data.server.error = "webparity replay: offline"
            return
        # HTTP/1.1 to the servers: mitmproxy 12.3 keeps sending requests on an HTTP/2 connection the server has closed
        # with GOAWAY ("RECV_HEADERS in state ConnectionState.CLOSED", mitmproxy#8580), and every one becomes a 502 – in
        # one browser or both, so visits got differences that weren't there. Over HTTP/1.1 a closed connection is
        # replaced by a new one. The browsers still talk HTTP/2 to the proxy where they negotiate it.
        if not data.server.alpn_offers:
            data.server.alpn_offers = (b"http/1.1",)

    def request(self, flow: http.HTTPFlow) -> None:
        # Pages must not reach the services on the shared loopback (WebDriver, this proxy's control port) or the host's
        # network through the proxy.
        if _private(flow.request.host):
            flow.response = http.Response.make(403, b"webparity: private address", {"X-Webparity-Blocked": "1"})
            return
        if RECORDER.mode == "replay":
            RECORDER.answer(flow)

    def response(self, flow: http.HTTPFlow) -> None:
        if RECORDER.mode == "record" and flow.response is not None:
            RECORDER.write(flow)

    def error(self, flow: http.HTTPFlow) -> None:
        # mitmproxy answers these with its own 502, which a page can't tell from the server's: the visit notes them so
        # that a stylesheet or script lost in one browser isn't reported as a difference (webparity.detectors).
        if flow.response is None:
            RECORDER.note_error(flow)


addons = [WebparityProxy()]
