# SPDX-License-Identifier: BSD-2-Clause
"""Layout differences in desktop width: the element where a difference starts, from the layout maps.

Signatures: ``layout:<kind>:<parent display>><display>`` (error), kind one of ``missing``, ``extra``, ``width``,
``height``, ``position``; the display is the tag for replaced elements and ``reference≠ladybird`` when the displays
differ (``layout:extra:block>none≠block``). At most the two largest groups (by area, at least 1,500 px²). More than
eight different groups on one page usually means the browsers got different variants of the page: then one
``layout-differs-a-lot`` (info) instead. Elements that differ between the reference's own two loads are masked
(``<reference>-layout-reload.json.gz``). The comparison itself is ``detectors._layout_map.compare``.
"""

from __future__ import annotations

from webparity.detectors import Detector
from webparity.detectors._common import OK, cache, finding, gate, info, layout_map, reference
from webparity.detectors._layout_map import compare

MIN_AREA = 1500


def comparison(bundle) -> dict | None:
    c = cache(bundle)
    if "layout" not in c:
        ref = reference(bundle)
        c["layout"] = (
            compare(
                layout_map(bundle, "ladybird"),
                layout_map(bundle, ref),
                layout_map(bundle, ref, "layout-reload"),
            )
            if gate(bundle) == OK
            else None
        )
    return c["layout"]


def layout_findings(bundle) -> list:
    c = cache(bundle)
    if "layout_findings" in c:
        return c["layout_findings"]
    out = []
    result = comparison(bundle) or {}
    groups = [g for g in result.get("groups") or [] if g["area"] >= MIN_AREA]
    if len(groups) > 8:
        out.append(
            info(
                "layout-differs-a-lot",
                f"{len(groups)} different layout differences - probably different variants of the page",
                groups=[g["sig"] for g in groups],
            )
        )
    else:
        for g in groups[:2]:
            out.append(
                finding(
                    f"layout:{g['sig']}",
                    f"Layout difference ({g['sig']}, {g['count']} elements)",
                    count=g["count"],
                    area=g["area"],
                    examples=g["examples"],
                    unstable_in_reference=result.get("unstable_in_reference"),
                )
            )
    c["layout_findings"] = out
    return out


class Layout(Detector):
    name = "layout"
    description = "Layout map comparison in desktop width, masked by the reference's reload"

    def run(self, bundle):
        return [f.__class__(**f.to_dict()) for f in layout_findings(bundle)]
