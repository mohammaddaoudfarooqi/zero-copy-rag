# cutover.py flips the active-collection pointer that retrieval and read_span
# follow. Pointing it at an empty collection would succeed and silently answer
# every query from nothing, so it has to refuse.

from __future__ import annotations

import pytest

from pipeline import cutover


class _FakeColl:
    def __init__(self, sample):
        self._sample = sample

    def find_one(self, *args, **kwargs):
        return self._sample


@pytest.fixture
def flips(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(cutover, "get_active", lambda: {"active_collection": "knowledge_zc", "model": "voyage-3.5"})

    def _set_active(**kwargs):
        calls.append(kwargs)
        return {"active_collection": kwargs["collection"], **kwargs}

    monkeypatch.setattr(cutover, "set_active", _set_active)
    return calls


def test_an_empty_target_is_refused_and_the_pointer_is_not_touched(monkeypatch, flips):
    monkeypatch.setattr(cutover, "knowledge_collection", lambda name: _FakeColl(None))
    monkeypatch.setattr("sys.argv", ["cutover", "--to", "knowledge_v2"])
    with pytest.raises(SystemExit, match="knowledge_v2 is empty"):
        cutover.main()
    assert flips == []


def test_model_and_dim_are_read_from_the_target(monkeypatch, flips):
    monkeypatch.setattr(cutover, "knowledge_collection", lambda name: _FakeColl({"model": "voyage-3-large", "dim": 2048}))
    monkeypatch.setattr("sys.argv", ["cutover", "--to", "knowledge_v2"])
    cutover.main()
    assert flips[0]["collection"] == "knowledge_v2"
    assert (flips[0]["model"], flips[0]["dim"]) == ("voyage-3-large", 2048)
