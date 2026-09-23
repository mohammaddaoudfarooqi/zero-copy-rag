# Tests the agent shim's contract: the retriever never returns text, the reader
# is the only text path, and read_span takes no caller-supplied pointers.

from __future__ import annotations

import json
import sys
import types

import pytest


@pytest.fixture
def agent_module(monkeypatch):
    """Import mongodb_agent_engine.app against a stub SDK so the test needs no platform install.

    The stub mirrors the surface mongodb_agent_engine/app.py actually uses:
    ``App(app_name=...)``, ``@app.tool(is_local=...)`` returning the original
    function, ``@app.entrypoint``, ``app.get_tools()`` returning objects with a
    ``.name``, and ``app.deep_agent(...)``. ``deepagents.SubAgent`` is a
    TypedDict upstream, so the stub is a plain dict factory rather than a class.
    """
    registered: dict[str, object] = {}

    class _Tool:
        """Stands in for the LangChain tool object app.get_tools() hands back."""

        def __init__(self, fn):
            self.name = fn.__name__
            self.fn = fn

    class _App:
        def __init__(self, *a, **k):
            self.kwargs = k
            self.deep_agent_kwargs: dict = {}
            self.builder = None

        def tool(self, *a, **k):
            def deco(fn):
                registered[fn.__name__] = fn
                # The real decorator registers a wrapped tool and returns the
                # undecorated function. Match that, or the module-level names
                # would not be callable here.
                return fn

            return deco

        def entrypoint(self, fn):
            self.builder = fn
            return fn

        def get_tools(self):
            return [_Tool(fn) for fn in registered.values()]

        def get_tool_schemas(self):
            return self.get_tools()

        def checkpointer(self):
            return None

        def deep_agent(self, **k):
            self.deep_agent_kwargs = k
            return self

        def run(self):  # pragma: no cover - never called in tests
            raise AssertionError("app.run() must not run during import")

    sdk = types.ModuleType("agent_engine_sdk_langgraph")
    sdk.App = _App
    monkeypatch.setitem(sys.modules, "agent_engine_sdk_langgraph", sdk)

    deep = types.ModuleType("deepagents")
    deep.SubAgent = lambda **k: dict(k)
    monkeypatch.setitem(sys.modules, "deepagents", deep)

    sys.modules.pop("mongodb_agent_engine.app", None)

    import mongodb_agent_engine.app as mod

    mod._REGISTERED = registered
    yield mod
    sys.modules.pop("mongodb_agent_engine.app", None)


@pytest.fixture
def built_agent(agent_module, monkeypatch):
    """Run the @app.entrypoint builder with a stubbed LLM and return deep_agent's kwargs."""
    monkeypatch.setattr(agent_module, "build_llm", lambda temperature=0: object())
    agent_module.build_agent()
    return agent_module.app.deep_agent_kwargs


def test_both_tools_are_registered(agent_module):
    assert set(agent_module._REGISTERED) == {"search_knowledge", "read_span"}


def test_tools_run_in_the_tool_pod(agent_module):
    """is_local=False is what puts the credentials in the tool sandbox instead of the AER.

    The SDK's default is is_local=True, so this has to be passed explicitly and a
    silent revert to the default would move S3 and Mongo credentials into the
    process that runs the model loop.
    """
    import inspect

    src = inspect.getsource(agent_module)
    assert src.count("@app.tool(is_local=False)") == 2
    assert "@app.tool()" not in src


def test_search_returns_pointers_only(agent_module, monkeypatch):
    monkeypatch.setattr(agent_module, "vector_search", lambda q, k=5: [
        {"chunk_id": "d:0", "ordinal": 0, "source_uri": "s3://b/k.md",
         "span": {"kind": "byte", "start": 0, "end": 80}, "metadata": {}, "score": 0.9}
    ])
    out = json.loads(agent_module.search_knowledge("q"))
    assert all("text" not in hit for hit in out["results"])


def test_tools_return_json_strings(agent_module, monkeypatch):
    """The platform requires a string return; a dict would reach the model unserialized."""
    monkeypatch.setattr(agent_module, "vector_search", lambda q, k=5: [])
    monkeypatch.setattr(agent_module, "_read_span", lambda c: {"chunk_id": c, "status": "unknown"})

    searched = agent_module.search_knowledge("q")
    read = agent_module.read_span("d:0")
    assert isinstance(searched, str) and isinstance(read, str)
    assert json.loads(searched) == {"results": []}
    assert json.loads(read) == {"chunk_id": "d:0", "status": "unknown"}


def test_read_tool_takes_only_a_chunk_id(agent_module):
    import inspect

    assert list(inspect.signature(agent_module.read_span).parameters) == ["chunk_id"]


def test_subagents_are_split_by_capability(built_agent):
    subagents = {s["name"]: s for s in built_agent["subagents"]}
    assert set(subagents) == {"knowledge-retriever", "source-reader"}
    assert [t.name for t in subagents["knowledge-retriever"]["tools"]] == ["search_knowledge"]
    assert [t.name for t in subagents["source-reader"]["tools"]] == ["read_span"]


def test_parent_agent_holds_no_tools_of_its_own(built_agent):
    """The parent delegates. Tools on the parent would let it answer without reading."""
    assert built_agent["tools"] == []


def test_deep_agent_gets_an_unwrapped_llm_and_no_explicit_checkpointer(built_agent):
    """deep_agent wraps the model itself and defaults the checkpointer to app.checkpointer().

    Passing app.llm(...) here would double-register the model, and passing the
    checkpointer explicitly would pin us to today's default.
    """
    assert "llm" in built_agent
    assert "checkpointer" not in built_agent


def test_subagent_tool_lookup_rejects_an_unknown_name(agent_module):
    """A typo must fail loudly: a sub-agent with an empty tool list just hallucinates."""
    with pytest.raises(RuntimeError, match="no registered tool"):
        agent_module._tools_named("search_knowlege")


def test_read_tool_forwards_the_chunk_id_and_injects_nothing(agent_module, monkeypatch):
    """The reader tool's whole security surface is one call. Pin it behaviourally.

    test_read_tool_takes_only_a_chunk_id proves the model cannot PASS a pointer.
    This proves the shim does not SUPPLY one on the model's behalf: pipeline.spanio
    .read_span accepts keyword-only `collection` and `client` for test injection, and
    a shim that forwarded either would silently pin the tool to something other than
    the live, hash-verified default wiring. Assert the call is positional-only and
    that the result passes through untouched.
    """
    seen: dict = {}
    sentinel = {"chunk_id": "d:0", "status": "ok", "text": "hello"}

    def _spy(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return sentinel

    monkeypatch.setattr(agent_module, "_read_span", _spy)
    out = json.loads(agent_module.read_span("d:0"))

    assert seen["args"] == ("d:0",)
    assert seen["kwargs"] == {}
    assert out == sentinel
