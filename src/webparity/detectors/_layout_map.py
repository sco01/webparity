# SPDX-License-Identifier: BSD-2-Clause
"""The layout comparison: pairs the elements of two layout maps (``webparity.js.LAYOUT``) and returns the causes.

Ported from the old explorer's ``layoutjamforelse`` (2026-11-09). The causes are elements whose size differs without a
child explaining it (width/height), the outermost element that exists in only one of the maps (missing: 0 x 0 in
Ladybird; extra: 0 x 0 in the reference), and positioned elements (absolute/fixed) that ended up elsewhere relative to
their parent with the same size (position). Elements in fixed or sticky layers (cookie banners, headers) do not count.
Each cause has the signature ``<kind>:<parent display>><display>`` (the tag for replaced elements).

With the reference's map after its reload, elements that differ between the reference's own two loads do not count.
Elements hidden under content-visibility: hidden in the reference (a closed <details>, hidden=until-found) do not count
either: Ladybird creates no boxes for them and measures 0 x 0, Chromium and Firefox measure them (known, 2026-11-09 -
half of layout:missing:block>block). A file upload control's width is each browser's own choice and does not count
either (2026-11-22), nor Google's ad slots, which are filled in one browser and empty in the other (2026-11-22).
"""

from __future__ import annotations

import re

REPLACED = {
    "img",
    "svg",
    "video",
    "iframe",
    "input",
    "button",
    "select",
    "textarea",
    "canvas",
    "object",
    "embed",
    "audio",
    "progress",
    "meter",
}
# Input types whose width is the browser's own choice (no specified intrinsic size)
NATIVE_WIDTH = {"file"}
# Carousel viewports (2026-11-22): the layout map stops at them (webparity.js.LAYOUT), but their own height is set by
# the carousel's script and depends on when it last measured - Flickity's thumbnails on dromvavaren.se were 63 px at a
# mobile load and 91 after a resize in every browser. Not compared, but an explanation for the ancestors (like ads).
# UIkit's slideshow and slider (2026-11-23: asb-executive.se, an autoplaying slideshow on another slide in each one).
# w3schools' slideshow (2026-11-23: byggbeskrivningar.se and fixatrixa.se, another .mySlides shown in each browser).
# Strong Testimonials' slider (2026-11-25: kulturgryning.nu, the viewport as high as the quote shown - 725, 1062 and
# 1845 px in Firefox, Chromium and Ladybird).
CAROUSEL_VIEWPORT = re.compile(
    r"/(?:div|ul|section)\.(?:flickity-viewport|slick-list|swiper-wrapper|splide__track|owl-stage-outer|glide__track"
    r"|carousel-inner|slideshow|n2-ss-slider-1|et_pb_slides|uk-slideshow-items|uk-slider-items|slideshow-container"
    r"|wpmslider-viewport)"
    r"(?::\d+)?$"
    r"|/rs-slides(?::\d+)?$"
)
# Media players' "loaded" bars (2026-11-23: MediaElement.js on mittzoo.se, 425 px in Ladybird against 26 in
# Chromium): their width is how much of the video has been downloaded, which depends on the network and how each
# browser buffers - not compared, but an explanation for the ancestors (like ads)
BUFFER_BAR = re.compile(
    r"/span\.mejs-time-(?:loaded|buffering)(?::\d+)?$|/div\.vjs-load-progress(?::\d+)?$"
    r"|/progress\.plyr__progress__buffer(?::\d+)?$"
)
# Hover zoom images (2026-11-23: jQuery Zoom on WooCommerce product pages - ledins, lillamargreth, nywebbutik): hidden
# until the pointer is over the product image, then moved with the pointer, so where they are depends on where the
# pointer happens to be - not compared
HOVER_ZOOM = re.compile(r"/img\.zoomImg(?::\d+)?$")
# Google's ad slots (2026-11-22): filled in one browser and empty in the other (280 px against 0 on dagensprognos and
# autonytt) - not compared, but an explanation for the ancestors
AD_SLOT = re.compile(r"/(?:div|ins|aside)\.(?:google-auto-placed|adsbygoogle)(?::\d+)?(?=/|$)")
# Only the first three screens in the reference - further down lazy loading and time-dependent content differ
DEPTH = 2700


# The most elements an item (a card, a list entry) has inside, for different text explaining the whole item
ITEM_SIZE = 25


