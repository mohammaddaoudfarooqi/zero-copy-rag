# The agent eval's scoring and summary, without a network or an agent.

from __future__ import annotations

from infra import eval_agent as ev


def test_expected_phrases_are_found_by_question_prefix():
    expect = ev.expected_for("What are the implementation stages for constitutional AI? Cite your sources.")
    assert "Phase 4: Scale and mature" in expect
    assert ev.expected_for("Something not in the eval set?") == []


def test_a_run_scores_coverage_and_counts_cited_chunks():
    answer = ("Phase 1: Assessment and planning (chunk a0ba520de5f485ac:46) and "
              "Phase 2: Initial implementation (chunk :55).")
    out = ev.score_run(answer, ["Phase 1: Assessment and planning", "Phase 2: Initial implementation",
                                "Phase 3: Optimization", "Phase 4: Scale and mature"])
    assert out["coverage"] == 0.5
    assert out["missing"] == ["Phase 3: Optimization", "Phase 4: Scale and mature"]
    assert out["cited"] == 2


def test_a_question_without_expectations_has_no_coverage():
    assert ev.score_run("anything", [])["coverage"] is None


def test_summary_reports_median_and_range_of_completed_runs():
    runs = [{"status": "completed", "seconds": 100.0, "coverage": 1.0},
            {"status": "completed", "seconds": 300.0, "coverage": 0.5},
            {"status": "completed", "seconds": 200.0, "coverage": 0.75},
            {"status": "failed: TimeoutError", "seconds": 600.0, "coverage": None}]
    out = ev.summarize(runs)
    assert (out["runs"], out["completed"]) == (4, 3)
    assert out["seconds"] == {"median": 200.0, "min": 100.0, "max": 300.0}
    assert out["coverage"] == {"mean": 0.75, "min": 0.5}
