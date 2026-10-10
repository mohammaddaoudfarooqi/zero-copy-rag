"""Cut over the active collection/index/model pointer (blue/green swap).

Run after re-ingesting into the target collection and once its index is queryable:
  uv run python -m pipeline.cutover --to knowledge_v2

Refuses an empty target, which would point every query at a collection with nothing in it.
"""

from __future__ import annotations

import argparse

from .clients import knowledge_collection
from .config import settings
from .config_store import get_active, set_active


def main() -> None:
    parser = argparse.ArgumentParser(description="Flip the active collection/index/model pointer.")
    parser.add_argument("--to", default=settings.knowledge_v2_collection, help="Target collection to activate.")
    parser.add_argument("--model", help="Model now active (default: read from a target doc).")
    parser.add_argument("--dim", type=int, help="Embedding dim (default: read from a target doc).")
    parser.add_argument("--index", default=settings.vector_search_index_name)
    args = parser.parse_args()

    # Infer model/dim from a sample doc in the target if not supplied.
    model, dim = args.model, args.dim
    sample = knowledge_collection(args.to).find_one({}, {"model": 1, "dim": 1})
    if sample is None:
        raise SystemExit(f"cutover: {args.to} is empty; ingest into it before switching retrieval to it")
    model = model or sample.get("model")
    dim = dim or sample.get("dim")
    model = model or settings.voyage_model
    dim = dim or settings.embed_dim

    before = get_active()
    after = set_active(collection=args.to, model=model, dim=dim, index=args.index)
    print(f"cutover: {before['active_collection']} ({before.get('model')}) "
          f"-> {after['active_collection']} ({after['model']}, dim={after['dim']})")
    print("retrieval now reads the new collection. (Old collection left intact for rollback.)")


if __name__ == "__main__":
    main()