def index(rows: list) -> dict:
    out: dict = {}
    for i, r in enumerate(rows):
        if not isinstance(r, list) or len(r) < 12:
            continue
        key, pi, x, y, w, h, disp, pos, flt, text, fs, fp = r[:12]
        parent = rows[pi][0] if isinstance(pi, int) and 0 <= pi < len(rows) else None
        fixed = pos in ("fixed", "sticky") or (parent is not None and out.get(parent, {}).get("fixed"))
        out[key] = {
            "i": i,
            "parent": parent,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "display": disp,
            "position": pos,
            "float": flt,
            "text": text,
            "fs": fs,
            "fp": fp,
            "hidden": len(r) > 12 and bool(r[12]),
            "cva": len(r) > 13 and bool(r[13]),
            "type": r[14] if len(r) > 14 else "",
            "clip": len(r) > 15 and bool(r[15]),
            "fixed": bool(fixed),
            "tag": key.rsplit("/", 1)[-1].split(":")[0].split("#")[0].split(".")[0],
        }
    for key in reversed(list(out)):  # text in the whole subtree (children come after their parent in document order)
        e = out[key]
        e["tt"] = e.get("tt", 0) + e["text"]
        if e["parent"] in out:
            out[e["parent"]]["tt"] = out[e["parent"]].get("tt", 0) + e["tt"]
    return out


