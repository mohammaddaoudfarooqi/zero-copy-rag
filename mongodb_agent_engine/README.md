# mongodb_agent_engine

The query agent for zero-copy-rag, deployed on MongoDB Atlas Agent Engine. Two tools, split across
two sub-agents: `knowledge-retriever` searches and gets pointers with no text, and `source-reader`
resolves a pointer and gets the verified bytes. All retrieval, S3 and span-verification logic
lives in `pipeline/`; this package only registers tools, selects a model and assembles the graph.

Atlas Agent Engine is in Public Preview. MongoDB does not recommend production workloads on it
yet and expects breaking changes, so pin the CLI and recheck this file against the
[public docs](https://www.mongodb.com/docs/agentengine/) before upgrading.

## Workspace layout

The **repository root is the agent workspace**, not `mongodb_agent_engine/`. Both tools import
`pipeline.retrieval` and `pipeline.spanio` directly, so `pipeline/` has to be inside the built
image, and the workspace is whichever directory holds `agent.yaml`. Run `agentengine init`,
`agentengine build` and `agentengine deploy` from the repository root.

Files that make the root a workspace:

| File | Purpose |
| --- | --- |
| `agent.yaml` | Name, entrypoint, feature flags, sandbox secret grants, egress allow-list |
| `dev.yaml` | Local `agentengine dev up` settings only. Never read by build or deploy |
| `uv.toml` | A fixed `exclude-newer` date. The scaffold writes `"7 days"`, which older uv releases cannot parse |
| `pyproject.toml` | Adds the SDK names to the pipeline dependencies. The base images supply the real packages |
| `.agentengineignore` | Narrows the source archive `agentengine build` uploads |
| `.env` | The only runtime secret source locally. Host environment variables are ignored inside the container |

`agentengine build` uploads a source archive of the whole workspace, not a directory. With the
root as the workspace, that would ship the docs, the test suite and the seed corpus.
`.agentengineignore` denies every top-level entry (`/*`) and then names what ships: the agent,
`pipeline/`, `infra/` and the build inputs. `tests/test_packaging.py` fails if an edit lets
`docs/`, `seed/` or another withheld path back in, or drops a package the wheel needs.

The first line is `/*` rather than `*` on purpose. gitignore syntax cannot re-include a file
whose parent directory is excluded, so excluding only the top-level entries is what makes
`!/pipeline/` work.

## SDK surface

```python
from agent_engine_sdk_langgraph import App
from deepagents import SubAgent

app = App(app_name="zero-copy-rag")

@app.tool(is_local=False)
def search_knowledge(query: str, k: int = 16) -> str: ...

@app.entrypoint
def build_agent():
    return app.deep_agent(llm=..., tools=[], subagents=[...], system_prompt=...)
```

Details that are easy to get wrong:

- **Tools return `str`.** The platform hands the return value to the model as tool-message
  content and does not serialize a `dict` or `list` for you, so both tools return `json.dumps(...)`.
- **Tools are local by default.** `@app.tool()` puts a tool in the agent sandbox. Both tools pass
  `is_local=False` and are listed under `sandboxes.tool.tools` in `agent.yaml`, so they run in the
  tool sandbox, which holds the credentials. `tests/test_agent_wiring.py` asserts the
  `is_local=False` half, because a silent revert would move S3 and MongoDB credentials into the
  process running the model loop.
- **`SubAgent` is a TypedDict, not a class.** Its required keys are `name`, `description` and
  `system_prompt`, and its `tools` field takes tool objects, not names, which is why `app.py`
  resolves them from `app.get_tools()` through `_tools_named`.
- **`deep_agent` wraps the model itself.** Pass the raw `BaseChatModel`, not `app.llm(...)`:
  `deep_agent()` applies `SecureWrappedLLM` internally, and wrapping twice registers the model
  under one id twice. Leave `checkpointer` unset too; its default resolves to `app.checkpointer()`.
- **Pin `deepagents==0.5.3`.** The SDK imports it only when `App.deep_agent()` is called, and the
  public docs require that exact version.

### Differences from the public docs

| Item | Public docs | This repo |
| --- | --- | --- |
| LLM credential | Provider-specific keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...) | One `LLM_API_KEY`, with the provider chosen in `llm.py` |
| `features.durable_workflow` | Not in the documented `features` schema | Set to `true` in `agent.yaml`, carried over from the scaffold. Whether it changes anything is unverified |

