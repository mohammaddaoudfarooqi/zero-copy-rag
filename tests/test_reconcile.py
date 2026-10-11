# The reconciliation sweep compares the bucket with the index and plans the ingests
# that bring them back in line: new or changed objects, and indexed keys whose object
# is gone (a delete event that never arrived). Unchanged objects are not downloaded.

from __future__ import annotations

from datetime import timedelta

import pytest

from pipeline import reconcile
from pipeline.activities import reconcile as rec
from pipeline.extractors.factory import has_supported_extension


class _Paginator:
    def __init__(self, pages):
        self.pages = pages
        self.kwargs = None

    def paginate(self, **kwargs):
        self.kwargs = kwargs
        return iter(self.pages)


class _FakeS3:
    def __init__(self, pages):
        self.paginator = _Paginator(pages)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self.paginator


class _FakeKnow:
    """Answers the $group-by-key aggregation with (key, etag) pairs."""

    def __init__(self, indexed):
        self.indexed = indexed
        self.pipeline = None

    def aggregate(self, pipeline):
        self.pipeline = pipeline
        prefix = next((s["$match"]["key"]["$regex"] for s in pipeline
                       if "$match" in s and "key" in s["$match"]), "^")
        import re
        return [{"_id": k, "etag": e} for k, e in self.indexed.items() if re.match(prefix, k)]


def _obj(key, etag):
    return {"Key": key, "ETag": f'"{etag}"'}


@pytest.fixture
def plan(monkeypatch):
    def _plan(listed_pages, indexed, prefix=""):
        s3, know = _FakeS3(listed_pages), _FakeKnow(indexed)
        monkeypatch.setattr(rec, "s3_client", lambda: s3)
        monkeypatch.setattr(rec, "knowledge_collection", lambda name=None: know)
        return rec.plan_reconcile("b", prefix), s3, know
    return _plan


def test_new_changed_and_orphaned_keys_are_planned(plan):
    out, *_ = plan(
        [{"Contents": [_obj("same.md", "e1"), _obj("changed.md", "new"), _obj("new.md", "e3")]}],
        {"same.md": "e1", "changed.md": "old", "gone.md": "e9"},
    )
    assert sorted(out["ingest"]) == ["changed.md", "new.md"]
    assert out["orphans"] == ["gone.md"]
    assert (out["listed"], out["indexed"]) == (3, 3)


def test_an_unchanged_bucket_plans_nothing(plan):
    out, *_ = plan([{"Contents": [_obj("a.md", "e1")]}], {"a.md": "e1"})
    assert out["ingest"] == [] and out["orphans"] == []


def test_etag_quotes_are_ignored(plan):
    """S3 lists ETags quoted; the index stores them stripped (from the GET)."""
    out, *_ = plan([{"Contents": [{"Key": "a.md", "ETag": '"abc"'}]}], {"a.md": "abc"})
    assert out["ingest"] == []


def test_unsupported_keys_are_not_planned(plan):
    """Non-Markdown objects never get pointers, so planning them would re-run them forever."""
    out, *_ = plan([{"Contents": [_obj("a.pdf", "e"), _obj("b.txt", "e"), _obj("c.md", "e")]}], {})
    assert out["ingest"] == ["c.md"]
    assert out["listed"] == 1


def test_the_prefix_scopes_listing_and_orphans(plan):
    out, s3, _know = plan([{"Contents": [_obj("docs/a.md", "e")]}],
                         {"docs/a.md": "e", "docs/gone.md": "e", "other/x.md": "e"},
                         prefix="docs/")
    assert s3.paginator.kwargs == {"Bucket": "b", "Prefix": "docs/"}
    assert out["orphans"] == ["docs/gone.md"]


def test_listing_follows_every_page(plan):
    out, *_ = plan([{"Contents": [_obj("a.md", "e")]}, {"Contents": [_obj("b.md", "e")]}, {}], {})
    assert sorted(out["ingest"]) == ["a.md", "b.md"]


def test_the_index_query_is_scoped_to_the_bucket(plan):
    _, _, know = plan([{}], {})
    match = next(s["$match"] for s in know.pipeline if "$match" in s)
    assert match["bucket"] == "b"


@pytest.mark.parametrize(("key", "ok"), [("a.md", True), ("A.MARKDOWN", True), ("a.mdx", False),
                                         ("a.pdf", False), ("noext", False)])
def test_has_supported_extension(key, ok):
    assert has_supported_extension(key) is ok


@pytest.mark.parametrize(("text", "delta"), [("1h", timedelta(hours=1)), ("30m", timedelta(minutes=30)),
                                             ("2d", timedelta(days=2)), ("90s", timedelta(seconds=90))])
def test_schedule_intervals_parse(text, delta):
    assert reconcile.parse_interval(text) == delta


@pytest.mark.parametrize("text", ["", "1", "h", "1w", "-1h", "0m"])
def test_bad_schedule_intervals_are_refused(text):
    with pytest.raises(ValueError):
        reconcile.parse_interval(text)
