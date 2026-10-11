"""Retrieval eval: how much of each expected answer the top-k chunks actually contain.

Each case in evals/retrieval_cases.json lists phrases quoted from the seed document.
For every question this runs the agent's retrieval (vector_search), reads each hit
back through read_span (so only verified bytes count), and scores the share of
expected phrases found. Running it at several k shows what a read cap leaves out.

Run:  uv run python -m infra.eval_retrieval                # k = 12, the agent's cap
      uv run python -m infra.eval_retrieval --k 12 --k 24  # compare caps
      uv run python -m infra.eval_retrieval --min-recall 0.8   # non-zero exit below it
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pipeline.retrieval import vector_search
from pipeline.spanio import read_span

CASES = Path(__file__).resolve().parents[1] / "evals" / "retrieval_cases.json"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def contains(text: str, phrase: str) -> bool:
    return _norm(phrase) in _norm(text)


def score_case(expect: list[str], texts: list[str]) -> tuple[list[str], list[str]]:
    """(found, missing) phrases across the texts of the retrieved chunks."""
    joined = " ".join(texts)
    found = [p for p in expect if contains(joined, p)]
    return found, [p for p in expect if p not in found]


def run_case(case: dict, k: int) -> dict:
    hits = vector_search(case["question"], k=k)
    reads = [read_span(hit["chunk_id"]) for hit in hits]
    texts = [r["text"] for r in reads if r.get("status") == "ok"]
    found, missing = score_case(case["expect"], texts)
    return {"question": case["question"], "found": found, "missing": missing,
            "unreadable": len(reads) - len(texts)}


def evaluate(k: int, cases: list[dict]) -> dict:
    results = [run_case(c, k) for c in cases]
    phrases = sum(len(r["found"]) + len(r["missing"]) for r in results)
    found = sum(len(r["found"]) for r in results)
    complete = sum(1 for r in results if not r["missing"])
    return {"k": k, "recall": found / phrases if phrases else 0.0,
            "complete_cases": complete, "cases": len(results), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Score retrieval against evals/retrieval_cases.json.")
    parser.add_argument("--k", type=int, action="append", help="Chunks per question (repeatable). Default 12.")
    parser.add_argument("--min-recall", type=float, help="Exit non-zero if recall at the first k is below this.")
    parser.add_argument("--json", action="store_true", help="Print the full results as JSON.")
    args = parser.parse_args()
    cases = json.loads(CASES.read_text())["cases"]
    runs = [evaluate(k, cases) for k in (args.k or [12])]

    if args.json:
        print(json.dumps(runs, indent=2))
    else:
        for run in runs:
            print(f"k={run['k']:>3}  recall {run['recall']:.0%}  "
                  f"complete {run['complete_cases']}/{run['cases']} questions")
        for r in runs[0]["results"]:
            if r["missing"]:
                print(f"  k={runs[0]['k']} misses: {r['question']}")
                for p in r["missing"]:
                    print(f"      - {p}")
    if args.min_recall is not None and runs[0]["recall"] < args.min_recall:
        sys.exit(1)


if __name__ == "__main__":
    main()
