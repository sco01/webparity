# SPDX-License-Identifier: BSD-2-Clause
"""Canary pages: our own pages that misbehave only in Ladybird (its user agent contains "Ladybird"), one per detector,
and one clean control page that must give no finding.

The nightly chain visits them: if a detector doesn't fire, the detector or the collection is broken, not Ladybird.
The pages are served from memory on 127.0.0.1 (the browsers reach loopback directly, not through the proxy).
``/fail.js`` closes the connection without an answer when Ladybird asks, so Ladybird logs a failed load while Chromium
loads the file. ``/crash-me`` makes the server send SIGSEGV to the newest WebContent process (the worker shares the
PID namespace with the browsers), a synthetic crash that doesn't depend on a real crash bug staying unfixed.

Each canary names the signature it must produce in the previous explorer's terms; ``expected()`` translates it.
Detectors without a canary: memory leaks (need a real leak), video (needs a video file), missing features (info).
"""

from __future__ import annotations

import http.server
import os
import signal
import threading

LB = "<script>const LB = navigator.userAgent.includes('Ladybird');</script>"
STYLE = (
    "<style>body{font:16px sans-serif;margin:20px;background:#f4f1ea;color:#222}"
    ".r{display:inline-block;width:90px;height:60px;margin:4px}</style>"
)
TEXT = "".join(
    f"<p>Paragraph {i}: Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt.</p>"
    for i in range(12)
)
COLORS = "".join(f"<span class=r style='background:hsl({i * 37 % 360},70%,50%)'></span>" for i in range(24))
OK_SVG = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' width='80' height='60'%3E%3Crect width='80' height='60' fill='%23c33'/%3E%3C/svg%3E"
)
LONG = "This text is long enough to be counted "


def page(title: str, body: str, head: str = "") -> str:
    return (
        f"<!doctype html><html lang=en><head><meta charset=utf-8><title>{title}</title>{STYLE}{LB}{head}</head>"
        f"<body><h1>{title}</h1>{body}</body></html>"
    )


FRAME = page("Frame", COLORS * 3)
CLEAN = page(
    "Clean control page",
    TEXT
    + COLORS
    + "<iframe src='/frame.html' width=500 height=200></iframe><canvas id=c width=400 height=200></canvas>"
    + f'<img src="{OK_SVG}" width=80 height=60>'
    + "<header><button aria-expanded=false aria-controls=m"
    " onclick=\"this.setAttribute('aria-expanded','true');m.hidden=false\">Menu</button>"
    "<ul id=m hidden><li>One</li><li>Two</li></ul></header><div style='height:2500px'></div><div id=end></div>"
    "<script>const x=c.getContext('2d');for(let i=0;i<40;i++){x.fillStyle=`hsl(${i*9},80%,50%)`;x.fillRect(i*10,0,10,200)}"
    "new IntersectionObserver(e=>{if(e[0].isIntersecting){for(let i=0;i<80;i++){const d=document.createElement('div');"
    "d.textContent='More '+i;end.append(d)}}}).observe(end)</script>",
)

