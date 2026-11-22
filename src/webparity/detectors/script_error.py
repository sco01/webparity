# SPDX-License-Identifier: BSD-2-Clause
"""Script exceptions only in Ladybird, in the top document, during the first load.

Ladybird's exceptions come from its log up to the reload (``log_reload_offset``: when Ladybird leaves the page for the
reload, visibilitychange handlers run and throw - the same happens in Chromium, where nobody sees it; 2026-11-21) and
from the BiDi events of the steps before the reload. An exception counts only when it comes from the top document:
BiDi tags every console entry with ``top``; an exception the events don't place is told apart by its WebContent
process (the top document logs a marker, see ladybird_log.TOP_MARKER; 2026-11-22: reCAPTCHA's anchor frame threw from
www.gstatic.com, a host the top document also loads scripts from), and else falls back to the old host heuristics
(its first stack line's host is a cross-origin frame's host, or a host the top document never fetched scripts from -
2026-11-21: a weather widget in an iframe loaded a CDN's script that threw). The reference's exceptions are its BiDi
``javascript`` entries in the top document before the reload.

Signatures:

- ``script-error:<message>`` (error): at least two exceptions only in Ladybird while the reference had none; the
  message is the most frequent one (``[TypeError] x is not a function``, digits as N, URLs as <url>).
- ``script-error-iframe`` (info): at least two exceptions from cross-origin iframes.
- ``missing-feature:js:<message>`` (info): exceptions that are missing features, not bugs: ``TODO(...)``, ``not
  implemented``, and permissions.query() with a PermissionName Ladybird doesn't know (a TypeError per spec, 2026-11-17).

Exceptions about callbacks from a document that is "not fully active" are not counted (2026-11-17: Ladybird refuses to
run callbacks from a removed iframe and logs it; Chromium and Firefox skip them silently).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from webparity import ladybird_log
from webparity.detectors import Detector
from webparity.detectors._common import OK, events, finding, gate, info, m, reference

FIRST_LOAD_STEPS = ("load", "probe", "idle", "interact")
NOT_IMPLEMENTED = re.compile(r"TODO\(|not implemented|NotImplemented|for enumeration type 'PermissionName'")


def normalize(text: str) -> str:
    """A BiDi or log exception text in the log's form: ``[TypeError] message`` with digits as N and URLs as <url>."""
    t = re.sub(r"^.*?Unhandled JavaScript exception( \(in promise\))?:\s*", "", text or "")
    t = re.sub(r"^Uncaught (\(in promise\) )?", "", t)
    match = re.match(r"^(\w*(?:Error|Exception)): (.*)$", t, re.S)
    if match:
        t = f"[{match.group(1)}] {match.group(2)}"
    return re.sub(r"\d+", "N", re.sub(r"https?://\S+", "<url>", t.splitlines()[0] if t else "")).strip()[:160]


def script_events(bundle, browser: str) -> list[dict]:
    return [
        e
        for e in events(bundle)
        if e.get("browser") == browser
        and e.get("method") == "log.entryAdded"
        and e.get("type") == "javascript"
        and e.get("step") in FIRST_LOAD_STEPS
    ]


class ScriptError(Detector):
    name = "script_error"
    description = "Script exceptions only in Ladybird's top document, before the reload"

    def run(self, bundle):
        if gate(bundle) != OK:
            return []
        ref = reference(bundle)
        lp = m(bundle, "ladybird", "probe")
        parsed = ladybird_log.for_bundle(bundle)
        first = parsed["first"]
        top_pids = set(parsed["full"].get("top_pids") or [])
        exception_pids = first.get("exception_pids") or {}
        exceptions = dict(first["exceptions"])
        hosts = dict(first["exception_hosts"])
        top_msgs: dict[str, int] = {}
        frame_msgs: dict[str, int] = {}
        for e in script_events(bundle, "ladybird"):
            target = top_msgs if e.get("top") else frame_msgs
            k = normalize(e.get("text") or "")
            target[k] = target.get(k, 0) + 1
        for k in {**frame_msgs, **top_msgs}:  # exceptions only the events saw
            if k not in exceptions:
                exceptions[k] = max(top_msgs.get(k, 0), frame_msgs.get(k, 0))
        frame_hosts = set(lp.get("frame_hosts") or [])
        top_hosts = set(lp.get("top_script_hosts") or [])
        page_host = urlsplit(lp.get("url") or "").hostname

        def from_frame(k: str) -> bool:
            if k in top_msgs:
                return False
            if k in frame_msgs:
                return True
            if top_pids and exception_pids.get(k):  # the top document's WebContent process is known
                return not (set(exception_pids[k]) & top_pids)
            h = hosts.get(k)
            return h in frame_hosts or bool(h and top_hosts and frame_hosts and h not in top_hosts and h != page_host)

        own = {k: n for k, n in exceptions.items() if not from_frame(k)}
        frames = {k: n for k, n in exceptions.items() if k not in own}
        out = []
        missing = {k: n for k, n in own.items() if NOT_IMPLEMENTED.search(k)}
        for k, n in sorted(missing.items(), key=lambda kv: -kv[1])[:2]:
            out.append(info(f"missing-feature:js:{k[:100]}", f"A missing feature as a script exception ({n})", count=n))
        own = {k: n for k, n in own.items() if k not in missing and "whose document is not fully active" not in k}
        total = sum(own.values())
        ref_uncaught = sum(1 for e in script_events(bundle, ref) if e.get("top"))
        if total >= 2 and not ref_uncaught:
            top = max(own.items(), key=lambda kv: kv[1])[0]
            out.append(
                finding(
                    f"script-error:{top[:100]}",
                    f"Script exceptions only in Ladybird ({total})",
                    exceptions=dict(sorted(own.items(), key=lambda kv: -kv[1])[:5]),
                    stack=first["exception_stacks"].get(top),
                )
            )
        elif sum(frames.values()) >= 2:
            out.append(
                info(
                    "script-error-iframe",
                    "Script exceptions in a cross-origin iframe",
                    hosts=sorted({hosts.get(k) for k in frames} - {None}),
                    exceptions=dict(sorted(frames.items(), key=lambda kv: -kv[1])[:5]),
                )
            )
        return out
