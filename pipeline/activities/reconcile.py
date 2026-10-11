"""Plan a reconciliation sweep: what the bucket holds against what the index points at.

The bucket is the source of truth. A sweep catches what event notifications missed:
objects whose ObjectCreated or ObjectRemoved event was dropped, and anything uploaded
before the trigger existed. It compares ETags from the bucket listing with the ETags
recorded on the indexed pointers, so unchanged objects are never downloaded.
"""

from __future__ import annotations

import re

from temporalio import activity

from ..clients import knowledge_collection, s3_client
from ..config import settings
from ..extractors.factory import has_supported_extension


def _listed(bucket: str, prefix: str) -> dict[str, str]:
    """key -> ETag (unquoted) for every object the extractor factory would accept."""
    kwargs = {"Bucket": bucket, **({"Prefix": prefix} if prefix else {})}
    out: dict[str, str] = {}
    for page in s3_client().get_paginator("list_objects_v2").paginate(**kwargs):
        for obj in page.get("Contents", []):
            if has_supported_extension(obj["Key"]):
                out[obj["Key"]] = str(obj.get("ETag", "")).strip('"')
    return out


def _indexed(bucket: str, prefix: str, target_collection: str | None) -> dict[str, str]:
    """key -> ETag recorded on that key's indexed pointers."""
    match: dict = {"bucket": bucket}
    if prefix:
        match["key"] = {"$regex": "^" + re.escape(prefix)}
    coll = knowledge_collection(target_collection or settings.knowledge_collection)
    rows = coll.aggregate([{"$match": match}, {"$group": {"_id": "$key", "etag": {"$first": "$etag"}}}])
    return {row["_id"]: row["etag"] for row in rows}


@activity.defn
def plan_reconcile(bucket: str, prefix: str = "", target_collection: str | None = None) -> dict:
    listed = _listed(bucket, prefix)
    indexed = _indexed(bucket, prefix, target_collection)
    ingest = sorted(k for k, etag in listed.items() if indexed.get(k) != etag)
    orphans = sorted(k for k in indexed if k not in listed)
    activity.logger.info(
        "reconcile s3://%s/%s: %d listed, %d indexed, %d to ingest, %d orphaned",
        bucket, prefix, len(listed), len(indexed), len(ingest), len(orphans),
    )
    return {"ingest": ingest, "orphans": orphans, "listed": len(listed), "indexed": len(indexed)}
