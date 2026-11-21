# SPDX-License-Identifier: BSD-2-Clause
"""Helpers shared by the detectors: the reference browser, the comparison gate, small parsers and a per-bundle cache.

Ladybird is compared with one reference browser (Chromium when it took part, else the first other browser). Most
comparisons only make sense when both browsers got a real page: ``gate`` says why not, in the old explorer's order -
no usable reference (missing, behind a bot wall, an error page) -> only what Ladybird can tell on its own; the browsers
ended on different sites -> nothing is compared; Ladybird got an error page or a bot wall -> only ``not-loading``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from webparity.findings import ERROR, INFO, Finding

OK = "ok"
NO_REFERENCE = "no-reference"
DIFFERENT_SITE = "different-site"
LADYBIRD_FAILED = "ladybird-failed"


def finding(signature: str, text: str, category: str = ERROR, /, **data: Any) -> Finding:
    return Finding(signature, category, text, data=data)


def info(signature: str, text: str, /, **data: Any) -> Finding:
    return Finding(signature, INFO, text, data=data)


def cache(bundle) -> dict:
    return bundle.__dict__.setdefault("_detector_cache", {})


def reference(bundle) -> str | None:
    names = [b for b in (bundle.data.get("browsers") or {}) if b != "ladybird"]
    return "chromium" if "chromium" in names else (names[0] if names else None)


def m(bundle, browser: str | None, step: str) -> dict:
    """A step's measurements as a dict (never None)."""
    if not browser:
        return {}
    value = bundle.m(browser, step)
    return value if isinstance(value, dict) else {}


def num(d: dict, key: str) -> float:
    v = d.get(key)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def dict_list(d: dict, key: str) -> list[dict]:
    """A probe list of dicts - sites that overwrite built-ins (Array.from ...) can give strings instead (2026-11-09:
    an AttributeError in the comparison)."""
    v = d.get(key)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def domain(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def site_key(host: str) -> str:
    """The approximate registrable domain (the last two labels; three for co.uk and the like)."""
    d = host.split(".")
    if len(d) >= 3 and d[-2] in ("co", "com", "org", "gov", "ac", "net") and len(d[-1]) == 2:
        return ".".join(d[-3:])
    return ".".join(d[-2:])


def norm_url(u: str) -> str:
    try:
        p = urlsplit(u)
        return (p.hostname or "") + p.path
    except ValueError:
        return u


def resource_key(u: str) -> str:
    """A comparison key for a resource in both browsers: host + file name, digits (>= 4) and hex strings (>= 12)
    replaced. The whole address is not enough - a CMS changes directory per request (/2.x/1793711536267/x.css against
    /2.x/1793713895620/x.css), so the same file had different addresses (false alarm 2026-11-08)."""
    try:
        p = urlsplit(u if "://" in u else "https://" + u)
    except ValueError:
        return u
    segments = [x for x in p.path.split("/") if x]
    name = segments[-1] if segments else ""
    name = re.sub(r"[0-9a-f]{12,}", "H", re.sub(r"\d{4,}", "N", name.lower()))
    return f"{p.hostname or ''}/{name}"


def file_type(u: str) -> str:
    match = re.search(r"\.([a-z0-9]{2,5})$", norm_url(u).lower())
    return match.group(1) if match else "other"


def gate(bundle) -> str:
    """Whether Ladybird and the reference can be compared (see the module docstring)."""
    key = "gate"
    c = cache(bundle)
    if key in c:
        return c[key]
    ref = reference(bundle)
    lp, cp = m(bundle, "ladybird", "probe"), m(bundle, ref, "probe")
    if not ref or not cp or cp.get("blocked") or cp.get("error_page") or cp.get("error"):
        result = NO_REFERENCE
    elif (
        lp.get("url")
        and cp.get("url")
        and site_key(domain(lp["url"]))
        and site_key(domain(cp["url"]))
        and site_key(domain(lp["url"])) != site_key(domain(cp["url"]))
        and not lp.get("error_page")
    ):
        # The browsers ended on different sites (2026-11-08: the reference was redirected to a news site): nothing is
        # compared
        result = DIFFERENT_SITE
    elif lp.get("error_page") or lp.get("blocked") or lp.get("error") or not lp:
        result = LADYBIRD_FAILED
    else:
        result = OK
    c[key] = result
    return result


def events(bundle) -> list[dict]:
    c = cache(bundle)
    if "events" not in c:
        c["events"] = bundle.events()
    return c["events"]


def layout_map(bundle, browser: str | None, name: str = "layout") -> list | None:
    if not browser:
        return None
    c = cache(bundle)
    key = f"layout:{browser}:{name}"
    if key not in c:
        try:
            c[key] = bundle.read_json(f"{browser}-{name}.json.gz")
        except (ValueError, OSError):
            c[key] = None
    return c[key]


def grid(bundle, browser: str | None, name: str):
    """The grey-level grid of a screenshot (``<browser>-<name>.png``), decoded once per bundle."""
    if not browser:
        return None
    from webparity import images

    c = cache(bundle)
    key = f"grid:{browser}:{name}"
    if key not in c:
        c[key] = images.grid(bundle.read_file(f"{browser}-{name}.png"))
    return c[key]
