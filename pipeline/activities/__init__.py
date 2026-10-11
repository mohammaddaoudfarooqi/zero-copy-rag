"""Temporal activities for the ingest pipeline."""

from .ingest import (
    clear_document,
    embed_staged_batch,
    fetch_and_stage_chunks,
    index_document,
)
from .reconcile import plan_reconcile

ALL_ACTIVITIES = [
    fetch_and_stage_chunks,
    embed_staged_batch,
    index_document,
    clear_document,
    plan_reconcile,
]