`agent.yaml` uses the `sandboxes` block only. The SDK still parses the legacy `required_secrets`
shape, but enforcement is server side, from `sandboxes`, which is what
`agentengine agent validate` checks.

### The cost of `features.deep_agent`

`App.deep_agent()` requires `features.deep_agent: true`. The flag also registers the tool
sandbox's built-in filesystem and shell handlers, which are off by default so agents that do not
need them do not carry that surface. This agent uses none of them; it is surface accepted to get
sub-agent delegation. If deep agents misbehave on the platform, the fallback is to rebuild
`build_agent()` as a standard `StateGraph` plus `ToolNode` loop and keep the retrieve-then-read
discipline in the system prompt. That loses nothing the platform was enforcing, for the reason in
the next section.

## What the capability split does and does not enforce

This is the design's central claim, and it is easy to overstate.

**Enforced in this repo.** `read_spans` accepts chunk_ids and nothing else, forwards each one
positionally to `pipeline.spanio.read_span`, and reads at most sixteen per call, only ids a
search returned. No caller, model or otherwise, can supply a bucket, key, ETag, byte range or content
hash. `search_knowledge` wraps `pipeline.retrieval.vector_search`, whose projection has no `text`
field, so the retrieval path cannot return document text at all. `tests/test_agent_wiring.py` pins
both.

**Enforced by the platform.** Secrets are deny-by-default and granted per sandbox: `agent.yaml`
gives the agent sandbox nothing and grants the credentials to the tool sandbox only. Egress is
deny-all, and every destination is named. Both are server side and cannot be verified from the
client source.

**Not enforced anywhere.** The sub-agent boundary. Nothing in the SDK or `deepagents` treats a
sub-agent's `tools` list as a dispatch restriction, and the public limitations page states that
tools in one sandbox share all of its secrets and egress. Both tools share one tool sandbox, so a
subverted `knowledge-retriever` is held back by the fact that retrieval cannot return text, not by
the fact that it was not given the reader. The split keeps the agent's reasoning orderly; it is
not an isolation boundary, and real isolation would take two agents.

## Configuration

`agent.yaml` grants these to the tool sandbox: `MONGODB_URI`, `MONGODB_DB`, `VOYAGE_API_KEY`,
`LLM_API_KEY`, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. Everything else the pipeline reads
has a default in `pipeline/config.py`, including `AWS_REGION` (`us-east-1`) and
`KNOWLEDGE_COLLECTION` (`knowledge_zc`). `S3_BUCKET` is deliberately not granted: `read_span`
takes the bucket and key from the pointer document, never from configuration.

The corpus bucket is `s3://temporal-agentic` in `us-east-1`, and both its virtual-host name and
the regional endpoint are in the egress allow-list. A different bucket needs its host added first
(`docs/RUNBOOK.md` section 5).

Query embeddings go to MongoDB's hosted Voyage endpoint, `VOYAGE_BASE_URL`, default
`https://ai.mongodb.com/v1`. The voyageai SDK would otherwise pick the endpoint from the shape of
the key (a key beginning `al-` goes to `ai.mongodb.com`, anything else to `api.voyageai.com`), and
an egress allow-list should not depend on a credential prefix. `pipeline/clients.py` passes
`base_url` explicitly, `api.voyageai.com` is not allowed, and `tests/test_egress.py` holds the two
together.

### Model

Model selection lives in `llm.py`, driven by `LLM_PROVIDER` with an optional `LLM_MODEL`
override; the platform's convention keeps provider and model out of `agent.yaml`.
`LLM_PROVIDER` defaults to `anthropic` (`claude-sonnet-5`), with `openai` and `gemini` as the
other choices, and `LLM_API_KEY` holds the key for whichever is selected.

