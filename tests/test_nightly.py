# SPDX-License-Identifier: BSD-2-Clause
from webparity import nightly
from webparity.db import DB


def test_summary_text(tmp_path):
    db = DB(tmp_path / "webparity.db")
    text = nightly.summary_text(
        db,
        {
            "build": {"commit": "0123456789abcdef", "date": "2026-11-21 01:12"},
            "canary": {"pages": 29, "failed": {}, "noisy": {}, "seconds": 80},
            "retire": {"candidates_gone": ["a", "b"], "sites_dead": [], "seconds": 1},
            "verify": {"error": "boom", "traceback": "...", "seconds": 0},
            "failed_steps": ["verify"],
        },
    )
    assert text.splitlines()[0] == "Last 24 h: 0 visits, 0 sites, 0 new candidates."
    assert "Build: 0123456789ab 2026-11-21 01:12" in text
    assert "Canary: 29 of 29 pages OK" in text
    assert "retire: candidates_gone 2, sites_dead 0" in text
    assert "traceback" not in text and "Failed: verify" in text
