# SPDX-License-Identifier: BSD-2-Clause
"""Resources and network: more or fewer resources than the reference, failed and missing loads, fetch loops.

Signatures (error unless marked info):

- ``resource-buffer-full`` (info): Resource Timing's buffer (250 entries unless the page raises it) filled up, so the
  resource lists are incomplete and fewer/missing resources are not compared.
- ``reference-smaller-page`` (info): the reference got a much smaller page (stopped as headless: 265 elements against
  1,331, the app never loaded) - then the resource comparison says nothing about Ladybird.
- ``more-resources:<initiator>:<file type>``: Ladybird fetched many more of a file type (181 SVG against 18: background
  images for rules that match nothing, #14069). The initiator of Ladybird's extra entries tells CSS backgrounds from
  <img>; the reference is compared per file type, since the browsers set initiatorType differently.
- ``fewer-resources``: fewer resources than the reference, without <link> prefetches (Speculation Rules and quicklink
  prefetch internal pages in Chromium; Ladybird doesn't prefetch). Info when the page doesn't also look different (no
  layout, mobile layout or image finding; 2026-11-24, 42 sites: the reference's extra entries were its preload scanner
  fetching scripts that the page then turned into ``text/lazyload``, fonts no element used, lazy images further down,
  and SVG sprites for ``<use href>``, which Ladybird renders without a Resource Timing entry).
- ``failed-loads``: loads that failed in Ladybird (log) that the reference loaded - not those the reference failed too,
  not hosts that don't resolve (``reload.unresolvable_hosts``, looked up during the visit), and not localhost or private
  addresses.
- ``missing-resources`` / ``missing-resources-few`` (info, one or two - nearly always tracking loaded on consent or
  timing): scripts and stylesheets from the site that only the reference fetched, not via <link> (prefetch/preload),
  only those still in the reference's final DOM, and not Elementor's lightbox/dialog/share-link (a tracking pixel's scan
  of global objects loads them through a getter in Chromium only, 2026-11-16 and 2026-11-21), nor scripts the reference
  first prefetched through <link rel=prefetch> (Next.js loads a route's chunks once its prefetch has loaded).
- ``network-loop:<file type>``: the top document fetched the same address at least 50 times (200 when the reference's
  buffer was full) and the reference at most 5 times (#14392: a stylesheet importing itself). Counted from Resource
  Timing, which is per document (an iframe each fetching a player stylesheet once is no loop).
- ``renavigation`` (info): the page navigated itself three or more times during the first load (BiDi events).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from webparity import ladybird_log
from webparity.detectors import Detector
from webparity.detectors._common import (
    OK,
    domain,
    events,
    file_type,
    finding,
    gate,
    info,
    m,
    norm_url,
    num,
    reference,
    resource_key,
    site_key,
)

BUFFER = 249  # Resource Timing has room for 250 entries unless the page raises it - full = the list is incomplete
FIRST_LOAD_STEPS = ("load", "probe", "idle", "interact")


def reference_network_errors(bundle, ref: str) -> set[str]:
    """Resource keys the reference failed to load, from its console ("Failed to load resource")."""
    out = set()
    for e in events(bundle):
        if e.get("browser") != ref or "Failed to load resource" not in (e.get("text") or ""):
            continue
        for u in re.findall(r"https?://\S+", e.get("text") or "") + [
            s.split(" ", 1)[-1].rsplit(":", 2)[0] for s in e.get("stack") or []
        ]:
            if u.startswith("http"):
                out.add(resource_key(u))
    return out


def private_host(host: str) -> bool:
    """localhost, loopback and private addresses: a site that loads from them (2026-11-22: a development copy loading
    from http://localhost) fails in every browser, and the proxy refuses them anyway."""
    import ipaddress

    if host in ("localhost", "") or host.endswith((".localhost", ".local")):
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def public_page_private_resource(resource_url: str, page_url: str) -> bool:
    """A resource on a private host loaded by a page on a public one. A page served from a private host itself (the
    canary) is judged as usual."""
    return private_host(urlsplit(resource_url).hostname or "") and not private_host(urlsplit(page_url).hostname or "")


def looks_different(bundle) -> bool:
    """Whether the page also looks different in Ladybird: a layout, mobile layout or image finding (not one inside an
    embedded PDF viewer)."""
    from webparity.detectors.image import image_findings
    from webparity.detectors.layout import layout_findings
    from webparity.detectors.mobile import mobile_findings

    return any(
        f.signature.startswith(("layout:", "mobile-layout:", "image-differs")) and not (f.data or {}).get("pdf_viewer")
        for f in layout_findings(bundle) + mobile_findings(bundle) + image_findings(bundle)
    )


class Network(Detector):
    name = "network"
    description = "More, fewer, failed and missing resources; fetch loops; self-navigation"

    def run(self, bundle):
        if gate(bundle) != OK:
            return []
        ref = reference(bundle)
        lp, cp = m(bundle, "ladybird", "probe"), m(bundle, ref, "probe")
        out = []
        full = num(lp, "resources_total") >= BUFFER or num(cp, "resources_total") >= BUFFER
        if full:
            out.append(
                info(
                    "resource-buffer-full",
                    "The Resource Timing buffer (250) filled up - the resource lists are not compared",
                    resources=[lp.get("resources_total"), cp.get("resources_total")],
                )
            )
        # More resources of a file type (also when Ladybird's buffer is full - then its count is a floor). Not when
        # the reference's is full: then the reference's count is the floor, and resources fetched after the 250th entry
        # looked like Ladybird's extras (2026-11-22: 44 scripts against 3 on varldsarvetgastgivars.se, the same 56 in
        # both DOMs)
        ref_full = num(cp, "resources_total") >= BUFFER
        ref_types: dict[str, int] = {}
        for u in cp.get("resource_list") or []:
            ref_types[file_type(u)] = ref_types.get(file_type(u), 0) + 1
        ref_urls = {norm_url(u) for u in cp.get("resource_list") or []}
        lb_initiators = lp.get("resource_types") or []
        lb_types: dict[str, int] = {}
        extra: dict[str, dict[str, list[str]]] = {}
        for k, u in enumerate(lp.get("resource_list") or []):
            t = file_type(u)
            lb_types[t] = lb_types.get(t, 0) + 1
            if norm_url(u) not in ref_urls:
                initiator = (lb_initiators[k] if k < len(lb_initiators) else "") or "other"
                extra.setdefault(t, {}).setdefault(initiator, []).append(norm_url(u))
        smaller = (
            num(lp, "elements") >= 300
            and num(cp, "elements") < 0.4 * num(lp, "elements")
            and num(cp, "text") < 0.4 * max(1, num(lp, "text"))
        )
        if smaller:
            out.append(
                info(
                    "reference-smaller-page",
                    "The reference got a much smaller page than Ladybird",
                    elements=[lp.get("elements"), cp.get("elements")],
                    text=[lp.get("text"), cp.get("text")],
                )
            )
        for t, n in sorted(lb_types.items(), key=lambda kv: -kv[1]):
            if (
                t != "other"
                and n - ref_types.get(t, 0) >= 30
                and n >= 2 * ref_types.get(t, 0)
                and not smaller
                and not ref_full
            ):
                initiator, urls = max((extra.get(t) or {"other": []}).items(), key=lambda kv: len(kv[1]))
                out.append(
                    finding(
                        f"more-resources:{initiator}:{t}",
                        f"Ladybird fetched {n} .{t} against {ref_types.get(t, 0)} in the reference"
                        f" (most extra via {initiator})",
                        count=[n, ref_types.get(t, 0)],
                        per_initiator={k: len(v) for k, v in (extra.get(t) or {}).items()},
                        examples=sorted(set(urls))[:8],
                    )
                )

        def without_link(d):
            return sum(1 for t in (d.get("resource_types") or []) if t != "link")

        lr, cr = without_link(lp), without_link(cp)
        if not full and cr - lr >= 10 and lr < 0.7 * cr:
            visible = looks_different(bundle)
            out.append(
                finding(
                    "fewer-resources",
                    "Fewer resources fetched than in the reference (without link prefetches)",
                    "error" if visible else "info",
                    resources=[lr, cr],
                    total=[lp.get("resources_total"), cp.get("resources_total")],
                    **({} if visible else {"no_visible_difference": True}),
                )
            )
        # Loads that failed in Ladybird but the reference loaded (not those the reference failed too, e.g. blocked
        # hosts)
        log = ladybird_log.for_bundle(bundle)["full"]
        ref_loaded = {resource_key(u) for u in cp.get("resource_list") or []}
        ref_failed = reference_network_errors(bundle, ref)
        unresolvable = set(m(bundle, "ladybird", "reload").get("unresolvable_hosts") or [])
        failed = [
            f
            for f in log["failed_loads"]
            if resource_key(f["url"]) in ref_loaded
            and resource_key(f["url"]) not in ref_failed
            and not ("resolve host" in f.get("error", "") and (urlsplit(f["url"]).hostname or "") in unresolvable)
            and not public_page_private_resource(f["url"], lp.get("url") or cp.get("url") or "")
        ]
        if failed:
            out.append(
                finding(
                    "failed-loads",
                    f"{len(failed)} resources the reference loaded failed in Ladybird",
                    examples=failed[:8],
                )
            )
        # Scripts and stylesheets from the site that only the reference fetched (not via <link>: prefetch/preload, e.g.
        # a
        # framework prefetching other pages - what is really used, Ladybird fetches when needed; 2026-11-08)
        site = site_key(domain(lp.get("url") or cp.get("url") or ""))
        lb_all = {resource_key(u) for u in lp.get("resource_list") or []} | {
            resource_key(f["url"]) for f in log["failed_loads"]
        }
        types = cp.get("resource_types") or []
        missing = sorted(
            {
                norm_url(u)
                for i, u in enumerate(cp.get("resource_list") or [])
                if re.search(r"\.(m?js|css)$", norm_url(u))
                and (types[i] if i < len(types) else "") != "link"
                and site_key(domain("https://" + norm_url(u))) == site
                and resource_key(u) not in lb_all
            }
        )
        if cp.get("dom_resources") is not None:
            in_dom = {norm_url(u) for u in cp.get("dom_resources") or []}
            missing = [u for u in missing if u in in_dom]
        missing = [u for u in missing if not re.search(r"/elementor(-pro)?/.*(share-link|dialog|lightbox)", u)]
        # Scripts the reference first prefetched through <link rel=prefetch>: Next.js loads a route's chunks once its
        # prefetch link has loaded, and Ladybird doesn't prefetch, so it loads them on navigation instead (2026-11-24:
        # feminvest.se, 15 chunks in Chromium and Firefox, none in Ladybird)
        prefetched = {
            norm_url(u)
            for i, u in enumerate(cp.get("resource_list") or [])
            if (types[i] if i < len(types) else "") == "link"
        }
        missing = [u for u in missing if u not in prefetched]
        if len(missing) >= 3 and not full:
            out.append(
                finding(
                    "missing-resources",
                    f"{len(missing)} scripts/stylesheets from the site were fetched only by the reference",
                    examples=missing[:8],
                )
            )
        elif missing and not full:
            out.append(
                info(
                    "missing-resources-few",
                    f"{len(missing)} script(s)/stylesheet(s) from the site were fetched only by the reference",
                    examples=missing[:8],
                )
            )
        # The same address again and again (#14392). The reference's list is cut at 300 characters - compare on that.
        ref_counts: dict[str, int] = {}
        for u in cp.get("resource_list") or []:
            ref_counts[u[:300]] = ref_counts.get(u[:300], 0) + 1
        # The reference's buffer stops at 250 entries; when full, the address may have been fetched after that, so
        # require
        # more fetches (2026-11-18: 80-120 against "0" with a full buffer)
        ref_full = num(cp, "resources_total") >= 250
        for item in lp.get("repeated") or []:
            if not isinstance(item, list) or len(item) != 2:
                continue
            u, n = item
            if n >= (200 if ref_full else 50) and ref_counts.get(u[:300], 0) <= 5:
                path = urlsplit(u).path if "://" in u else u
                kind = (re.search(r"\.([a-z0-9]{1,5})(?:[?#]|$)", path + "?") or [None, "other"])[1]
                out.append(
                    finding(
                        f"network-loop:{kind}",
                        f"Ladybird fetched the same address {n} times in the first load"
                        f" (reference {ref_counts.get(u[:300], 0)})",
                        url=u[:300],
                        count=[n, ref_counts.get(u[:300], 0)],
                    )
                )
                break
        navigations = [
            e
            for e in events(bundle)
            if e.get("browser") == "ladybird"
            and e.get("method") == "browsingContext.navigationStarted"
            and e.get("top")
            and e.get("step") in FIRST_LOAD_STEPS
        ]
        if len(navigations) - 1 >= 3:
            out.append(
                info(
                    "renavigation",
                    f"The page navigated itself {len(navigations) - 1} times in the first load",
                    count=len(navigations) - 1,
                )
            )
        return out
