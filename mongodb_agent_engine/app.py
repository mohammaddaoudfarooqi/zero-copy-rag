# Atlas Agent Engine entrypoint for the zero-copy RAG agent. Registers two tools
# and splits them across two sub-agents so retrieval and reading stay separate steps.

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent_engine_sdk_langgraph import App
from deepagents import SubAgent
from dotenv import load_dotenv

from mongodb_agent_engine.llm import build_llm
from pipeline.retrieval import vector_search
from pipeline.spanio import read_span as _read_span

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()

app = App(app_name="zero-copy-rag")

# chunk_ids that search_knowledge has returned in this tool sandbox. The sandbox is
# reserved for one session and reset before the next, so this is per session.
# read_spans reads only these: chunk_ids are predictable (doc_id:ordinal), and
# without the check a model reads past the search results by constructing ids.
_FOUND: set[str] = set()


# is_local=False puts both tools in the Tool Pod rather than in the agent
# runtime. That is the placement the platform documents for database reads and
# external APIs, and it is what lets agent.yaml grant MongoDB, Voyage and AWS
# credentials to the tool sandbox alone.
@app.tool(is_local=False)
def search_knowledge(query: str, k: int = 12) -> str:
    """Search the indexed corpus. Returns citations, not text.

    Each result is a pointer: chunk_id, source_uri, ordinal, span, metadata and
    score. To read the text behind results, hand their chunk_ids to read_spans.

    Args:
        query: Natural-language search query.
        k: How many chunks to return. Keep it small; every hit costs a read later.
    """
    results = vector_search(query, k=k)
    _FOUND.update(hit["chunk_id"] for hit in results)
    return json.dumps({"results": results})


# Most chunks one read_spans call will read: one search's worth (k defaults to 12).
# A prompt alone did not hold the line: one run read 34 chunks for one question,
# each a separate tool round trip. Reads in one call run concurrently.
READ_LIMIT = 12


@app.tool(is_local=False)
def read_spans(chunk_ids: list[str]) -> str:
    """Read the exact source text behind up to twelve chunk_ids, in one call.

    Takes only chunk_ids that search_knowledge returned; any other id comes back
    with status "not_found_by_search" and is not read. Each pointer is resolved
    server-side and its bytes are verified against the hash recorded at index
    time. A result whose status is not "ok" carries no text: say that source is
    unavailable rather than answering from the search result alone. chunk_ids
    beyond the first twelve come back with status "skipped" and are not read.

    Args:
        chunk_ids: chunk_ids returned by search_knowledge, used verbatim. Pass
            the most promising ones first.
    """
    if isinstance(chunk_ids, str):
        chunk_ids = [chunk_ids]
    ordered = list(dict.fromkeys(chunk_ids))
    unfound = {
        c: {"chunk_id": c, "status": "not_found_by_search",
            "reason": "read only chunk_ids that search_knowledge returned"}
        for c in ordered if c not in _FOUND
    }
    found = [c for c in ordered if c in _FOUND]
    to_read, over = found[:READ_LIMIT], found[READ_LIMIT:]
    with ThreadPoolExecutor(max_workers=max(1, len(to_read))) as pool:
        # Positional chunk_id only: the shim never supplies a collection or client.
        read = dict(zip(to_read, pool.map(_read_span, to_read), strict=True))
    skipped = {
        c: {"chunk_id": c, "status": "skipped",
            "reason": f"over the limit of {READ_LIMIT} reads per call"}
        for c in over
    }
    every = {**unfound, **skipped, **read}
    return json.dumps({"results": [every[c] for c in ordered]})


def _tools_named(*names: str) -> list[Any]:
    """Select registered tools by name, in the order given.

    deepagents' SubAgent takes tool objects, not tool names, so a sub-agent's
    tool list has to be resolved from the app's registry rather than written as
    strings. Raise on a miss: a typo would silently produce a sub-agent with no
    tools that then hallucinates its answers.
    """
    by_name = {tool.name: tool for tool in app.get_tools()}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise RuntimeError(
            f"no registered tool named {missing}; registered: {sorted(by_name)}"
        )
    return [by_name[name] for name in names]


SYSTEM_PROMPT = """\
You answer questions about the indexed corpus, and only from what that corpus says.

Work in two steps, each delegated once. First ask knowledge-retriever for
pointers, which carry no text. Then send the most promising of the chunk_ids it
returned, at most twelve, to source-reader in a single task; it reads them all
in one call, and it cannot read a chunk_id the search did not return. Delegate a
second time only if every read in the first batch came back unavailable. Answer
from what those reads returned, and say what the sources did not cover rather
than searching again: every extra delegation costs several model calls.

Quote only text that source-reader returned with status "ok". If a read comes
back stale, missing or unknown, say the source is unavailable and name its
source_uri. Never reconstruct or paraphrase a span you could not read, and never
answer from a search result alone: search results carry no text, so anything you
write from one is invention.

Cite the source_uri and chunk_id behind every claim.
"""

RETRIEVER_PROMPT = """\
Call search_knowledge and return its results verbatim. You have no tool that can
read document text, so you must not produce any: report the pointers and stop.
"""

READER_PROMPT = """\
Call read_spans exactly once, passing every chunk_id you were given exactly as
you received it. Return each text exactly as it comes back. For a result whose
status is not "ok", report the status and the chunk_id and return no text for it.
"""


@app.entrypoint
def build_agent():
    """Build the deep agent graph.

    The checkpointer is intentionally not passed: deep_agent's default sentinel
    resolves to app.checkpointer(), and passing it explicitly only risks
    diverging from that if the default changes.
    """
    logger.info("Building zero-copy-rag deep agent")
    return app.deep_agent(
        # Raw model, not app.llm(...): deep_agent wraps it in SecureWrappedLLM.
        llm=build_llm(),
        # The parent only delegates. Giving it the tools directly would let it
        # skip the retrieve-then-read sequence the system prompt depends on.
        tools=[],
        subagents=[
            SubAgent(
                name="knowledge-retriever",
                description="Finds relevant chunks. Returns pointers only; cannot read content.",
                system_prompt=RETRIEVER_PROMPT,
                tools=_tools_named("search_knowledge"),
            ),
            SubAgent(
                name="source-reader",
                description="Reads the source text behind specific chunk_ids.",
                system_prompt=READER_PROMPT,
                tools=_tools_named("read_spans"),
            ),
        ],
        system_prompt=SYSTEM_PROMPT,
    )


def main() -> None:
    """Platform entrypoint: start the runtime server."""
    app.run()


if __name__ == "__main__":
    main()
