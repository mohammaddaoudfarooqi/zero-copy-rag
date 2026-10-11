"""ReconcileWorkflow: bring the index back in line with the bucket.

Plans with one activity (bucket listing against indexed ETags), then starts the
ordinary IngestWorkflow for every new, changed or orphaned key. An orphan is an
indexed key whose object is gone; its ingest finds the object missing and clears it.
Each ingest uses the key's usual workflow id, so a sweep never races an ingest that
an event already started: that start is skipped, not forced.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.workflow import ParentClosePolicy

with workflow.unsafe.imports_passed_through():
    from ..activities.reconcile import plan_reconcile
    from ..models import ReconcileRequest, S3Ref, doc_id_for_uri
    from .ingest_workflow import IngestWorkflow


@workflow.defn
class ReconcileWorkflow:
    @workflow.run
    async def run(self, req: ReconcileRequest) -> dict:
        plan = await workflow.execute_activity(
            plan_reconcile,
            args=[req.bucket, req.prefix, req.target_collection],
            # Listing a large bucket is the slow part; it is idempotent, so retry it.
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        keys = plan["ingest"] + plan["orphans"]
        started, already_running = 0, 0
        for key in keys[: req.max_starts]:
            ref = S3Ref.make(bucket=req.bucket, key=key)
            try:
                await workflow.start_child_workflow(
                    IngestWorkflow.run,
                    args=[ref, req.target_collection],
                    id=f"ingest-{doc_id_for_uri(ref.s3_uri)}",
                    # The ingests outlive the sweep; ending the sweep must not cancel them.
                    parent_close_policy=ParentClosePolicy.ABANDON,
                )
                started += 1
            except WorkflowAlreadyStartedError:
                already_running += 1
        return {
            "listed": plan["listed"], "indexed": plan["indexed"],
            "to_ingest": len(plan["ingest"]), "orphans": len(plan["orphans"]),
            "started": started, "already_running": already_running,
            "deferred": max(0, len(keys) - req.max_starts),
        }
