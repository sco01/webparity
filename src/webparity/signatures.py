# SPDX-License-Identifier: BSD-2-Clause
"""The old explorer's Swedish signatures and statuses, and their English names in webparity.

The old explorer (``matning.py``, ``utforskare.py``, ``lbdb.py``) wrote findings, candidates and known differences with
Swedish signatures (``krasch:SIGSEGV i WebContent``, ``layout:saknas:block>block``). ``translate_signature`` turns any
of them into the webparity signature, so imported findings, candidates, known differences and issues keep matching
what the detectors report now. Signatures that are already English (or unknown) come back unchanged.

The table below is derived from every signature format string in the three files; ``docs/detectors.md`` lists which
detector reports each new family.
"""

from __future__ import annotations

import re

# Old prefix -> new prefix. Longest prefixes first (``mobil-överflödning-dold`` before ``mobil-överflödning``).
# Entries without a trailing colon are whole signatures (or prefixes of them).
PREFIXES: list[tuple[str, str]] = [
    # Crashes and security (crash detector)
    ("krasch:", "crash:"),
    ("säkerhet:", "security:"),
    # Missing features (missing_features, script_error and css detectors)
    ("js-saknas:", "missing-feature:js:"),
    ("css-saknas:", "missing-feature:css:"),
    ("saknas:", "missing-feature:"),
    # Page-level comparison (page detector)
    ("spärrsida-bara-ladybird", "blocked:ladybird-only"),
    ("olika-slutadress", "different-final-site"),
    ("laddar-inte:", "not-loading:"),
    ("laddar-inte", "not-loading"),
    ("kakruta-laser-sidan", "cookie-banner-locks-page"),
    ("kakruta-låser-sidan", "cookie-banner-locks-page"),
    ("mindre-innehåll", "less-content"),
    ("kortare-sida", "shorter-page"),
    ("felrapporter", "error-reports"),
    ("feltext:", "error-text:"),
    ("överflödning-dold", "overflow-hidden"),
    ("överflödning", "overflow"),
    ("tom-rot", "empty-root"),
    ("nollstora-textelement", "zero-size-text"),
    ("utanför-sidan", "off-page-text"),
    ("object-reserv", "object-fallback"),
    # Network and resources (network detector)
    ("resursbuffert-full", "resource-buffer-full"),
    ("chromium-mindre-sida", "reference-smaller-page"),
    ("fler-resurser:", "more-resources:"),
    ("färre-resurser", "fewer-resources"),
    ("misslyckade-laddningar", "failed-loads"),
    ("saknade-resurser-fa", "missing-resources-few"),
    ("saknade-resurser", "missing-resources"),
    ("nät-loop:", "network-loop:"),
    ("omnavigering", "renavigation"),
    # Images and fonts (image detector)
    ("trasiga-bilder", "broken-images"),
    ("typsnitt-inte-laddat-i-chromium", "font-not-loaded-in-reference"),
    ("typsnitt-ersatt", "font-replaced"),
    ("typsnitt-lokalt", "font-local"),
    ("typsnittsfel", "font-error"),
    ("tom-iframe", "empty-iframe"),
    ("tom-canvas", "empty-canvas"),
    ("tom-sida", "blank-page"),
    ("kakruta-skiljer", "cookie-banner-differs"),
    ("bild-skiljer", "image-differs"),
    # CSS (css detector)
    ("css-regler-kända", "css-rules-known"),
    ("css-regler", "css-rules"),
    ("css-tappad:", "css-dropped:"),
    # Script errors (script_error detector)
    ("js-fel-iframe", "script-error-iframe"),
    ("js-fel:", "script-error:"),
    ("konsol:", "console:"),
    # Layout (layout and mobile detectors)
    ("layout-skiljer-mycket", "layout-differs-a-lot"),
    ("mobil-layout:", "mobile-layout:"),
    ("layout:", "layout:"),
    ("mobil-överflödning-ovanför", "mobile-overflow-above"),
    ("mobil-överflödning-dold", "mobile-overflow-hidden"),
    ("mobil-överflödning", "mobile-overflow"),
    # Interaction (scroll, menu, video detectors)
    ("rullning-låst", "scroll-locked"),
    ("scroll", "scroll-lazy-content"),
    ("meny", "menu"),
    ("video", "video:not-started"),
    # Stability and performance (performance detector)
    ("minnesläcka", "memory-leak"),
    ("minne-hogt", "memory-high"),
    ("minne-högt", "memory-high"),
    ("instabil", "unstable"),
    ("cpu-vila", "cpu-idle"),
    ("seg", "slow-response"),
    ("långsam", "slow-load"),
    # Known bugs (the old kanda_monster added a finding per matched issue; webparity sets Finding.known instead)
    ("känd:", "known:"),
]
PREFIXES.sort(key=lambda p: -len(p[0]))

