# The retrieval eval scores how many expected phrases the top-k chunks contain, read
# back through the zero-copy path. These tests pin the scoring, and that the eval set
# itself is valid against the seed document.

from __future__ import annotations

import json
from pathlib import Path

from infra import eval_retrieval as ev

ROOT = Path(__file__).resolve().parents[1]


def test_matching_ignores_case_and_whitespace():
    assert ev.contains("### 1. Phase 1:\n  Assessment and PLANNING", "phase 1: assessment and planning")
    assert not ev.contains("Phase 2", "Phase 1")


def test_a_case_scores_the_share_of_phrases_found():
    texts = ["... Phase 1: Assessment and planning ...", "Phase 3: Optimization here"]
    found, missing = ev.score_case(["Phase 1: Assessment and planning", "Phase 2: Initial implementation",
                                    "Phase 3: Optimization"], texts)
    assert found == ["Phase 1: Assessment and planning", "Phase 3: Optimization"]
    assert missing == ["Phase 2: Initial implementation"]


def test_only_verified_reads_count(monkeypatch):
    """A stale or missing span carries no text, so it cannot satisfy a phrase."""
    hits = [{"chunk_id": "d:0"}, {"chunk_id": "d:1"}]
    monkeypatch.setattr(ev, "vector_search", lambda q, k: hits)
    monkeypatch.setattr(ev, "read_span", lambda c: {"chunk_id": c, "status": "ok", "text": "alpha"}
                        if c == "d:0" else {"chunk_id": c, "status": "stale"})
    result = ev.run_case({"question": "q", "expect": ["alpha", "beta"]}, k=2)
    assert result["found"] == ["alpha"] and result["missing"] == ["beta"]
    assert result["unreadable"] == 1


def test_every_expected_phrase_is_in_the_seed_document():
    """A typo in the eval set would read as a retrieval miss forever."""
    doc = (ROOT / "seed" / "ethical_governance.md").read_text()
    cases = json.loads((ROOT / "evals" / "retrieval_cases.json").read_text())["cases"]
    assert len(cases) >= 10
    for case in cases:
        for phrase in case["expect"]:
            assert ev.contains(doc, phrase), phrase
