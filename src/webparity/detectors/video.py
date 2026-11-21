# SPDX-License-Identifier: BSD-2-Clause
"""Video that hasn't started in Ladybird.

Signature: ``video:not-started`` (error): more visible videos with a source and readyState 0 than in the reference.
Hidden videos don't count (2026-11-17: a player with two video elements in the same place hides the one it doesn't
use - in Ladybird the MSE element stayed at readyState 0 while the mp4 element played). Several known media bugs are
page tests in ``known/media.yaml`` (prefix ``video``).
"""

from __future__ import annotations

from webparity.detectors import Detector
from webparity.detectors._common import OK, finding, gate, m, num, reference


class Video(Detector):
    name = "video"
    description = "Videos that haven't started in Ladybird"

    def run(self, bundle):
        if gate(bundle) != OK:
            return []
        lp, cp = m(bundle, "ladybird", "probe"), m(bundle, reference(bundle), "probe")
        if num(lp, "video_stalled") > num(cp, "video_stalled"):
            return [
                finding(
                    "video:not-started",
                    "A video that hasn't started in Ladybird",
                    count=[lp.get("video_stalled"), cp.get("video_stalled")],
                )
            ]
        return []
