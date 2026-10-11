"""Data contracts passed across Temporal activity/workflow boundaries.

These are plain dataclasses so they serialize cleanly through Temporal's default
(JSON) data converter. Keep them free of any client objects or non-serializable state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class S3Ref:
    """A reference to a single object in S3 (the unit passed to the IngestWorkflow)."""

    bucket: str
    key: str
    s3_uri: str
    etag: str = ""
    size: int = 0
    content_type: str = ""

    @staticmethod
    def make(bucket: str, key: str, etag: str = "", size: int = 0, content_type: str = "") -> S3Ref:
        return S3Ref(
            bucket=bucket,
            key=key,
            s3_uri=f"s3://{bucket}/{key}",
            etag=etag.strip('"'),
            size=size,
            content_type=content_type,
        )


@dataclass
class Span:
    """A byte range in the unmodified source object. End is exclusive."""

    start: int
    end: int
    kind: str = "byte"


@dataclass
class ReconcileRequest:
    """Input to ReconcileWorkflow: which bucket (and prefix) to bring back in line."""

    bucket: str
    prefix: str = ""
    target_collection: str | None = None
    max_starts: int = 500  # ingests started per run; the rest wait for the next sweep


def doc_id_for_uri(uri: str) -> str:
    """Stable, filesystem/workflow-id-safe document id derived from a source URI."""
    return hashlib.sha1(uri.encode("utf-8")).hexdigest()[:16]


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