# Signatures that are a whole word: "scroll", "meny", "video", "seg" must not translate "scroll-locked" or "segment".
_WHOLE = {"scroll", "meny", "video", "seg", "laddar-inte", "instabil", "långsam"}

# The second part of layout signatures: layout:<kind>:<parent display>><display>
LAYOUT_KINDS = {"saknas": "missing", "extra": "extra", "bredd": "width", "höjd": "height", "läge": "position"}
# not-loading:<reason>
LOAD_REASONS = {
    "anslutning": "connection",
    "annat": "other",
    "krasch": "crash",
    "xml-flöde": "xml-feed",
    "http2": "http2",
    "ssl": "ssl",
    "dns": "dns",
    "timeout": "timeout",
}
# Words inside crash locations (krasch:SIGSEGV i WebContent, krasch:minnesbrist (OOM), krasch:okänd plats)
CRASH_WORDS = [
    (re.compile(r"^minnesbrist(?= |$)"), "out-of-memory"),
    (re.compile(r"^okänd plats$"), "unknown location"),
    (re.compile(r"^(SIG\w+) i (\S+)$"), r"\1 in \2"),
]
# Known-issue names that were Swedish words (känd:mätning-skärm, känd:utkast:ellips-inline, känd:känd:cv-auto)
ISSUE_WORDS = [("mätning-", "measurement-"), ("utkast:", "draft:"), ("känd:", "known:")]

# Statuses
CATEGORY = {"fel": "error", "info": "info"}
CANDIDATE_STATUS = {
    "ny": "new",
    "undersöks": "investigating",
    "rapporterad": "filed",
    "avfärdad": "dismissed",
    "rättad": "fixed",
    # "borta" (gone at the recheck) has no own status in webparity: the finding disappeared without a known fix
    "borta": "dismissed",
}
SITE_STATUS = {
    "kö": "queued",
    "pågår": "visiting",
    "ren": "done",
    "kandidat": "done",
    "blockerad": "done",
    "död": "done",
    "borta": "done",
    "fel": "failed",
}
# The outcome of a visit (the old site status after a visit)
VISIT_STATUS = {
    "ren": "clean",
    "kandidat": "candidate",
    "blockerad": "blocked",
    "död": "dead",
    "fel": "failed",
    "borta": "gone",
}


def _translate_issue(name: str) -> str:
    for old, new in ISSUE_WORDS:
        if name.startswith(old):
            return new + _translate_issue(name[len(old) :])
    return name


def _translate_rest(new_prefix: str, rest: str) -> str:
    """The part after the prefix, for families whose parts were Swedish words."""
    if new_prefix in ("layout:", "mobile-layout:"):
        kind, sep, tail = rest.partition(":")
        return LAYOUT_KINDS.get(kind, kind) + sep + tail
    if new_prefix in ("crash:", "security:"):
        for pattern, replacement in CRASH_WORDS:
            rest = pattern.sub(replacement, rest)
        return rest
    if new_prefix == "not-loading:":
        return LOAD_REASONS.get(rest, rest)
    if new_prefix in ("more-resources:", "network-loop:"):
        return ":".join("other" if part == "annat" else part for part in rest.split(":"))
    if new_prefix == "css-dropped:":
        prop, sep, kind = rest.rpartition(":")
        return f"{prop}{sep}{'value' if kind == 'värde' else kind}" if sep else rest
    if new_prefix == "font-error":
        # typsnittsfel:woff2+ttf; "lokal" never ended up in a signature but may be in a known prefix
        return rest.replace("lokal", "local")
    if new_prefix == "known:":
        return _translate_issue(rest)
    return rest


def translate_signature(old: str) -> str:
    """The webparity signature for an old explorer signature (or signature prefix); unchanged if not an old one."""
    if not old:
        return old
    for prefix, new in PREFIXES:
        if not old.startswith(prefix):
            continue
        if prefix in _WHOLE and old != prefix and not old.startswith(prefix + ":"):
            continue
        return new + _translate_rest(new if new.endswith(":") or new == "font-error" else "", old[len(prefix) :])
    return old


def translate_status(kind: str, old: str) -> str:
    """``kind`` is ``candidate``, ``site``, ``visit`` or ``category``."""
    table = {"candidate": CANDIDATE_STATUS, "site": SITE_STATUS, "visit": VISIT_STATUS, "category": CATEGORY}[kind]
    return table.get(old, old)


__all__ = ["translate_signature", "translate_status", "PREFIXES"]
