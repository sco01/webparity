# SPDX-License-Identifier: BSD-2-Clause
"""A lone surrogate (page text cut inside an emoji) doesn't stop a bundle from being saved."""

import json

from webparity.bundle import Bundle


def test_lone_surrogate_is_saved(tmp_path):
    bundle = Bundle(tmp_path, {"url": "https://a.example/", "title": "Smile \ud83d"})
    bundle.save()
    bundle.write_json("probe.json", {"text": "\ud83d"})
    bundle.add_events([{"text": "\ud83d"}])
    assert json.loads((tmp_path / "visit.json").read_text(encoding="utf-8"))["title"] == "Smile ?"
    assert json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))["text"] == "?"
    assert json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8"))["text"] == "?"
