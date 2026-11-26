# SPDX-License-Identifier: BSD-2-Clause
"""Stability and performance: memory over reloads, CPU when idle, response times, load time.

Signatures (error unless marked info):

- ``memory-leak``: Ladybird's WebContent memory grew at least 100 MB on each of four reloads.
- ``memory-high`` (info): it grew 100 MB twice and then levelled off (1.6-2 GB, released at about:blank: not a leak).
- ``unstable`` (info): the element count differed by more than 40 % between two loads in Ladybird.
- ``cpu-idle``: Ladybird's main page process uses at least 50 % CPU while the page is idle, the reference's under 15 %.
  Both are measured from /proc (the main process found with a busy loop, see ``measure.cpu``). Info when the reference
  reaches under 30 frames a second: the page keeps drawing, and Chromium rasterizes in its GPU process, not measured.
- ``slow-response``: a trivial script takes at least 1,000 ms in Ladybird and under 300 ms in the reference.
- ``slow-load``: the page became stable after at least three times the reference's time and 10 s more.
"""

from __future__ import annotations

from webparity.detectors import Detector
from webparity.detectors._common import OK, finding, gate, info, m, num, reference


class Performance(Detector):
    name = "performance"
    description = "Memory over reloads, idle CPU, response time, load time"

    def run(self, bundle):
        if gate(bundle) != OK:
            return []
        ref = reference(bundle)
        out = []
        lr = m(bundle, "ladybird", "reload")
        memory = [x for x in lr.get("memory") or [] if x]
        if len(memory) >= 5 and all(memory[i + 1] - memory[i] >= 100 for i in range(4)):
            out.append(
                finding("memory-leak", f"WebContent grew {memory[-1] - memory[0]} MB over four reloads", memory=memory)
            )
        elif len(memory) >= 3 and memory[1] - memory[0] >= 100 and memory[2] - memory[1] >= 100:
            out.append(
                info("memory-high", f"WebContent grew {memory[-1] - memory[0]} MB but levelled off", memory=memory)
            )
        elements = m(bundle, "ladybird", "probe").get("elements")
        if lr.get("elements") and elements and abs(lr["elements"] - elements) > 0.4 * elements:
            out.append(
                info(
                    "unstable",
                    "The content differed a lot between two loads in Ladybird",
                    elements=[elements, lr["elements"]],
                )
            )
        li, ci = m(bundle, "ladybird", "idle"), m(bundle, ref, "idle")
        if num(li, "cpu_idle") >= 50 and ci.get("cpu_idle") is not None and ci["cpu_idle"] < 15:
            text = f"Ladybird uses {li['cpu_idle']} % CPU while the page is idle (reference {ci['cpu_idle']} %)"
            fps = ci.get("frame_rate")
            if isinstance(fps, (int, float)) and fps < 30:
                # The page keeps drawing and the reference rasterizes it outside the measured process (2026-11-26)
                out.append(
                    info(
                        "cpu-idle",
                        text + f"; the page keeps drawing ({fps} frames/s in the reference)",
                        cpu=[li["cpu_idle"], ci["cpu_idle"]],
                        frame_rate=fps,
                    )
                )
            else:
                out.append(finding("cpu-idle", text, cpu=[li["cpu_idle"], ci["cpu_idle"]]))
        if num(li, "response_ms") >= 1000 and num(ci, "response_ms") < 300:
            out.append(
                finding(
                    "slow-response",
                    f"The page responds in {li['response_ms']} ms in Ladybird (reference {ci.get('response_ms')} ms)",
                    ms=[li["response_ms"], ci.get("response_ms")],
                )
            )
        ll, cl = m(bundle, "ladybird", "load"), m(bundle, ref, "load")
        tl, tc = num(ll, "seconds") + num(ll, "waited"), num(cl, "seconds") + num(cl, "waited")
        if tl >= 3 * max(tc, 1) and tl - tc >= 10:
            out.append(
                finding(
                    "slow-load",
                    f"The page was ready after {round(tl)} s in Ladybird against {round(tc)} s in the reference",
                    seconds=[tl, tc],
                    stable=[ll.get("stable"), cl.get("stable")],
                )
            )
        return out
