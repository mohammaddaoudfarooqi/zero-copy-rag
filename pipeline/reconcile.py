"""Run or schedule the reconciliation sweep.

  uv run python -m pipeline.reconcile                 # one sweep of S3_BUCKET, prints the result
  uv run python -m pipeline.reconcile --prefix docs/  # only keys under docs/
  uv run python -m pipeline.reconcile --every 1h      # create or update a Temporal Schedule
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import timedelta

from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
)
from temporalio.common import WorkflowIDConflictPolicy

from .config import settings
from .models import ReconcileRequest
from .trigger import get_client

_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_interval(text: str) -> timedelta:
    """'90s', '30m', '1h' or '2d' as a positive timedelta."""
    m = re.fullmatch(r"(\d+)([smhd])", text.strip())
    if not m or int(m.group(1)) == 0:
        raise ValueError(f"interval {text!r} is not a positive number followed by s, m, h or d")
    return timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})


async def _run(req: ReconcileRequest, every: str | None) -> None:
    client = await get_client()
    wf_id = f"reconcile-{req.bucket}"
    if every is None:
        result = await client.execute_workflow(
            "ReconcileWorkflow", req, id=wf_id, task_queue=settings.temporal_task_queue,
            # A sweep already running for this bucket covers this one: wait for it.
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        print(json.dumps(result, indent=2))
        return

    interval = parse_interval(every)
    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            "ReconcileWorkflow", req, id=wf_id, task_queue=settings.temporal_task_queue
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=interval)]),
        # A sweep that outlasts the interval is not doubled up.
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )
    try:
        await client.create_schedule(wf_id, schedule)
        print(f"created schedule {wf_id}: every {every}")
    except ScheduleAlreadyRunningError:
        await client.get_schedule_handle(wf_id).update(lambda _: ScheduleUpdate(schedule=schedule))
        print(f"updated schedule {wf_id}: every {every}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bring the index back in line with the bucket.")
    parser.add_argument("--bucket", default=settings.s3_bucket)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--max-starts", type=int, default=500)
    parser.add_argument("--every", help="Schedule the sweep at this interval (e.g. 1h) instead of running it once.")
    args = parser.parse_args()
    if not args.bucket:
        parser.error("no bucket: set S3_BUCKET or pass --bucket")
    req = ReconcileRequest(bucket=args.bucket, prefix=args.prefix, max_starts=args.max_starts)
    asyncio.run(_run(req, args.every))


if __name__ == "__main__":
    main()
