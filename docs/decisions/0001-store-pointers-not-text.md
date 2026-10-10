# 1. Store pointers to source bytes, not chunk text

- Status: Accepted
- Date: 2026-10-11

## Context

A RAG index usually stores each chunk's text next to its embedding. That puts a second copy of
every document in the database, outside the access controls of the object store that already
holds it, and the copy goes stale silently when the source object changes or is deleted.

## Decision

`knowledge_zc` stores, per chunk, a byte span into the unmodified S3 object, the SHA-256 of
those bytes, the embedding, and the object's bucket, key and ETag. It stores no text.

- Extractors emit byte spans. The extractor base enforces one invariant:
  `body[span.start:span.end].decode("utf-8") == chunk.text`.
- Retrieval (`pipeline/retrieval.py`) returns pointers only; its projection has no text field.
- `pipeline/spanio.read_span(chunk_id)` is the only path from a pointer back to text. It looks
  the pointer up server side, issues a ranged GET conditioned on the ETag, and checks the hash.
  It answers `ok` with text, or `stale`, `missing` or `unknown` with none.
- `read_span` takes only a `chunk_id`. A caller that could pass a bucket, key and range could read
  anything the credential reaches, and a caller-supplied hash would certify whatever it found.

## Consequences

- The bucket holds the only copy of the text, under the access policy it already has.
- A changed object reads as `stale` and a deleted one as `missing`, instead of being cited from
  an old copy.
- Every read of a chunk costs an S3 request, and the agent's runtime needs `s3:GetObject` on the
  bucket and egress to it.
- Only formats whose text is a contiguous byte range of the stored object can be indexed.
  Markdown qualifies. PDF, DOCX and XLSX do not, because their text is reconstructed.
- An embedding-model change cannot re-embed from the index, because there is no text in it. It is
  a re-ingest from S3 into a second collection, followed by a pointer flip (`make cutover`).

## Alternatives considered

- **Store text and embeddings together.** Simplest and fastest to read, and it is the copy this
  project exists to avoid.
- **Store a separate extracted-text copy beside the bucket.** Supports formats like PDF, but it is
  again a second copy that can drift from the source.
