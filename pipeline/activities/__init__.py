"""Temporal activities for the ingest pipeline."""

from .ingest import clear_document, embed_staged_batch, fetch_and_stage_chunks, index_document

ALL_ACTIVITIES = [
    fetch_and_stage_chunks,
    embed_staged_batch,
    index_document,
    clear_document,
]
