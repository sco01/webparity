# SPDX-License-Identifier: BSD-2-Clause
"""Page-level differences: bot walls, pages that don't load, less content, overflow, error texts.

Signatures (error unless marked info):

- ``blocked:ladybird-only`` (info): Ladybird is stuck on a bot wall the reference gets past. Info since 2026-11-08: the
  building blocks of a Vercel checkpoint's worker work in Ladybird; walls likely react to automation or the user agent
  and are not investigated further.
- ``different-final-site`` (info): the browsers ended on different sites; nothing else is compared.
- ``not-loading:<reason>``: the page doesn't load in Ladybird but does in the reference. Reasons from the log:
  ``http2``, ``ssl``, ``connection``, ``dns``, ``timeout``, ``other``; ``crash``, ``xml-feed`` and ``server-error`` are
  info (the crash has its own finding; Ladybird shows RSS/Atom as an empty page, rendering feeds is "not planned"; a
  server's own 5xx page is the server's - the browsers load in parallel, and a reference that gets one only makes the
  visit incomparable, so these were only ever counted against Ladybird: 2026-11-24, seven 500s, all at one host).
- ``cookie-banner-locks-page`` (info): a cookie banner locks scrolling in Ladybird and isn't shown in the reference
  (some consent tools show no dialog to headless Chromium); the page height is then not compared.
- ``less-content``, ``shorter-page``, ``error-reports`` (Sentry and the like), ``error-text:<text>``, ``overflow``
  (``overflow-hidden``, info, when the viewport can't be scrolled sideways), ``empty-root`` (#root, #__next ... empty),
  ``zero-size-text``, ``off-page-text``, ``object-fallback`` (info: <object> without data with fallback content, PR
  #15190).
"""

from __future__ import annotations

import re

from webparity import ladybird_log
from webparity.detectors import Detector
from webparity.detectors._common import (
    DIFFERENT_SITE,
    LADYBIRD_FAILED,
    OK,
    dict_list,
    domain,
    finding,
    gate,
    info,
    m,
    num,
    reference,
    site_key,
)


def cookie_lock(lp: dict, cp: dict) -> bool:
    """A cookie banner that locks scrolling only in Ladybird (2026-11-08): then Ladybird's page is one screen high."""
    return bool(
        lp.get("scroll_lock")
        and not cp.get("scroll_lock")
        and any(x.get("cookie") for x in dict_list(lp, "large_fixed"))
        and not any(x.get("cookie") for x in dict_list(cp, "large_fixed"))
    )


SERVER_ERROR = re.compile(
    r"^\s*(5\d\d\b|Internal Server Error|Bad Gateway|Service (Temporarily )?Unavailable|Gateway Time-?out)", re.I
)


def load_reason(bundle, lp: dict) -> str:
    log = ladybird_log.for_bundle(bundle)
    full = log["full"]
    # The reason from the log (2026-11-08): curl error 92 (HTTP/2 framing, #13900), SSL verification
    text = " ".join(full["curl_errors"]) + " " + " ".join(f.get("error", "") for f in full["failed_loads"])
    reason = (
        "http2"
        if "HTTP/2" in text
        else "ssl"
        if "SSL" in text
        else "connection"
        if "Unable to connect" in text
        else "dns"
        if "resolve" in text.lower()
        else "timeout"
        if "timed out" in text.lower() or "timeout" in text.lower()
        else "other"
    )
    content_type = (lp.get("content_type") or "").split(";")[0].strip().lower()
    xml = bool(re.search(r"[+/]xml$", content_type)) and not re.search(r"xhtml|svg", content_type)
    if log["crash"]["crash"]:
        reason = "crash"
    elif xml:
        reason = "xml-feed"
    elif reason == "other" and SERVER_ERROR.search(lp.get("title") or ""):
        reason = "server-error"
    return reason


