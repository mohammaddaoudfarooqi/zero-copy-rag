# Guard rails for the one claim this project makes: nothing in the write path
# may persist text, and the default collection must be the zero-copy one.

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_default_collection_is_the_zero_copy_one():
    from pipeline.config import Settings

    assert Settings.model_fields["knowledge_collection"].default == "knowledge_zc"


def test_config_store_default_follows_settings(monkeypatch):
    """default_active() must DERIVE the collection from settings, not repeat the
    literal. Asserting it equals "knowledge_zc" today would pass just as happily
    against a hardcoded copy, and a hardcoded copy is exactly what silently
    survives the next rename. Point settings at a sentinel and demand it follows.
    """
    from pipeline import config_store

    monkeypatch.setattr(config_store.settings, "knowledge_collection", "sentinel_zc")
    assert config_store.default_active()["active_collection"] == "sentinel_zc"


def _string_keys(path: pathlib.Path) -> set[str]:
    """Every literal dict key written anywhere in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out.add(k.value)
    return out


def test_write_path_modules_never_mention_a_text_key():
    # spanio.py stays out; its model-facing read result legitimately returns text.
    for rel in ("pipeline/activities/ingest.py", "pipeline/retrieval.py"):
        assert "text" not in _string_keys(ROOT / rel), rel


def test_agent_tools_test_was_retired():
    assert not (ROOT / "tests" / "test_agent_tools.py").exists()


def test_the_old_agent_package_is_gone():
    assert not (ROOT / "agent").exists()


def test_no_module_imports_the_old_agent_package():
    """Parse imports rather than grepping for them.

    This test is the durable guard against a dangling import of the deleted
    package, and a dangling import is what breaks the worker at startup rather
    than at call time. A substring check for "import agent\\n" misses exactly the
    shapes most likely to reappear: `import agent.tools`, `import agent as a`.
    Matching on the parsed module root also keeps `mongodb_agent_engine` from tripping
    it, which a looser pattern would.
    """
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "agent", f"{path}: import {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                root_pkg = (node.module or "").split(".")[0]
                assert root_pkg != "agent", f"{path}: from {node.module} import ..."


def test_openai_settings_are_gone():
    from pipeline.config import Settings

    leftovers = {"openai_api_key", "agent_model", "agent_max_turns",
                 "agent_api_port", "voyage_rerank_model"}
    assert not leftovers & set(Settings.model_fields)
