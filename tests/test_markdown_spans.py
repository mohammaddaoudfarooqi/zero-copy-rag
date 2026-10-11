# Round-trip tests for the markdown extractor: every chunk's byte span must
# reproduce that chunk's text exactly, across ASCII, CJK, emoji, combining marks, BOM and CRLF.

from __future__ import annotations

import hashlib

import pytest

from pipeline.extractors.base import UnsupportedSource
from pipeline.extractors.factory import get_extractor
from pipeline.extractors.markdown import MarkdownExtractor

FIXTURES = {
    "ascii": "# Title\n\nAlpha beta gamma.\n\n## Second\n\nDelta epsilon.\n",
    "cjk": "# 漢字の見出し\n\nこれはテストです。\n\n## 第二章\n\nさらにテスト。\n",
    "emoji": "# Hi \U0001f600\n\nText with \U0001f680 and \U0001f1ef\U0001f1f5 flags.\n",
    "combining": "# Café notes\n\nNaïve résumé text.\n",
    "bom": "﻿# BOM leads\n\nBody after a byte order mark.\n",
    "crlf": "# CRLF\r\n\r\nLine one.\r\nLine two.\r\n",
    "no_heading": "Just a paragraph with no heading at all.\n",
}


def _extractor() -> MarkdownExtractor:
    return MarkdownExtractor(chunk_size=40, chunk_overlap=10)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_span_reproduces_chunk_text(name):
    body = FIXTURES[name].encode("utf-8")
    chunks = _extractor().chunk(body)
    assert chunks, f"{name} produced no chunks"
    for c in chunks:
        assert body[c.span.start : c.span.end].decode("utf-8") == c.text


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_chunk_text_has_no_outer_whitespace(name):
    for c in _extractor().chunk(FIXTURES[name].encode("utf-8")):
        assert c.text == c.text.strip()


def test_ordinals_are_dense_and_spans_are_monotonic():
    chunks = _extractor().chunk(FIXTURES["ascii"].encode("utf-8"))
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    starts = [c.span.start for c in chunks]
    assert starts == sorted(starts)


def test_bom_stays_inside_the_first_span():
    body = FIXTURES["bom"].encode("utf-8")
    first = _extractor().chunk(body)[0]
    assert first.span.start == 0
    assert first.text.startswith("﻿")


def test_metadata_carries_no_heading_text():
    for c in _extractor().chunk(FIXTURES["ascii"].encode("utf-8")):
        assert "heading" not in c.meta
        assert "extractor" in c.meta
        assert set(c.meta) <= {"extractor", "section", "heading_span", "heading_path"}
        if "heading_span" in c.meta:
            assert set(c.meta["heading_span"]) == {"start", "end"}
        for span in c.meta.get("heading_path", []):
            assert set(span) == {"start", "end"}


def test_heading_span_points_at_the_heading_text():
    body = FIXTURES["ascii"].encode("utf-8")
    chunks = _extractor().chunk(body)
    spans = {c.meta["heading_span"]["start"]: c.meta["heading_span"]["end"]
             for c in chunks if "heading_span" in c.meta}
    titles = {body[s:e].decode("utf-8") for s, e in spans.items()}
    assert titles == {"Title", "Second"}


def test_preamble_before_first_heading_has_no_heading_span():
    body = b"Preamble line.\n\n# Later\n\nBody.\n"
    chunks = _extractor().chunk(body)
    assert "heading_span" not in chunks[0].meta


def test_content_hash_matches_the_span_bytes():
    body = FIXTURES["cjk"].encode("utf-8")
    for c in _extractor().chunk(body):
        digest = hashlib.sha256(c.text.encode("utf-8")).hexdigest()
        assert hashlib.sha256(body[c.span.start : c.span.end]).hexdigest() == digest


def test_invalid_utf8_is_unsupported():
    with pytest.raises(UnsupportedSource, match="utf-8"):
        _extractor().chunk(b"# ok\n\n\xff\xfe not utf-8\n")


def test_factory_accepts_markdown():
    assert get_extractor("a.md").name == "markdown"
    assert get_extractor("a.markdown").name == "markdown"
    assert get_extractor("a", "text/markdown; charset=utf-8").name == "markdown"