class Page(Detector):
    name = "page"
    description = "Bot walls, pages that don't load, less content, overflow, error texts, empty roots"

    def run(self, bundle):
        out = []
        ref = reference(bundle)
        lp, cp = m(bundle, "ladybird", "probe"), m(bundle, ref, "probe")
        if lp.get("blocked") and not cp.get("blocked"):
            out.append(
                info(
                    "blocked:ladybird-only",
                    f'Ladybird is stuck on a bot wall the reference gets past: "{(lp.get("title") or "")[:60]}"',
                    title=lp.get("title"),
                    url=lp.get("url"),
                )
            )
        state = gate(bundle)
        if state == DIFFERENT_SITE:
            a, b = site_key(domain(lp.get("url") or "")), site_key(domain(cp.get("url") or ""))
            out.append(
                info(
                    "different-final-site",
                    f"The browsers ended on different sites: {a} / {b}",
                    urls=[lp.get("url"), cp.get("url")],
                )
            )
            return out
        if state == LADYBIRD_FAILED:
            if not lp.get("blocked"):
                full = ladybird_log.for_bundle(bundle)["full"]
                reason = load_reason(bundle, lp)
                out.append(
                    finding(
                        f"not-loading:{reason}",
                        f"The page doesn't load in Ladybird but does in the reference ({reason})",
                        "info" if reason in ("crash", "xml-feed", "server-error") else "error",
                        title=lp.get("title"),
                        load_error=m(bundle, "ladybird", "load").get("error"),
                        curl_errors=full["curl_errors"],
                        failed_loads=full["failed_loads"][:3],
                        content_type=lp.get("content_type"),
                    )
                )
            return out
        if state != OK:
            return out
        locked = cookie_lock(lp, cp)
        if locked:
            out.append(
                info(
                    "cookie-banner-locks-page",
                    "The cookie banner locks scrolling in Ladybird but isn't shown in the reference (headless)",
                    height=[lp.get("height"), cp.get("height")],
                )
            )
        if (num(cp, "text") >= 500 and num(lp, "text") < 0.5 * cp["text"]) or (
            num(cp, "elements") >= 200 and num(lp, "elements") < 0.5 * cp["elements"]
        ):
            out.append(
                finding(
                    "less-content",
                    "Less content than in the reference",
                    text=[lp.get("text"), cp.get("text")],
                    elements=[lp.get("elements"), cp.get("elements")],
                )
            )
        if num(cp, "height") >= 1500 and num(lp, "height") < 0.5 * cp["height"] and not locked:
            out.append(
                finding(
                    "shorter-page",
                    "The page is less than half the reference's height",
                    height=[lp.get("height"), cp.get("height")],
                )
            )
        if num(lp, "error_reports") - num(cp, "error_reports") >= 2:
            out.append(
                finding(
                    "error-reports",
                    "The site sent more error reports (Sentry and the like) than in the reference",
                    count=[lp.get("error_reports"), cp.get("error_reports")],
                )
            )
        for t in sorted(set(lp.get("error_texts") or []) - set(cp.get("error_texts") or [])):
            out.append(finding(f"error-text:{t}", f'An error text only in Ladybird: "{t}"'))
        if num(lp, "overflow") > 20 and num(cp, "overflow") <= 20:
            # 2026-11-10: when the viewport can't be scrolled sideways (overflow-x hidden/clip, e.g. body { overflow-x:
            # clip } that Ladybird propagates despite container-type) the overflow is invisible - only scrollWidth
            # differs
            hidden = lp.get("viewport_overflow_x") in ("hidden", "clip")
            out.append(
                finding(
                    "overflow-hidden" if hidden else "overflow",
                    "Horizontal overflow only in Ladybird"
                    + (" (the viewport can't be scrolled sideways)" if hidden else ""),
                    "info" if hidden else "error",
                    px=[lp.get("overflow"), cp.get("overflow")],
                    viewport_overflow_x=lp.get("viewport_overflow_x"),
                )
            )
        if lp.get("empty_root") and not cp.get("empty_root"):
            out.append(finding("empty-root", "The framework root (#root, #__next ...) is empty in Ladybird"))
        if num(lp, "zero_size") - num(cp, "zero_size") >= 5:
            out.append(
                finding(
                    "zero-size-text",
                    "Text elements with width or height 0 only in Ladybird",
                    count=[lp.get("zero_size"), cp.get("zero_size")],
                    examples=lp.get("zero_size_examples"),
                )
            )
        if num(lp, "off_page") - num(cp, "off_page") >= 5:
            out.append(
                finding(
                    "off-page-text",
                    "Text elements outside the page only in Ladybird",
                    count=[lp.get("off_page"), cp.get("off_page")],
                    examples=lp.get("off_page_examples"),
                )
            )
        if num(cp, "objects_without_data") >= 1:
            out.append(
                info(
                    "object-fallback",
                    f"{cp['objects_without_data']} <object> without data with fallback content"
                    " (Ladybird doesn't show it, PR #15190)",
                    count=[lp.get("objects_without_data"), cp.get("objects_without_data")],
                )
            )
        return out
