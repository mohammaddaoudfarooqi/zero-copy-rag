"""Agent eval: run one question through the agent several times and report the spread.

A single agent run says little: model latency varies, and so does how much of the
answer the agent reads. This sends the same question N times to an invoke endpoint and
reports wall time and, for questions in evals/retrieval_cases.json, the share of
expected phrases the answer contains, plus the chunks it cites.

Run:  uv run python -m infra.eval_agent                       # local dev stack, 3 runs
      uv run python -m infra.eval_agent --runs 5 --question "What is Constitutional AI?"

With no --url, the local Orchestration Engine's port is read from `docker port`. Its
/invoke reply carries no answer text, so the answer is looked up by execution_id in the
dev stack's execution store, which is a local-only detail.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
import urllib.request
from typing import Any

from infra.eval_retrieval import CASES, score_case
from pipeline.clients import mongo_client

DEFAULT_QUESTION = "What are the implementation stages for constitutional AI? Cite your sources."
_CHUNK_ID = re.compile(r"\b[0-9a-f]{16}:\d+\b")


def local_invoke_url() -> str:
    """The dev stack's OE /invoke URL, from the port Docker mapped for it."""
    cid = subprocess.run(["docker", "ps", "--filter", "name=-oe-", "-q"],
                         capture_output=True, text=True, check=True).stdout.split()
    if not cid:
        raise SystemExit("no local Orchestration Engine container; run `make playground` first")
    port = subprocess.run(["docker", "port", cid[0], "8000"],
                          capture_output=True, text=True, check=True).stdout.split(":")[-1].strip()
    return f"http://127.0.0.1:{port}/invoke"


def stored_answer(execution_id: str) -> str:
    """Answer text of a local execution, from the dev stack's execution store."""
    client = mongo_client()
    for name in client.list_database_names():
        if not name.startswith("mdb_store_"):
            continue
        doc = client[name]["executions"].find_one({"execution_id": execution_id}, {"result": 1})
        if doc:
            return str(doc.get("result") or "")
    return ""


def expected_for(question: str) -> list[str]:
    cases = json.loads(CASES.read_text())["cases"]
    norm = question.strip().rstrip("?").lower()
    for case in cases:
        if norm.startswith(case["question"].strip().rstrip("?").lower()):
            return case["expect"]
    return []


def score_run(answer: str, expect: list[str]) -> dict[str, Any]:
    found, missing = score_case(expect, [answer]) if expect else ([], [])
    return {"coverage": len(found) / len(expect) if expect else None, "missing": missing,
            "cited": len(set(_CHUNK_ID.findall(answer)) | set(re.findall(r"chunk\s*:(\d+)", answer)))}


def summarize(runs: list[dict]) -> dict[str, Any]:
    done = [r for r in runs if r["status"] == "completed"]
    times = [r["seconds"] for r in done]
    cov = [r["coverage"] for r in done if r["coverage"] is not None]
    return {
        "runs": len(runs), "completed": len(done),
        "seconds": {"median": statistics.median(times), "min": min(times), "max": max(times)} if times else None,
        "coverage": {"mean": statistics.fmean(cov), "min": min(cov)} if cov else None,
    }


def invoke(url: str, question: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps({"message": question}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
    except Exception as exc:  # noqa: BLE001 - a failed run is a result, not a crash
        return {"status": f"failed: {type(exc).__name__}", "seconds": time.monotonic() - t0, "answer": ""}
    answer = str(body.get("response") or "") or stored_answer(body.get("execution_id", ""))
    return {"status": body.get("status", "unknown"), "seconds": time.monotonic() - t0,
            "answer": answer, "execution_id": body.get("execution_id")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one question through the agent N times.")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--url", help="Invoke endpoint. Default: the local dev stack's OE.")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    url = args.url or local_invoke_url()
    expect = expected_for(args.question)
    runs = []
    for i in range(args.runs):
        run = invoke(url, args.question, args.timeout)
        run.update(score_run(run.pop("answer"), expect))
        runs.append(run)
        cov = "n/a" if run["coverage"] is None else f"{run['coverage']:.0%}"
        print(f"run {i + 1}: {run['status']:<10} {run['seconds']:6.0f}s  coverage {cov:>4}  "
              f"cites {run['cited']:>2} chunks" + (f"  missing: {run['missing']}" if run["missing"] else ""))
    print(json.dumps(summarize(runs), indent=2))


if __name__ == "__main__":
    main()
