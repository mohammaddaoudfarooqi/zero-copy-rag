# Atlas Agent Engine entrypoint for the zero-copy RAG agent. Registers two tools
# and splits them across two sub-agents so retrieval and reading stay separate steps.

from __future__ import annotations

import json
import logging
from typing import Any

from deepagents import SubAgent
from agent_engine_sdk_langgraph import App
from dotenv import load_dotenv

from mongodb_agent_engine.llm import build_llm
from pipeline.retrieval import vector_search
from pipeline.spanio import read_span as _read_span

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()

app = App(app_name="zero-copy-rag")


# is_local=False puts both tools in the Tool Pod rather than in the agent
# runtime. That is the placement the platform documents for database reads and
# external APIs, and it is what lets agent.yaml grant MongoDB, Voyage and AWS
# credentials to the tool sandbox alone.
@app.tool(is_local=False)
def search_knowledge(query: str, k: int = 8) -> str:
    """Search the indexed corpus. Returns citations, not text.

    Each result is a pointer: chunk_id, source_uri, ordinal, span, metadata and
    score. To read the text behind a result, hand its chunk_id to read_span.

    Args:
        query: Natural-language search query.
        k: How many chunks to return. Keep it small; every hit costs a read later.
    """
    return json.dumps({"results": vector_search(query, k=k)})


@app.tool(is_local=False)
def read_span(chunk_id: str) -> str:
    """Read the exact source text behind one chunk_id.

    Takes only a chunk_id. The pointer is resolved server-side and the bytes are
    verified against the hash recorded at index time. A result whose status is
    not "ok" carries no text: say the source is unavailable rather than
    answering from the search result alone.

    Args:
        chunk_id: A chunk_id returned by search_knowledge, used verbatim.
    """
    return json.dumps(_read_span(chunk_id))


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

Work in two steps. First delegate to knowledge-retriever, which returns pointers
with no text. Then pick the two to four most promising chunk_ids and delegate
those to source-reader, which returns the text.

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
For each chunk_id you are given, call read_span once, with the chunk_id exactly
as you received it. Return the text exactly as it comes back. If status is not
"ok", report the status and the chunk_id and return no text for that chunk.
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
                tools=_tools_named("read_span"),
            ),
        ],
        system_prompt=SYSTEM_PROMPT,
    )


def main() -> None:
    """Platform entrypoint: start the runtime server."""
    app.run()


if __name__ == "__main__":
    main()
