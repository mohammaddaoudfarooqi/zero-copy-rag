"""Shared trigger logic: start an IngestWorkflow for an S3 object.

Used by the AWS Lambda (`lambda_handler`) and the seed scripts, which upload and then
start the ingest themselves. Re-uploads terminate any in-flight ingest for the same doc and start fresh, so the search
index is updated in place rather than duplicated.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import TLSConfig

from .config import Settings, settings
from .models import S3Ref, doc_id_for_uri
from .s3util import refs_from_s3_event

INGEST_WORKFLOW = "IngestWorkflow"  # referenced by name to avoid importing workflow deps


def connect_options(s: Settings) -> dict[str, Any]:
    """Keyword arguments for Client.connect beyond the target address.

    Blank credentials connect without TLS (a local or self-hosted server). For Temporal
    Cloud set TEMPORAL_API_KEY, which temporalio pairs with TLS by itself, or both
    TEMPORAL_TLS_CERT and TEMPORAL_TLS_KEY for mTLS.
    """
    opts: dict[str, Any] = {"namespace": s.temporal_namespace}
    cert, key = s.temporal_tls_cert.strip(), s.temporal_tls_key.strip()
    if bool(cert) != bool(key):
        raise ValueError("mTLS needs both TEMPORAL_TLS_CERT and TEMPORAL_TLS_KEY")
    if cert and s.temporal_api_key:
        raise ValueError("set either TEMPORAL_API_KEY or the TEMPORAL_TLS_* pair, not both")
    if s.temporal_api_key:
        opts["api_key"] = s.temporal_api_key
    if cert:
        opts["tls"] = TLSConfig(
            client_cert=Path(cert).read_bytes(), client_private_key=Path(key).read_bytes()
        )
    return opts


async def get_client() -> Client:
    """The one way this repo connects to Temporal: worker, Lambda and seed scripts."""
    return await Client.connect(settings.temporal_address, **connect_options(settings))


async def handle_s3_event(client: Client, event: dict | str | bytes) -> list[str]:
    """Parse an S3 ObjectCreated event and start an IngestWorkflow per object.

    The entrypoint for event adapters such as the AWS Lambda (`lambda_handler`). Returns
    the started workflow ids (empty for a non-object event such as S3's `s3:TestEvent`).
    """
    return [await start_ingest(client, ref) for ref in refs_from_s3_event(event)]


async def start_ingest(client: Client, ref: S3Ref) -> str:
    """Start (or restart) the IngestWorkflow for one S3 object. Returns the workflow id."""
    doc_id = doc_id_for_uri(ref.s3_uri)
    handle = await client.start_workflow(
        INGEST_WORKFLOW,
        ref,
        id=f"ingest-{doc_id}",
        task_queue=settings.temporal_task_queue,
        # Re-upload of the same key replaces any running ingest -> update in place.
        id_conflict_policy=WorkflowIDConflictPolicy.TERMINATE_EXISTING,
    )
    return handle.id


def start_ingests(refs: list[S3Ref]) -> list[str]:
    """Start an IngestWorkflow per ref from synchronous code. Returns the workflow ids.

    The seed scripts use this after uploading. Nothing emits an object-created event
    locally, so without it an upload would sit in the bucket unindexed. If an S3 event
    trigger is also wired to the bucket, both starts share one workflow id: the later one
    replaces the earlier, and the index is updated in place rather than duplicated.
    """

    async def _run() -> list[str]:
        client = await get_client()
        return [await start_ingest(client, ref) for ref in refs]

    return asyncio.run(_run())
