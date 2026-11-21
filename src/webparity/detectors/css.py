# SPDX-License-Identifier: BSD-2-Clause
"""CSS: fewer rules in the same stylesheet, and declarations Ladybird dropped.

Signatures:

- ``css-rules`` (error): fewer CSS rules than the reference in the same stylesheet (only sheets both can read; at least
  20 rules, at least 5 and 10 % fewer), after subtracting rules Ladybird drops for known reasons (the probe's
  ``css_known`` in the reference: View Transitions pseudo-classes, ``@starting-style`` #14121, ``:-webkit-any()``,
  ``::-webkit-scrollbar``). ``css-rules-known`` (info) when the known reasons explain it.
- ``css-dropped:<property>:<value kind>``: a declaration the reference has and Ladybird dropped (from the interact
  step's CSS comparison), e.g. ``css-dropped:background-image:-webkit-radial-gradient``. Error only for unprefixed
  properties, the prefixed ones pages rely on (``-webkit-line-clamp``, ``-webkit-mask`` ...) and gradients - at most two
  errors per visit; prefixed values other than gradients and ``-webkit-box`` are info. Harmless ones are info
  (``-webkit-text-stroke`` with width 0 or only a colour: 0px on 180 sites; outline with ``-webkit-focus-ring-color``,
  which Firefox drops too; ``appearance: base-select``; unitless ``perspective``, only visible in 3D). An error
  ``css-dropped`` becomes info when the page doesn't also look different (no layout, mobile layout or image finding;
  2026-11-09: half the visits were candidates without a visible difference).
- ``missing-feature:css:<property>`` (info): Ladybird doesn't support the property at all (``CSS.supports``), unless it
  is one of the prefixed properties pages rely on.
"""

from __future__ import annotations

import re

from webparity.detectors import Detector
from webparity.detectors._common import OK, finding, gate, info, m, reference, resource_key
from webparity.detectors.image import image_findings
from webparity.detectors.layout import layout_findings
from webparity.detectors.mobile import mobile_findings

# Prefixed properties that show on the page and that sites use without an unprefixed equivalent
IMPORTANT_PREFIXED = re.compile(
    r"^-webkit-(line-clamp|box-orient|mask|background-clip|text-fill-color|text-stroke|box-reflect|appearance|user-select)"
)
PREFIX = re.compile(r"-(webkit|moz|ms|o)-")


def _per_sheet(d: dict | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, v in (d or {}).items():
        n = k if k.startswith("inline:") else resource_key(k)
        out[n] = out.get(n, 0) + (v or 0)
    return out


class Css(Detector):
    name = "css"
    description = "Fewer CSS rules per stylesheet; dropped declarations; unsupported properties"

    def run(self, bundle):
        if gate(bundle) != OK:
            return []
        ref = reference(bundle)
        lp, cp = m(bundle, "ladybird", "probe"), m(bundle, ref, "probe")
        out = []
        lc, cc = _per_sheet(lp.get("css")), _per_sheet(cp.get("css"))
        known: dict[str, dict[str, int]] = {}
        for k, v in (cp.get("css_known") or {}).items():
            n = k if k.startswith("inline:") else resource_key(k)
            for reason, x in (v or {}).items():
                known.setdefault(n, {})[reason] = known.get(n, {}).get(reason, 0) + x
        fewer_all = [
            (k, lc[k], cc[k]) for k in cc if k in lc and cc[k] >= 20 and lc[k] < 0.9 * cc[k] and cc[k] - lc[k] >= 5
        ]
        fewer, explained = [], []
        for k, lb, c in fewer_all:
            adjusted = c - sum((known.get(k) or {}).values())
            (fewer if lb < 0.9 * adjusted and adjusted - lb >= 5 else explained).append((k, lb, c, known.get(k) or {}))
        if fewer:
            out.append(
                finding(
                    "css-rules", "Fewer CSS rules than in the reference in the same stylesheet", stylesheets=fewer[:5]
                )
            )
        elif explained:
            out.append(
                info(
                    "css-rules-known",
                    "Fewer CSS rules, explained by known reasons (View Transitions, @starting-style, :-webkit-any ...)",
                    stylesheets=explained[:5],
                )
            )
        dropped = (m(bundle, "ladybird", "css") or {}).get("dropped") or []
        focus_ring = any(x.get("kind") == "-webkit-focus-ring-color" for x in dropped)
        errors = 0
        for x in dropped:
            prop, kind = x.get("property", ""), x.get("kind", "")
            if x.get("supported") is False and not IMPORTANT_PREFIXED.match(prop):
                out.append(
                    info(
                        f"missing-feature:css:{prop}"[:120],
                        f"Ladybird doesn't support {prop} ({x.get('count')} rules)",
                        count=x.get("count"),
                        examples=(x.get("examples") or [])[:1],
                    )
                )
                continue
            # Prefixed values (-webkit-optimize-contrast, -webkit-left ...) are info too, except gradients and
            # -webkit-box
            prefixed_value = PREFIX.match(kind) and not kind.endswith("gradient") and kind != "-webkit-box"
            important = (
                not PREFIX.match(prop) or IMPORTANT_PREFIXED.match(prop) or kind.endswith("gradient")
            ) and not prefixed_value
            # Harmless (2026-11-09/10): -webkit-text-stroke with width 0 or only a colour paints nothing; outline with
            # Chromium's -webkit-focus-ring-color is dropped by Firefox too - the whole shorthand (outline: 5px auto
            # -webkit-focus-ring-color), so also outline-width/-style when the page has it; appearance: base-select
            # (customizable selects) is missing in Firefox too - a feature, not a bug; perspective: sites write
            # -webkit-perspective:1000 without a unit, Chromium and Firefox accept it as 1000px, Ladybird doesn't - only
            # visible with 3D rotation
            harmless = (
                prop == "-webkit-text-stroke-color"
                or kind == "-webkit-focus-ring-color"
                or kind == "base-select"
                or prop in ("perspective", "-webkit-perspective")
                or (
                    prop == "-webkit-text-stroke-width"
                    and all(str(e.get("reference")) in ("0px", "0") for e in x.get("examples") or [])
                )
                or (focus_ring and prop.startswith("outline"))
            )
            important = bool(important) and not harmless
            if important and errors >= 2:
                continue
            errors += important
            out.append(
                finding(
                    f"css-dropped:{prop}:{kind}"[:120],
                    f"Ladybird dropped {prop} ({kind}) in {x.get('count')} rules",
                    "error" if important else "info",
                    count=x.get("count"),
                    examples=x.get("examples"),
                    supported=x.get("supported"),
                )
            )
        visible = any(
            f.signature.startswith(("layout:", "mobile-layout:", "image-differs"))
            for f in layout_findings(bundle) + mobile_findings(bundle) + image_findings(bundle)
        )
        if not visible:
            for f in out:
                if f.signature.startswith("css-dropped:") and f.category == "error":
                    f.category = "info"
                    f.data["no_visible_difference"] = True
        return out
