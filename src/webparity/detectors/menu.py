# SPDX-License-Identifier: BSD-2-Clause
"""The menu test: the first menu button in the header opens in the reference but not in Ladybird.

Signature: ``menu`` (error). Only when both browsers found the same button and clicked it the same way (real input or
a JS click): when one browser can't click the button (something covers it) the comparison says nothing (2026-11-08).
"""

from __future__ import annotations

from webparity.detectors import Detector
from webparity.detectors._common import OK, finding, gate, m, reference


def opened(d: dict) -> bool:
    return bool(d.get("expanded") or d.get("controlled_visible"))


class Menu(Detector):
    name = "menu"
    description = "A header menu button that opens in the reference but not in Ladybird"

    def run(self, bundle):
        if gate(bundle) != OK:
            return []
        lm, cm = m(bundle, "ladybird", "menu"), m(bundle, reference(bundle), "menu")
        if (
            lm.get("label")
            and lm.get("label") == cm.get("label")
            and lm.get("method") == cm.get("method")
            and opened(cm)
            and not opened(lm)
        ):
            return [
                finding(
                    "menu",
                    f'The menu button "{lm["label"]}" opens in the reference but not in Ladybird',
                    ladybird=lm,
                    reference=cm,
                )
            ]
        return []
