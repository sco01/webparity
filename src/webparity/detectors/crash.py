# SPDX-License-Identifier: BSD-2-Clause
"""Crashes in Ladybird, and crashes that may be security bugs.

Signatures: ``crash:<location>`` (error) - one candidate per crash location, kept even when a repeat visit does not
crash; ``security:<location>`` (error) when the crash may be more than a controlled abort
(``ladybird_log.security_reasons``).
The location comes from the log (a Rust panic's file and message, a VERIFY/ASSERT expression and file, a signal) or,
without one, from Ladybird's crash reports (``SIGSEGV in WebContent``). A session that ended without any crash line
is ``crash:unknown location``.
"""

from __future__ import annotations

from webparity import ladybird_log
from webparity.detectors import Detector
from webparity.detectors._common import finding


class Crash(Detector):
    name = "crash"
    description = "Ladybird crashes (log, crash reports, a session that ended) and possible security bugs"

    def run(self, bundle):
        crash = ladybird_log.for_bundle(bundle)["crash"]
        if not crash["crash"]:
            return []
        location = (crash["locations"] or ["unknown location"])[0][:110]
        reports = [{k: v for k, v in r.items() if k != "commit"} for r in crash["reports"]]
        out = [
            finding(
                f"crash:{location}",
                f"Crash in Ladybird: {location}",
                crash=crash["crash"],
                locations=crash["locations"],
                reports=reports,
            )
        ]
        reasons = ladybird_log.security_reasons(crash)
        if reasons:
            out.append(
                finding(
                    f"security:{location[:100]}",
                    f"Possible security bug ({', '.join(reasons)}): {location[:100]}",
                    reasons=reasons,
                    locations=crash["locations"],
                    reports=reports,
                )
            )
        return out
