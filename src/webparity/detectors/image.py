# SPDX-License-Identifier: BSD-2-Clause
"""Screenshots, iframes, canvases, broken images and fonts.

Signatures (error unless marked info):

- ``image-differs``: at least 15 % of the still parts of the first screenshot differ from the reference (32 px blocks;
  blocks that move between the load and idle screenshots, or between the load and the reload, in either browser are
  masked; not when more than 70 % is masked). Info when there is a layout finding - the image difference is then
  usually a consequence of it (2026-11-09).
- ``cookie-banner-differs`` (info): a cookie banner is shown in only one browser (instead of ``image-differs``).
- ``blank-page``: Ladybird's screenshot is a single colour, the reference's is not.
- ``empty-iframe`` / ``empty-canvas``: an iframe or canvas that is blank in Ladybird but not in the reference, or a
  same-origin iframe with less than half the reference's elements.
- ``broken-images``: at least three more broken images than the reference.
- ``font-error[:<source formats>]``: fonts that failed to load in Ladybird, named after the @font-face source formats.
  Not when the reference didn't load them either (``font-not-loaded-in-reference``, info - a 404, unused, or a local
  alias Chromium finds), when another @font-face of the same family loaded (``font-replaced``, info), or when the
  sources are only local() (``font-local``, info: the font isn't in the test environment; next/font's "X Fallback").
"""

from __future__ import annotations

import re

from webparity import images
from webparity.detectors import Detector
from webparity.detectors._common import OK, cache, dict_list, file_type, finding, gate, grid, info, m, num, reference
from webparity.detectors.layout import layout_findings
from webparity.detectors.mobile import mobile_findings


def font_source(src: str | None) -> str | None:
    """'local' if @font-face has only local() sources, else the format of the first url() (woff2, woff, ttf ...)."""
    if not src:
        return None
    if "url(" not in src:
        return "local" if "local(" in src else None
    match = re.search(r"url\(\s*[\"']?([^\"')]+)", src)
    return file_type(match.group(1)) if match else "url"


def comparison(bundle) -> dict:
    c = cache(bundle)
    if "image" not in c:
        ref = reference(bundle)
        g = {(b, n): grid(bundle, b, n) for b in ("ladybird", ref) for n in ("load", "idle", "reload")}
        result = (
            images.compare(
                g["ladybird", "load"],
                g["ladybird", "idle"],
                g[ref, "load"],
                g[ref, "idle"],
                g["ladybird", "reload"],
                g[ref, "reload"],
            )
            or {}
        )
        lp, cp = m(bundle, "ladybird", "probe"), m(bundle, ref, "probe")
        result["iframes"] = images.regions(g["ladybird", "load"], g[ref, "load"], lp.get("iframes"), cp.get("iframes"))
        result["canvases"] = images.regions(
            g["ladybird", "load"], g[ref, "load"], lp.get("canvases"), cp.get("canvases")
        )
        result["spread"] = [images.spread(g["ladybird", "load"]), images.spread(g[ref, "load"])]
        c["image"] = result
    return c["image"]


