# AWS Lambda entrypoint: S3 object event -> Temporal IngestWorkflow.
# Thin adapter over the shared handle_s3_event core in pipeline/trigger.py.

from __future__ import annotations

import asyncio

from .trigger import get_client, handle_s3_event


async def _run(event: dict) -> list[str]:
    # A fresh client per invocation: each invocation runs on its own event loop
    # (asyncio.run), and a Temporal client is bound to the loop it was created on.
    client = await get_client()
    return await handle_s3_event(client, event)


def lambda_handler(event: dict, context) -> dict:
    """S3-triggered Lambda handler. Wire to ObjectCreated and ObjectRemoved notifications."""
    return {"started": asyncio.run(_run(event))}
