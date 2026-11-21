# SPDX-License-Identifier: BSD-2-Clause
"""Differences in mobile width (390 x 844, without reloading): layout and horizontal overflow.

Signatures:

- ``mobile-layout:<kind>:<parent display>><display>`` (error): the largest layout group in mobile width that isn't
  already a group in desktop width (when there are one to eight groups), compared only when both browsers had the same
  viewport width.
- ``mobile-overflow`` (error): horizontal overflow in mobile width only in Ladybird.
- ``mobile-overflow-hidden`` (info): the same, but the viewport can't be scrolled sideways (overflow-x hidden/clip).
- ``mobile-overflow-above`` (info): the overflow comes only from boxes entirely above the page (2026-11-10: a mobile
  menu at top: -750px). Chromium does not count them in scrollWidth, Ladybird and Firefox do - not Ladybird's bug.
"""

from __future__ import annotations

from webparity.detectors import Detector
from webparity.detectors._common import OK, cache, finding, gate, info, layout_map, m, reference
from webparity.detectors._layout_map import compare
from webparity.detectors.layout import MIN_AREA, comparison

MOBILE_WIDTH = 390


def mobile_findings(bundle) -> list:
    c = cache(bundle)
    if "mobile_findings" in c:
        return c["mobile_findings"]
    out = []
    c["mobile_findings"] = out
    if gate(bundle) != OK:
        return out
    ref = reference(bundle)
    lm, cm = m(bundle, "ladybird", "mobile"), m(bundle, ref, "mobile")
    same_width = (lm.get("viewport") or [0])[0] == (cm.get("viewport") or [1])[0]
    if same_width:
        result = (
            compare(layout_map(bundle, "ladybird", "layout-mobile"), layout_map(bundle, ref, "layout-mobile")) or {}
        )
        desktop = {g["sig"] for g in (comparison(bundle) or {}).get("groups") or []}
        groups = [g for g in result.get("groups") or [] if g["area"] >= MIN_AREA and g["sig"] not in desktop]
        if 0 < len(groups) <= 8:
            g = groups[0]
            out.append(
                finding(
                    f"mobile-layout:{g['sig']}",
                    f"Layout difference in mobile width ({g['sig']}, {g['count']} elements)",
                    count=g["count"],
                    area=g["area"],
                    examples=g["examples"],
                )
            )
    lo, co = lm.get("overflow"), cm.get("overflow")
    if lo is not None and co is not None and lo > 20 and co <= 20 and lm.get("viewport") == cm.get("viewport"):
        hidden = lm.get("viewport_overflow_x") in ("hidden", "clip")
        above = lm.get("above") or [0, 0]
        if not hidden and above[0] > MOBILE_WIDTH + 20 and above[1] <= MOBILE_WIDTH + 20:
            out.append(
                info(
                    "mobile-overflow-above",
                    "Horizontal overflow in mobile width only from boxes above the page"
                    " (Chromium doesn't count them, Firefox does)",
                    px=[lo, co],
                    above=above,
                )
            )
        else:
            out.append(
                finding(
                    "mobile-overflow-hidden" if hidden else "mobile-overflow",
                    "Horizontal overflow in mobile width only in Ladybird"
                    + (" (the viewport can't be scrolled sideways)" if hidden else ""),
                    "info" if hidden else "error",
                    px=[lo, co],
                    viewport_overflow_x=lm.get("viewport_overflow_x"),
                )
            )
    return out


class Mobile(Detector):
    name = "mobile"
    description = "Layout and overflow in mobile width"

    def run(self, bundle):
        return [f.__class__(**f.to_dict()) for f in mobile_findings(bundle)]
