# SPDX-License-Identifier: BSD-2-Clause
"""Smoke test for the browser images: one BiDi session per browser, a small page, a console entry, and text widths
in the generic fonts (equal widths mean the browsers use the same fonts).

Run where the browsers listen on 127.0.0.1 (inside the reference container's network namespace):
    PYTHONPATH=src python3 tools/smoke_images.py
"""

import http.server
import json
import sys
import threading
import time

from webparity.bidi import BidiError, Session

PAGE_HTML = b"<!doctype html><title>smoke</title><script>console.log('SMOKE-LOG')</script>"
PORT = 8099
PAGE = f"http://127.0.0.1:{PORT}/smoke.html"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (the name is set by http.server)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE_HTML)

    def log_message(self, *args):
        pass


MEASURE = """JSON.stringify((() => {
  const c = document.createElement('canvas').getContext('2d'), text = 'The quick brown fox 0123456789';
  const width = font => { c.font = '16px ' + font; return Math.round(c.measureText(text).width * 100) / 100; };
  return {sans: width('sans-serif'), serif: width('serif'), mono: width('monospace'), arial: width('Arial'),
          ua: navigator.userAgent.slice(0, 60)};
})())"""


def check(session: Session) -> dict:
    client = session.client
    client.command("session.subscribe", {"events": ["log.entryAdded"]})
    context = client.command("browsingContext.getTree", {})["contexts"][0]["context"]
    client.command("browsingContext.navigate", {"context": context, "url": PAGE, "wait": "complete"})
    time.sleep(1)
    result = client.command(
        "script.evaluate", {"expression": MEASURE, "target": {"context": context}, "awaitPromise": False}
    )
    out = json.loads(result["result"]["value"])
    out["log"] = any(e.get("params", {}).get("text") == "SMOKE-LOG" for e in client.events)
    return out


def main() -> int:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    starters = {
        "ladybird": lambda: Session.ladybird("http://127.0.0.1:9616"),
        "chromium": lambda: Session.chromium("http://127.0.0.1:9515", ["--window-size=1440,900"]),
        "firefox": lambda: Session.firefox("ws://127.0.0.1:9222"),
    }
    results, failed = {}, False
    for name, start in starters.items():
        try:
            session = start()
        except (BidiError, OSError) as exc:
            print(f"{name}: no session: {exc}")
            failed = True
            continue
        try:
            results[name] = check(session)
            print(f"{name}: {results[name]}")
        except (BidiError, OSError, KeyError, ValueError) as exc:
            print(f"{name}: failed: {exc}")
            failed = True
        finally:
            try:
                session.end()
            except (BidiError, OSError):
                pass
    # The same font gives widths within a fraction of a percent (each engine shapes text slightly differently);
    # a different font (Noto instead of Liberation) differs by several percent.
    for font in ("sans", "serif", "mono", "arial"):
        widths = {name: r[font] for name, r in results.items()}
        spread = (max(widths.values()) - min(widths.values())) / max(widths.values()) if widths else 1
        same = spread < 0.01
        print(f"{font}: {'same font' if same else 'DIFFERENT FONTS'} ({spread:.2%} apart) {widths}")
        failed |= not same
    failed |= not all(r["log"] for r in results.values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
