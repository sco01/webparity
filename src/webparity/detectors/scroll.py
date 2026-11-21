# SPDX-License-Identifier: BSD-2-Clause
"""Scrolling: content that loads on scroll, and pages the mouse wheel can't scroll.

Signatures (error):

- ``scroll-lazy-content``: content that loads on scroll in the reference doesn't in Ladybird - the reference grew by
  at least 50 elements (and 10 %) or 10 resources while scrolling, Ladybird by less than 30 % of that, and Ladybird
  ends with clearly less (when Ladybird loaded the images before scrolling it doesn't grow; 2026-11-08).
- ``scroll-locked``: one wheel step scrolled the reference at least 100 px and Ladybird less than 10 px (#14254). The
  reset before the test is instant, not smooth (2026-11-21: with scroll-behavior: smooth the reset was still animating).
"""

from __future__ import annotations

from webparity.detectors import Detector
from webparity.detectors._common import OK, finding, gate, m, reference


class Scroll(Detector):
    name = "scroll"
    description = "Lazy content on scroll; the wheel test"

    def run(self, bundle):
        if gate(bundle) != OK:
            return []
        ref = reference(bundle)
        out = []
        lw, cw = m(bundle, "ladybird", "wheel"), m(bundle, ref, "wheel")
        if (
            lw.get("scrollable")
            and cw.get("scrollable")
            and (cw.get("scrolled") or 0) >= 100
            and (lw.get("scrolled") or 0) < 10
        ):
            out.append(
                finding(
                    "scroll-locked",
                    "The page can't be scrolled with the wheel in Ladybird, but in the reference",
                    ladybird=lw,
                    reference=cw,
                )
            )
        ls, cs = m(bundle, "ladybird", "scroll"), m(bundle, ref, "scroll")
        if "elements" in ls and "elements" in cs and "resources" in ls and "resources" in cs:
            cv, lv = cs["elements"][1] - cs["elements"][0], ls["elements"][1] - ls["elements"][0]
            cr, lr = cs["resources"][1] - cs["resources"][0], ls["resources"][1] - ls["resources"][0]
            if (
                cv >= max(50, 0.1 * cs["elements"][0]) and lv < 0.3 * cv and ls["elements"][1] < 0.8 * cs["elements"][1]
            ) or (cr >= 10 and lr < 0.3 * cr and ls["resources"][1] < 0.8 * cs["resources"][1]):
                out.append(
                    finding(
                        "scroll-lazy-content",
                        "Content that loads on scroll in the reference doesn't load in Ladybird",
                        elements=[lv, cv],
                        resources=[lr, cr],
                    )
                )
        return out