`LLM_SUBAGENT_MODEL` gives the two sub-agents a separate model; each gets its own LLM instance,
because Agent Engine rejects a model name on a `SubAgent` and wraps each sub-agent's model
separately. It is off by default. With `claude-haiku-4-5` for the sub-agents, the default question
took 237 s instead of 269 s, read the same 12 chunks, and came back with less detail: the reader
relays chunk texts to the parent and condensed them. Unset, the sub-agents use the parent's model.

To route `anthropic` or `openai` through a compatible gateway, set `LLM_BASE_URL` and, if the
gateway reads the key from its own header, `LLM_API_KEY_HEADER`. `llm.py` passes them as
`base_url` and `default_headers`. `api.anthropic.com` is the only model host in the egress
allow-list, so switching provider or adding a gateway needs that host added with
`agentengine agent egress add --component tool <host>:443` first. A hosted deploy through a
gateway also needs `LLM_BASE_URL` and `LLM_API_KEY_HEADER` listed under `sandboxes.tool.secrets`,
because `.env` never ships. `tests/test_egress.py` pins the default: the default provider's host
is allowed and the other providers' hosts are not.

### Credential resolution timing

`pipeline/config.py` builds its `settings` singleton at import. In the tool sandbox the tool
module can be imported before the sandbox's secrets reach the environment, which would freeze
blank credentials into a process-wide client. `pipeline/clients.py` therefore resolves credentials
on every call, preferring the configured value and falling back to the live environment when it
is blank, and keys each client cache on the resolved value. Local development and the tests are
unaffected, because there `settings` is always populated.

## Deploying

From the repository root:

1. `agentengine init`, then pick the org and project. It writes a `.agentengineignore` only if
   one is missing; check that it left this repo's alone.
2. Fill `.env`. It is git-ignored and stripped from the source archive unconditionally.
3. Set the deployed secrets, which are separate from `.env` and never read from it:
   `MONGODB_URI`, `MONGODB_DB`, `VOYAGE_API_KEY`, `LLM_API_KEY`, `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY`. `agentengine deploy` refuses to run until every populated `.env` key
   exists as a secret, so import them without putting values on the command line:
   `grep` the keys into `agentengine secret set --from-file -`, then `agentengine secret sync`.
   A secret existing is not the same as the tools seeing it: the tool sandbox sees only what
   `sandboxes.tool.secrets` grants.
4. Add the Agent Engine data-plane IP ranges to the Atlas IP access list with
   `agentengine atlas setup-ip-access`, and remove any `0.0.0.0/0` entry left from testing.
5. Confirm the deployed AWS credentials can read the bucket.
6. Run `agentengine dev up` and exercise both tools in the Playground.
7. `agentengine build`, then `agentengine deploy`, then `agentengine invoke "your question"`.

## Known pitfalls

- **The `us-east-1` global endpoint.** botocore resolves `us-east-1` alone to the global
  `s3.amazonaws.com`, so a bucket there is addressed as `<bucket>.s3.amazonaws.com`, which the
  allow-list does not name. The egress proxy answers `CONNECT` to that host with 403, and botocore
  reports only `Failed to connect to proxy URL`. `pipeline/clients.py` pins
  `us_east_1_regional_endpoint: regional`, and `tests/test_egress.py` asserts on the host boto3
  actually builds.
- **Grants the local stack hides.** Locally, `pipeline.config` reads `.env` from disk, so the tools
  see values that were never granted. Hosted, `.env` does not ship, and an ungranted
  `MONGODB_DB` would send queries to the default database and find nothing. It is granted, and
  `tests/test_packaging.py` pins the grant.
- **No source pin for the SDK.** The local dev image and the hosted builder install the SDK from
  wheels baked into their images. A git source in `[tool.uv.sources]` sent the hosted builder to a
  repository it could not clone, so `pyproject.toml` declares the names without one.
- **Reload during a request.** The dev stack hot-reloads on edits under `pipeline/` or
  `mongodb_agent_engine/`, and a request in flight fails with `connection refused`. That is the
  reload, not the agent.
- **Long runs.** A run is dominated by model calls: over three local runs the default question
  took a median of 309 s (281 to 313; `make eval-agent`). Prompts alone did not limit reads (one run read 34 chunks by
  constructing chunk ids), which is why `read_spans` caps each call and reads only searched ids.
  Give clients a long timeout.