@pytest.mark.parametrize("key", ["a.pdf", "a.csv", "a.txt", "a.json", "noext"])
def test_factory_refuses_everything_else(key):
    with pytest.raises(UnsupportedSource):
        get_extractor(key)


# A document exported flat: the phase headings sit at the same level as the sections
# under them, and only the empty-bodied heading tells the reader which phase follows.
STAGES = (
    "# Guide\n\nIntro text.\n\n"
    "## Stages\n\nStages intro.\n\n"
    "### 1. Phase 1: Plan\n\n"
    "### Objectives\n\nObjective one.\n\n"
    "### Key activities\n\nActivity one.\n\n"
    "### 2. Phase 2: Build\n\n"
    "### Objectives\n\nObjective two.\n\n"
    "## Other\n\nOther text.\n"
)


def _paths(doc: str) -> dict[str, list[str]]:
    """Map each chunk's text to the heading texts its heading_path spans decode to."""
    body = doc.encode("utf-8")
    chunks = MarkdownExtractor(chunk_size=400, chunk_overlap=0).chunk(body)
    return {c.text.strip(): [body[s["start"]:s["end"]].decode("utf-8") for s in c.meta.get("heading_path", [])]
            for c in chunks}


def test_heading_path_carries_ancestors_and_the_group_label():
    paths = _paths(STAGES)
    assert paths["### Objectives\n\nObjective one."] == ["Guide", "Stages", "1. Phase 1: Plan", "Objectives"]
    assert paths["### Key activities\n\nActivity one."] == ["Guide", "Stages", "1. Phase 1: Plan", "Key activities"]
    assert paths["### Objectives\n\nObjective two."] == ["Guide", "Stages", "2. Phase 2: Build", "Objectives"]


def test_a_group_label_heading_is_its_own_chunk_under_its_ancestors():
    assert _paths(STAGES)["### 1. Phase 1: Plan"] == ["Guide", "Stages", "1. Phase 1: Plan"]


def test_a_higher_level_heading_ends_the_group():
    assert _paths(STAGES)["## Other\n\nOther text."] == ["Guide", "Other"]


def test_text_before_the_first_heading_has_no_path():
    assert _paths("Preamble only.\n\n# Title\n\nBody.\n")["Preamble only."] == []


def test_an_unnumbered_empty_heading_does_not_replace_a_numbered_group():
    """In the seed document "### Key activities" is empty-bodied too, inside Phase 1."""
    doc = ("## Stages\n\n### 1. Phase 1: Plan\n\n### Key activities\n\n"
           "### Prerequisites\n\nNeeds one.\n\n### 2. Phase 2: Build\n\n### Prerequisites\n\nNeeds two.\n")
    paths = _paths(doc)
    assert paths["### Prerequisites\n\nNeeds one."] == ["Stages", "1. Phase 1: Plan", "Prerequisites"]
    assert paths["### Prerequisites\n\nNeeds two."] == ["Stages", "2. Phase 2: Build", "Prerequisites"]


def test_unnumbered_group_labels_still_work_without_numbered_ones():
    doc = "## Stages\n\n### Planning\n\n### Goals\n\nGoal text.\n\n### Building\n\n### Goals\n\nMore goals.\n"
    paths = _paths(doc)
    assert paths["### Goals\n\nGoal text."] == ["Stages", "Planning", "Goals"]
    assert paths["### Goals\n\nMore goals."] == ["Stages", "Building", "Goals"]


def test_windowing_stops_once_a_window_reaches_the_section_end():
    """A later window would start inside the last one's overlap and repeat its tail."""
    from pipeline.extractors.base import window_indices

    text = "x" * 65
    assert window_indices(text, 0, 65, 40, 10) == [(0, 40), (30, 65)]


def test_no_chunk_is_contained_in_the_one_before_it():
    """On the seed document five tail windows (one just "me.") duplicated bytes already
    in the previous chunk, adding pointers and taking search slots for nothing."""
    from itertools import pairwise
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "seed" / "ethical_governance.md").read_bytes()
    chunks = MarkdownExtractor(chunk_size=1200, chunk_overlap=150).chunk(body)
    for prev, c in pairwise(chunks):
        if prev.meta["section"] == c.meta["section"]:
            assert not (prev.span.start <= c.span.start and c.span.end <= prev.span.end), c.text[:30]