def image_findings(bundle) -> list:
    c = cache(bundle)
    if "image_findings" in c:
        return c["image_findings"]
    out: list = []
    c["image_findings"] = out
    if gate(bundle) != OK:
        return out
    ref = reference(bundle)
    lp, cp = m(bundle, "ladybird", "probe"), m(bundle, ref, "probe")
    if num(lp, "broken_images") - num(cp, "broken_images") >= 3:
        out.append(
            finding(
                "broken-images",
                "More broken images than in the reference",
                count=[lp.get("broken_images"), cp.get("broken_images")],
            )
        )
    out.extend(font_findings(lp, cp))
    result = comparison(bundle)
    for kind, name, label in (("iframes", "empty-iframe", "Iframe"), ("canvases", "empty-canvas", "Canvas")):
        for r in result.get(kind) or []:
            blank = (
                r["ladybird"] is not None and r["reference"] is not None and r["ladybird"] < 3 and r["reference"] > 10
            )
            ref_elements = r.get("reference_elements") or 0
            empty_dom = ref_elements >= 20 and (r.get("ladybird_elements") or 0) < 0.5 * ref_elements
            if blank or empty_dom:
                out.append(finding(name, f"{label} is empty in Ladybird but not in the reference", **r))
                break
    lb_spread, ref_spread = result.get("spread") or [None, None]
    blank_page = lb_spread is not None and ref_spread is not None and lb_spread < 3 and ref_spread > 15
    if blank_page:
        out.append(
            finding("blank-page", "The screenshot is a single colour in Ladybird", spread=[lb_spread, ref_spread])
        )
    has_layout = any(
        f.signature.startswith(("layout:", "mobile-layout:")) for f in layout_findings(bundle) + mobile_findings(bundle)
    )
    cookie_lb = any(x.get("cookie") for x in dict_list(lp, "large_fixed"))
    cookie_ref = any(x.get("cookie") for x in dict_list(cp, "large_fixed"))
    if cookie_lb != cookie_ref:
        out.append(
            info(
                "cookie-banner-differs",
                f"The cookie banner is shown only in {'Ladybird' if cookie_lb else 'the reference'}",
            )
        )
    elif (result.get("share") or 0) >= 0.15 and (result.get("masked") or 0) <= 0.7 and not blank_page:
        out.append(
            finding(
                "image-differs",
                f"{round(result['share'] * 100)} % of the still parts of the screenshot differ from the reference",
                "info" if has_layout else "error",
                share=result["share"],
                masked=result.get("masked"),
                area=result.get("area"),
            )
        )
    return out


def font_findings(lp: dict, cp: dict) -> list:
    if num(lp, "font_errors") <= num(cp, "font_errors"):
        return []
    names = lp.get("font_error_names") or []
    sources = lp.get("font_sources") or {}
    # next/font's fallback fonts are called "X Fallback" or "X Fallback: Arial" and have only local() sources - counted
    # as local even when the source couldn't be read (a stylesheet from another origin, 2026-11-08)
    kinds = {
        f: font_source(sources.get(f)) or ("local" if re.search(r" Fallback(:|$)", f) else None)
        for f in lp.get("font_error_families") or []
    }
    types = sorted({k for k in kinds.values() if k}) if kinds and all(kinds.values()) else []
    ref_loaded = set(cp.get("fonts_loaded") or [])
    if (
        lp.get("font_errors_all") is not None
        and cp.get("fonts_loaded") is not None
        and not any(n in ref_loaded for n in lp["font_errors_all"])
    ):
        return [
            info(
                "font-not-loaded-in-reference",
                "Fonts that didn't load in Ladybird and that the reference didn't load either"
                " (404, unused or a local alias)",
                fonts=names,
            )
        ]
    if lp.get("font_errors_unreplaced") is not None and num(lp, "font_errors_unreplaced") <= num(
        cp, "font_errors_unreplaced"
    ):
        return [
            info("font-replaced", "Fonts that didn't load, but another @font-face for the same family did", fonts=names)
        ]
    if types == ["local"]:
        return [info("font-local", "Fonts with only local() sources that aren't in the test environment", fonts=names)]
    formats = [t for t in types if t != "local"]
    return [
        finding(
            "font-error:" + "+".join(formats) if formats else "font-error",
            "Fonts that didn't load in Ladybird",
            count=[lp.get("font_errors"), cp.get("font_errors")],
            fonts=names,
            sources={k: sources.get(k) for k in kinds},
        )
    ]


class Image(Detector):
    name = "image"
    description = "Screenshot comparison, empty iframes/canvases, blank page, broken images, fonts"

    def run(self, bundle):
        return [f.__class__(**f.to_dict()) for f in image_findings(bundle)]