# (name, expected signature prefix in the previous explorer's terms, None for the clean page; html)
CANARIES: list[tuple[str, str | None, str]] = [
    ("clean", None, CLEAN),
    (
        "content",
        "mindre-innehåll",
        page("Content", "<div id=t>" + TEXT * 2 + "</div><script>if(LB)t.remove()</script>"),
    ),
    (
        "iframe",
        "tom-iframe",
        page(
            "Iframe", "<iframe id=f width=600 height=300></iframe><script>f.src=LB?'about:blank':'/frame.html'</script>"
        ),
    ),
    (
        "canvas",
        "tom-canvas",
        page(
            "Canvas",
            "<canvas id=c width=600 height=300></canvas><script>if(!LB){const x=c.getContext('2d');"
            "for(let i=0;i<60;i++){x.fillStyle=`hsl(${i*6},80%,50%)`;x.fillRect(i*10,0,10,300)}}</script>",
        ),
    ),
    (
        "images",
        "trasiga-bilder",
        page(
            "Images",
            "".join(f"<img id=b{i} width=80 height=60>" for i in range(5))
            + f"<script>for(let i=0;i<5;i++)document.getElementById('b'+i).src=LB?'/missing-'+i+'.png':\"{OK_SVG}\""
            "</script>",
        ),
    ),
    (
        "fonts",
        "typsnittsfel",
        page(
            "Fonts",
            TEXT + "<script>{const f=new FontFace('Canary',LB?'url(/missing.woff2)':'url(/font.ttf)');"
            "document.fonts.add(f);f.load().catch(()=>{})}</script>",
        ),
    ),
    (
        "exception",
        "js-fel:",
        page(
            "Exception",
            TEXT + "<script>if(LB)for(let i=0;i<3;i++)setTimeout(()=>{throw new Error('canary')},10*i)</script>",
        ),
    ),
    (
        "scrolling",
        "rullning-låst",
        page("Scrolling", TEXT * 5 + "<script>if(LB)document.body.style.overflow='hidden'</script>"),
    ),
    (
        "overflow",
        "överflödning",
        page(
            "Overflow",
            TEXT + "<script>if(LB){const d=document.createElement('div');d.style.width='3000px';d.textContent='wide';"
            "document.body.append(d)}</script>",
        ),
    ),
    (
        "zero-size",
        "nollstora-textelement",
        page(
            "Zero size",
            TEXT + "<div id=n></div><script>for(let i=0;i<8;i++){const d=document.createElement('div');"
            f"d.textContent='{LONG}'+i;if(LB)d.style.cssText='width:0;height:0;overflow:hidden';n.append(d)}}</script>",
        ),
    ),
    (
        "offscreen",
        "utanför-sidan",
        page(
            "Offscreen",
            TEXT + "<div id=n></div><script>for(let i=0;i<8;i++){const d=document.createElement('div');"
            f"d.textContent='{LONG}'+i;if(LB)d.style.cssText='position:absolute;left:-5000px';n.append(d)}}</script>",
        ),
    ),
    (
        "css",
        "css-regler",
        page(
            "CSS",
            TEXT,
            "<style id=s>"
            + "".join(f".k{i}{{color:#{i:03x}}}" for i in range(40))
            + "</style><script>addEventListener('DOMContentLoaded',()=>{if(LB)for(let i=0;i<20;i++)s.sheet.deleteRule(0)})"
            "</script>",
        ),
    ),
    (
        "error-text",
        "feltext:",
        page(
            "Error text",
            TEXT + "<p id=f></p><script>if(LB)f.textContent='Something went wrong. Please try again.'</script>",
        ),
    ),
    (
        "error-reports",
        "felrapporter",
        page(
            "Error reports",
            TEXT + "<script>if(LB)for(let i=0;i<3;i++)fetch('/api/1/envelope/?n='+i).catch(()=>{})</script>",
        ),
    ),
    ("failed-load", "misslyckade-laddningar", page("Failed load", TEXT, "<script src='/fail.js'></script>")),
    (
        "missing-resource",
        "saknade-resurser",
        page(
            "Missing resource",
            TEXT
            + "<script>if(!LB){for(const n of ['extra','extra2','extra3']){const s=document.createElement('script');"
            "s.src='/'+n+'.js';document.head.append(s)}}</script>",
        ),
    ),
    (
        "blank-page",
        "tom-sida",
        page(
            "Blank page",
            TEXT + COLORS * 6,
            "<style>html.lb,html.lb *{visibility:hidden!important;background:#fff!important}</style>"
            "<script>if(LB)document.documentElement.className='lb'</script>",
        ),
    ),
    (
        "image-differs",
        "bild-skiljer",
        page(
            "Image differs",
            TEXT + COLORS,
            "<style>html.lb body{background:#123;color:#eee}html.lb .r{filter:invert(1)}</style>"
            "<script>if(LB)document.documentElement.className='lb'</script>",
        ),
    ),
    (
        "scroll",
        "scroll",
        page(
            "Scroll",
            TEXT + "<div style='height:3000px'></div><div id=end></div><script>if(!LB)new IntersectionObserver(e=>{"
            "if(e[0].isIntersecting){for(let i=0;i<120;i++){const d=document.createElement('div');d.textContent='More '+i;"
            "end.append(d)}}}).observe(end)</script>",
        ),
    ),
    (
        "menu",
        "meny",
        page(
            "Menu",
            "<header><button id=b aria-expanded=false aria-controls=m>Menu</button>"
            "<ul id=m hidden><li>One</li><li>Two</li></ul></header>"
            + TEXT
            + "<script>if(!LB)b.onclick=()=>{b.setAttribute('aria-expanded','true');m.hidden=false}</script>",
        ),
    ),
    (
        "empty-root",
        "tom-rot",
        page("Empty root", "<div id=root></div><script>if(!LB)root.innerHTML=" + repr(TEXT) + "</script>"),
    ),
    (
        "not-loading",
        "laddar-inte",
        page(
            "Not loading", TEXT + "<script>if(LB){document.title='404 Not Found';document.body.textContent=''}</script>"
        ),
    ),
    (
        "more-resources",
        "fler-resurser:img:svg",
        page(
            "More resources", TEXT + "<script>if(LB)for(let i=0;i<40;i++){new Image().src='/images/'+i+'.svg'}</script>"
        ),
    ),
    (
        "layout",
        "layout:",
        page(
            "Layout",
            TEXT + "<div style='display:flex;gap:8px'><div style='width:300px;height:120px;background:#c33'></div>"
            "<div id=x style='width:300px;height:120px;background:#33c'></div></div><script>if(LB)x.style.width='40px'</script>",
        ),
    ),
    (
        "cpu",
        "cpu-vila",
        page(
            "CPU",
            TEXT
            + "<script>if(LB)setInterval(()=>{const t=performance.now();while(performance.now()-t<30);},40)</script>",
        ),
    ),
    (
        "sluggish",
        "seg",
        page(
            "Sluggish",
            TEXT + "<script>if(LB)setTimeout(()=>setInterval(()=>{const t=performance.now();"
            "while(performance.now()-t<1300);},20),4000)</script>",
        ),
    ),
    (
        "network-loop",
        "nät-loop:",
        page(
            "Network loop",
            TEXT + "<script>if(LB){for(let i=0;i<80;i++)fetch('/repeated.txt',{cache:'no-store'})}</script>",
        ),
    ),
    (
        "slow",
        "långsam",
        page(
            "Slow",
            TEXT + "<div id=d></div><script>if(LB){let n=0;const i=setInterval(()=>{d.textContent+='Tick '+(n++)+' ';"
            "if(n>40)clearInterval(i)},400)}</script>",
        ),
    ),
    (
        "crash",
        "krasch:",
        page(
            "Crash",
            TEXT
            + "<script>if(LB)addEventListener('load',()=>setTimeout(()=>fetch('/crash-me',{cache:'no-store'}),500))"
            "</script>",
        ),
    ),
]