def compare(lb_rows: list | None, ref_rows: list | None, ref_reload_rows: list | None = None) -> dict | None:
    if not lb_rows or not ref_rows:
        return None
    L, C = index(lb_rows), index(ref_rows)
    paired = {k for k in C if k in L and L[k]["fp"] == C[k]["fp"] and L[k]["tag"] == C[k]["tag"]}

    # Different text (2026-11-23: an Elementor loop that lists posts in random order showed other post titles in each
    # browser): the content differs, not the layout. The parent counts as explained, as by a child that differs - also
    # when the text is in an inline child that is not compared itself.
    def own_text(e):
        return e["text"], str(e["fp"]).partition(":")[2]

    other_text = {
        k
        for k in C
        if k in L and L[k]["tag"] == C[k]["tag"] and (L[k]["text"] or C[k]["text"]) and own_text(L[k]) != own_text(C[k])
    }
    paired -= other_text
    # ... and the rest of a small item around such text (2026-11-23: gpslogik.se's related products in random order -
    # the card with another title had another image). Only small items: a date or a counter that differs directly in
    # a page section must not hide the whole section.
    for item in {C[k]["parent"] for k in other_text if C[k]["parent"] in C}:
        inside = {k for k in C if k.startswith(item + "/")}
        if len(inside) <= ITEM_SIZE:
            paired -= inside
    hidden = {k for k in paired if C[k]["hidden"]}
    paired -= hidden

    # Clipped away (2026-11-25: vindobyggvaror.se's closed drop-down menu, a two-column list with max-height: 0 and
    # overflow: hidden - Chromium lays the third-level list out across overflow columns, Ladybird stacks it, and none of
    # it is visible): an element inside an ancestor that clips its overflow and has no size in the reference is not
    # compared.
    def clipped_away(k):
        a = C[k]["parent"]
        while a in C:
            if C[a]["clip"] and (C[a]["w"] < 1 or C[a]["h"] < 1):
                return True
            a = C[a]["parent"]
        return False

    clipped = {k for k in paired if clipped_away(k)}
    paired -= clipped

    def zero(e):
        return e["w"] < 1 or e["h"] < 1

    def visible(e):
        return e["w"] >= 20 and e["h"] >= 10

    unstable = 0
    C2 = index(ref_reload_rows) if ref_reload_rows else {}
    if ref_reload_rows:
        # Only the size: a banner that appears pushes the whole page down without any element changing size
        def moves(a, b):
            return abs(a["w"] - b["w"]) >= 8 or abs(a["h"] - b["h"]) >= 8

        shaky = {k for k in paired if k not in C2 or C2[k]["fp"] != C[k]["fp"] or moves(C[k], C2[k])}
        unstable = len(shaky)
        paired -= shaky
    kinds: dict[str, str] = {}
    for k in paired:
        lb, c = L[k], C[k]
        if k == "body" or c["fixed"] or lb["fixed"] or c["y"] > DEPTH or c["y"] + c["h"] < 0:
            continue
        if c["x"] + c["w"] < 0 and lb["x"] + lb["w"] < 0:
            # Parked off the page to the left in both (2026-11-23: Astra's header cart drop-down at x -14,428 on
            # betong.se and betongforeningen.se, 12 px lower in one browser) - not visible, not compared
            continue
        if CAROUSEL_VIEWPORT.search(k):
            if abs(lb["h"] - c["h"]) >= 24 or abs(lb["w"] - c["w"]) >= 24:
                kinds[k] = "carousel"
            continue
        if BUFFER_BAR.search(k):
            if abs(lb["h"] - c["h"]) >= 24 or abs(lb["w"] - c["w"]) >= 24:
                kinds[k] = "buffer"
            continue
        if HOVER_ZOOM.search(k):
            continue
        ad = AD_SLOT.search(k)
        if ad:
            if ad.end() == len(k) and (abs(lb["h"] - c["h"]) >= 24 or abs(lb["w"] - c["w"]) >= 24):
                kinds[k] = "ad"
            continue
        if c["tag"] == "img" and "unloaded" in (lb["type"], c["type"]):
            # A lazy image one browser hasn't loaded yet (2026-11-22): how far below the viewport loading="lazy"
            # starts is each browser's choice (Ladybird 1,250 px, headless Chromium 2,500), so the image keeps its
            # placeholder size in one of them - not a cause, but an explanation for the ancestors
            if abs(lb["h"] - c["h"]) >= 24 or abs(lb["w"] - c["w"]) >= 24:
                kinds[k] = "unloaded"
            continue
        sources = {lb["type"], c["type"]}
        if c["tag"] == "img" and len(sources) == 2 and all(t.startswith("src:") for t in sources):
            # Another candidate of the srcset (2026-11-23): which one a browser takes is its choice (Chromium reuses a
            # larger one it already has), and the candidates can have different shapes - not a cause, but an
            # explanation for the ancestors
            if abs(lb["h"] - c["h"]) >= 24 or abs(lb["w"] - c["w"]) >= 24:
                kinds[k] = "other-source"
            continue
        if (
            c["display"] == "inline" == lb["display"]
            and c["tag"] not in REPLACED
            and not c["text"]
            and not lb["text"]
            and zero(lb) != zero(c)
        ):
            # An inline element without text of its own whose children are floats or blocks (2026-11-25: dis-mitt.se's
            # header-content span around two floats - Chromium gives it the floats' area, Ladybird and Firefox 0 x 0):
            # the box is not comparable, and the children are compared themselves
            continue
        if c["cva"] and (abs(lb["h"] - c["h"]) >= 24 or abs(lb["w"] - c["w"]) >= 24):
            # content-visibility: auto (2026-11-10): Chromium gives the element a placeholder size off screen - not a
            # cause, but counted as an explanation so the ancestors are not pointed out
            kinds[k] = "cv-auto"
        elif c["tag"] == "input" and c["type"] in NATIVE_WIDTH and abs(lb["h"] - c["h"]) < 8:
            # A file upload control has no specified size: Chromium reserves 253 px (button and "No file chosen" in a
            # fixed box), Ladybird sizes it to the button and its text (186 px) - not a cause, but an explanation for
            # the ancestors (2026-11-22: 15+ sites as layout:width:block>input)
            if abs(lb["w"] - c["w"]) >= 24:
                kinds[k] = "native-width"
        elif zero(lb) and visible(c):
            kinds[k] = "missing"
        elif zero(c) and visible(lb):
            kinds[k] = "extra"
        elif c["display"] == "inline" and c["tag"] not in REPLACED:
            # Inline elements are not compared by size (2026-11-09): the box is the union of the fragments, and Ladybird
            # includes images and blocks inside (logo links: 221x68 against 221x18 in Chromium) - its own investigation
            continue
        elif not zero(lb) and not zero(c):
            dw, dh = abs(lb["w"] - c["w"]), abs(lb["h"] - c["h"])
            # Text wraps differently with different font metrics - a height difference of a few lines does not count
            # when the element has text
            height_limit = max(24, 3.2 * c["fs"]) if c["tt"] else 24
            if dw >= 24 and dw >= 0.25 * max(lb["w"], c["w"]):
                kinds[k] = "width"
            elif dh >= height_limit and dh >= 0.25 * max(lb["h"], c["h"]):
                kinds[k] = "height"
            elif c["tt"] and dh >= 24 and dh >= 0.25 * max(lb["h"], c["h"]):
                # Text that wraps onto another line (2026-11-23: a 48 px heading one line taller made its flex
                # container 64 px taller, more than the container's own limit) - not a cause, but an explanation
                # for the ancestors
                kinds[k] = "text-wrap"
            elif c["position"] in ("absolute", "fixed") and dw < 8 and dh < 8:
                # The position is measured against the nearest ancestor that is not inline (2026-11-10): an inline
                # parent's box is the union of its fragments and differs between the engines (above) - 45 sites got
                # layout:position:inline>... although the element was in the same place on the page in both
                anc = c["parent"]
                while anc in C and C[anc]["display"] == "inline" and C[anc]["tag"] not in REPLACED:
                    anc = C[anc]["parent"]
                if anc not in paired:
                    continue
                pl, pc = L[anc], C[anc]
                # A position the reference itself doesn't keep between its two loads (2026-11-25: a 40 s CSS animation
                # moving a gradient blob on vertica.dk, a mesh of icons placed at random on surecookie.com, a masonry
                # gallery in random order on kkm.se) - not compared
                if k in C2 and anc in C2:
                    r2, a2 = C2[k], C2[anc]
                    if (
                        abs((r2["x"] - a2["x"]) - (c["x"] - pc["x"])) >= 40
                        or abs((r2["y"] - a2["y"]) - (c["y"] - pc["y"])) >= 40
                    ):
                        continue
                if (
                    abs((lb["x"] - pl["x"]) - (c["x"] - pc["x"])) >= 40
                    or abs((lb["y"] - pl["y"]) - (c["y"] - pc["y"])) >= 40
                ):
                    kinds[k] = "position"
    unloaded_images = {k for k in L if k in C and C[k]["tag"] == "img" and "unloaded" in (L[k]["type"], C[k]["type"])}
    with_child = {C[k]["parent"] for k, kind in kinds.items() if kind != "text-wrap"} | {
        C[k]["parent"] for k in other_text
    }
    # Text wrapping onto another line explains a height difference, not a width difference (a menu item can be
    # wider because of a real bug and wrap because of it)
    taller_text = {C[k]["parent"] for k, kind in kinds.items() if kind == "text-wrap"}
    # content-visibility: auto (2026-11-10): an element the reference gives a placeholder size explains its parent even
    # below DEPTH (sections at y 4,400 made a results list 2,600 px taller in Ladybird)
    with_child |= {
        C[k]["parent"]
        for k in paired
        if C[k]["cva"] and (abs(L[k]["h"] - C[k]["h"]) >= 24 or abs(L[k]["w"] - C[k]["w"]) >= 24)
    }
    causes = []
    for k, kind in kinds.items():
        c, lb = C[k], L[k]
        if kind in ("cv-auto", "native-width", "ad", "unloaded", "other-source", "carousel", "text-wrap", "buffer"):
            continue
        if kind in ("missing", "extra") and any(u.startswith(k + "/") for u in unloaded_images):
            continue  # an image inside it hasn't loaded in one browser (2026-11-22: friluftsbyn.se, a lazy map image)
        if kind in ("missing", "extra") and kinds.get(c["parent"]) == kind:
            continue  # the outermost element that is missing is enough
        if kind in ("width", "height") and k in with_child:
            continue  # a child explains the difference
        if kind == "height" and k in taller_text:
            continue
        parent_display = C[c["parent"]]["display"] if c["parent"] in C else "?"
        d = (
            c["tag"]
            if c["tag"] in REPLACED
            else c["display"]
            if c["display"] == lb["display"]
            else f"{c['display']}≠{lb['display']}"
        )
        causes.append(
            {
                "sig": f"{kind}:{parent_display}>{d}",
                "key": k[-160:],
                "ladybird": [lb["x"], lb["y"], lb["w"], lb["h"]],
                "reference": [c["x"], c["y"], c["w"], c["h"]],
                "style": {"display": [lb["display"], c["display"]], "position": c["position"], "float": c["float"]},
                "area": max(lb["w"] * lb["h"], c["w"] * c["h"]),
            }
        )
    groups: dict = {}
    for cause in causes:
        g = groups.setdefault(cause["sig"], {"sig": cause["sig"], "count": 0, "area": 0, "examples": []})
        g["count"] += 1
        g["area"] += cause["area"]
        if len(g["examples"]) < 3:
            g["examples"].append({k: v for k, v in cause.items() if k not in ("sig", "area")})
    return {
        "paired": len(paired),
        "ladybird": len(L),
        "reference": len(C),
        "causes": len(causes),
        "unstable_in_reference": unstable,
        "hidden_in_reference": len(hidden),
        "clipped_in_reference": len(clipped),
        "groups": sorted(groups.values(), key=lambda g: -g["area"])[:12],
    }
