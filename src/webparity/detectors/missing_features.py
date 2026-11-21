# SPDX-License-Identifier: BSD-2-Clause
"""Features Ladybird reports as missing in its log: ``FIXME: Unimplemented ...``, ``TODO(...)``, ``... is not
implemented``.

Signature: ``missing-feature:<normalized log text>`` (info, digits as N and URLs as <url>), the ten most frequent per
visit. Counted per site in the ``missing_features`` table. Missing features that surface as script exceptions are
reported by ``script_error`` (``missing-feature:js:``), unsupported CSS properties by ``css``
(``missing-feature:css:``).
"""

from __future__ import annotations

from webparity import ladybird_log
from webparity.detectors import Detector
from webparity.detectors._common import info


class MissingFeatures(Detector):
    name = "missing_features"
    description = "FIXME / TODO / not implemented lines in Ladybird's log"

    def run(self, bundle):
        missing = ladybird_log.for_bundle(bundle)["full"]["missing"]
        return [
            info(f"missing-feature:{k[:100]}", f"Missing feature: {k[:100]}", count=n)
            for k, n in sorted(missing.items(), key=lambda kv: -kv[1])[:10]
        ]
