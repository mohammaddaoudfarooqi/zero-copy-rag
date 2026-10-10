"""Temporal worker: hosts the ingestion workflow and its activities.

Run:  uv run python -m pipeline.worker
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import ALL_ACTIVITIES
from .config import settings
from .workflows import ALL_WORKFLOWS


# Sync activities (pymongo, voyage, boto3) run in a thread pool of this size.
ACTIVITY_THREADS = 16


def build_worker(client: Client, executor: ThreadPoolExecutor) -> Worker:
    # Accept no more activities than there are threads. An activity's timeout starts
    # when the worker accepts it, so any excess would sit in the executor queue with
    # its start_to_close timer already running.
    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=ALL_WORKFLOWS,
        activities=ALL_ACTIVITIES,
        activity_executor=executor,
        max_concurrent_activities=ACTIVITY_THREADS,
    )


async def main() -> None:
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )

    with ThreadPoolExecutor(max_workers=ACTIVITY_THREADS) as executor:
        worker = build_worker(client, executor)
        print(
            f"[worker] connected to {settings.temporal_address} "
            f"(ns={settings.temporal_namespace}) on task queue '{settings.temporal_task_queue}'"
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