def _font() -> bytes:
    for path in (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            continue
    return b""


FILES = {
    "/frame.html": ("text/html", FRAME),
    "/extra.js": ("text/javascript", "window.extra=1;"),
    "/extra2.js": ("text/javascript", "window.extra2=1;"),
    "/extra3.js": ("text/javascript", "window.extra3=1;"),
    "/fail.js": ("text/javascript", "window.ok=1;"),
    "/repeated.txt": ("text/plain", "x"),
}


def expected(name: str) -> str | None:
    """The signature prefix a canary must produce, in webparity's terms."""
    old = next((c[1] for c in CANARIES if c[0] == name), None)
    if old is None:
        return None
    try:
        from webparity.signatures import translate_signature
    except ImportError:  # pragma: no cover
        return old
    return translate_signature(old)


def crash_newest_webcontent() -> bool:
    """Sends SIGSEGV to the most recently started WebContent process visible in /proc."""
    procs = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() != "WebContent":
                    continue
            with open(f"/proc/{pid}/stat") as f:
                start = int(f.read().rsplit(")", 1)[1].split()[19])
            procs.append((start, int(pid)))
        except (OSError, ValueError, IndexError):
            pass
    if not procs:
        return False
    os.kill(max(procs)[1], signal.SIGSEGV)
    return True


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, content_type: str | None = None, body: bytes = b"") -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        ladybird = "Ladybird" in (self.headers.get("User-Agent") or "")
        if path == "/fail.js" and ladybird:
            self.close_connection = True
            return  # no answer at all: Ladybird logs a failed load
        if path == "/crash-me" and ladybird:
            self._send(204)
            threading.Timer(0.2, crash_newest_webcontent).start()
            return
        if path == "/font.ttf":
            font = _font()
            return self._send(200 if font else 404, "font/ttf", font)
        if path.startswith("/c/") and path.endswith(".html"):
            canary = next((c for c in CANARIES if c[0] == path[3:-5]), None)
            content_type, data = ("text/html", canary[2]) if canary else (None, None)
        else:
            content_type, data = FILES.get(path, (None, None))
        if data is None:
            return self._send(404)
        self._send(200, content_type + "; charset=utf-8", data.encode())


class _Server(http.server.ThreadingHTTPServer):
    request_queue_size = 1024  # the default (5) drops Ladybird's many parallel connections
    daemon_threads = True


def start_server() -> tuple[str, _Server]:
    """Starts the canary server on a free port; returns (base URL, server)."""
    server = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


def entries(base: str, names: list[str] | None = None) -> list[dict]:
    """Canary entries for the nightly chain: {"name", "url", "expect": [prefix] or [] for the clean page}."""
    out = []
    for name, _old, _html in CANARIES:
        if names and name not in names:
            continue
        exp = expected(name)
        out.append({"name": name, "url": f"{base}/c/{name}.html", "expect": [exp] if exp else []})
    return out
