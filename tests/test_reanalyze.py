# SPDX-License-Identifier: BSD-2-Clause
from types import SimpleNamespace

from webparity import visit


def test_reanalyze_with_only_compares_those_detectors_alone(monkeypatch):
    bundle = SimpleNamespace(
        data={
            "findings": [
                {"signature": "layout:missing:a", "category": "error", "detector": "layout"},
                {"signature": "script-error:x", "category": "error", "detector": "script"},
            ]
        }
    )
    new = {"signature": "layout:width:b", "category": "error", "detector": "layout"}
    monkeypatch.setattr(visit, "analyze", lambda b, only=None, known=None: [SimpleNamespace(to_dict=lambda: new)])
    gone, added, findings = visit.reanalyze(bundle, only=["layout"])
    assert [f["signature"] for f in gone] == ["layout:missing:a"]  # not the script detector's finding
    assert added == [new] and findings == [new]
    gone_all, _, _ = visit.reanalyze(bundle)
    assert {f["signature"] for f in gone_all} == {"layout:missing:a", "script-error:x"}
