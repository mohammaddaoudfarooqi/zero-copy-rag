# Runbook: Developer Setup Guide

Everything a new developer needs to spin up this repo locally and understand the production
cloud setup.

Everything runs on your machine except storage. The Temporal dev server, the worker, the
trigger API and the Agent Engine Playground are local processes; the documents live in a real
S3 bucket and the index lives in Atlas. The bucket has to be real because the agent's
`read_span` tool reads it from behind an egress proxy that only admits the hosts named in
`agent.yaml` (section 5).

The short version, once sections 1 to 5 are done:

```bash
make demo FILE=./seed/ethical_governance.md
```

That is `make start`, `make index`, `make seed` and `make playground` in sequence, and it ends
with the Playground on [http://localhost:3000](http://localhost:3000). Section 6 walks through
the same steps one at a time.

---

## Table of contents

- [Prerequisites](#prerequisites)
- [1. MongoDB Atlas setup](#1-mongodb-atlas-setup)
- [2. Voyage AI API key](#2-voyage-ai-api-key)
- [3. Environment configuration](#3-environment-configuration)
- [4. Local setup (make setup)](#4-local-setup-make-setup)
- [5. S3 bucket](#5-s3-bucket)
- [6. Run the full stack](#6-run-the-full-stack)
- [7. Verify ingestion](#7-verify-ingestion)
- [8. Backfill + model cutover](#8-backfill--model-cutover)
- [9. Production trigger (AWS Lambda)](#9-production-trigger-aws-lambda)
- [10. Query plane (hosted deep agent)](#10-query-plane-hosted-deep-agent)
- [Cloud infra references](#cloud-infra-references)

---

## Prerequisites

Install these tools before running anything:

| Tool               | Version | Install                                                                                                              |
| ------------------ | ------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **uv**             | latest  | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (docs: [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)) |
| **Docker Desktop** | >= 4.x  | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/), for the local Playground         |
| **Temporal CLI**   | latest  | `brew install temporal` or [docs.temporal.io/cli](https://docs.temporal.io/cli)                                      |
| **agentengine**    | latest  | The Atlas Agent Engine CLI, for the local Playground and for deploying (section 10)                                  |
| **mongosh**        | latest  | `brew install mongosh`, optional, handy for inspecting Atlas collections                                             |

Verify:

```bash
uv --version
docker --version
temporal --version
agentengine --version
```

---

## 1. MongoDB Atlas setup

### Create a free cluster

1. Sign up or log in at [cloud.mongodb.com](https://cloud.mongodb.com).
2. Create a new **M0 free cluster** (or any tier) in a region close to you.
3. Docs: [Create a Cluster](https://www.mongodb.com/docs/atlas/tutorial/create-new-cluster/)

### Create a database user

1. In the Atlas UI: **Security -> Database Access -> Add New Database User**.
2. Choose **Password** auth. Note the username and password.
3. Grant **Atlas Admin** role (or at minimum `readWriteAnyDatabase`).
4. Docs: [Configure Database Users](https://www.mongodb.com/docs/atlas/security-add-mongodb-users/)

### Allow network access

The worker, the trigger API and the local Playground's Tool Pod all reach Atlas from your
machine's public IP. You need to allow:

- Your **local machine IP**, or **0.0.0.0/0** temporarily while testing.
- The **Atlas Agent Engine data-plane ranges**, if you deploy the query agent (section 10).
  `agentengine atlas setup-ip-access` adds them. Once they are in, remove any `0.0.0.0/0` entry.

Steps: **Security -> Network Access -> Add IP Address**.

Docs: [Configure IP Access List](https://www.mongodb.com/docs/atlas/security/ip-access-list/)

### Get the connection string

1. **Database -> Connect -> Drivers** and copy the `mongodb+srv://` URI.
2. Replace `<username>` and `<password>` with the credentials you created above.

The URI will look like:

```
mongodb+srv://myuser:mypass@mycluster.abc12.mongodb.net/?retryWrites=true&w=majority
```

---

## 2. Voyage AI API key

Voyage AI is available directly through **MongoDB Atlas Models**, no separate Voyage AI account
required.

1. In the Atlas UI go to **Services -> Atlas Models** (or search "Models" in the left nav).
2. Select **Voyage AI** from the provider list and click **Generate API Key**.
3. Copy the key, it will only be shown once.
4. The default model used is `voyage-3.5` (1024 dimensions).
5. Docs: [Atlas Models: Voyage AI](https://www.mongodb.com/docs/atlas/ai-integrations/)

---

## 3. Environment configuration

```bash
cp .env.example .env
```

Open `.env` and fill in the required values:

```bash
# REQUIRED, fill these in
MONGODB_URI=mongodb+srv://<user>:<pass>@<cluster>.mongodb.net/?retryWrites=true&w=majority
VOYAGE_API_KEY=<your-voyage-api-key>
```

Then the S3 credentials (section 5 covers the bucket itself):

```bash
AWS_REGION=us-east-1
S3_BUCKET=temporal-agentic
AWS_ACCESS_KEY_ID=<your-aws-access-key-id>
AWS_SECRET_ACCESS_KEY=<your-aws-secret-access-key>
```

Leave both keys blank to fall back to boto3's standard credential chain (a profile or a role).

For the Playground, also set `LLM_API_KEY`. With the default `LLM_PROVIDER=grove-anthropic`
it holds the Grove gateway key. The pipeline does not read it; only the agent does. The
searchable collection defaults to `knowledge_zc` (`pipeline/config.py`,
`knowledge_collection`).

One thing that only bites in `us-east-1`. botocore resolves that region alone to the global
`s3.amazonaws.com` rather than the regional host, which the hosted agent's egress allow-list
does not name. `pipeline/clients.py` pins `us_east_1_regional_endpoint: regional` to prevent it,
and `tests/test_egress.py` asserts on the host boto3 actually builds. Nothing to configure, but
it explains why the allow-list in `agent.yaml` names `s3.us-east-1.amazonaws.com` and not the
global host.

The remaining defaults work as-is (Temporal on `localhost:7233`).

---

## 4. Local setup (make setup)

Install Python dependencies:

```bash
make setup
```

This checks for `.env` and runs `make install`, which runs `uv sync` to install Python 3.12 and
every dependency from `pyproject.toml` except the Agent Engine SDK. The SDK's PyPI names are
placeholders; the real packages come from the Agent Engine base images, so `make install` passes
`--no-install-package` for each of them. There is no frontend to install: the query plane is a
separate deployment (`mongodb_agent_engine/`, see
[section 10](#10-query-plane-hosted-deep-agent)), not a process `make start` runs.

Run the test suite with:

```bash
uv run --no-sync pytest
```

`--no-sync` matters: a plain `uv run` re-syncs the environment and tries to install the SDK
placeholders. The suite needs no network, Atlas or AWS access.

---

## 5. S3 bucket

The documents stay in S3. `knowledge_zc` holds a pointer into each object plus its embedding,
and the agent's `read_span` tool fetches the bytes at query time, so the worker that indexes
the bucket and the Tool Pod that reads it must see the same bucket.

The Tool Pod's egress is deny-all. Locally and hosted alike, its proxy admits only the hosts
`agent.yaml` names, and for S3 those are `s3.us-east-1.amazonaws.com` and
`temporal-agentic.s3.us-east-1.amazonaws.com`. So:

- **Use `temporal-agentic` in `us-east-1`**, which is what `.env.example` sets, and every host
  is already allowed.
- **Or use your own bucket** and add its virtual-host name first:
  `agentengine agent egress add <bucket>.s3.<region>.amazonaws.com:443`. Until you do, every
  `read_span` fails and the agent reports each source as unavailable. `tests/test_egress.py`
  pins the allow-listed bucket, so update `ALLOW_LISTED_BUCKET` there as well.

The credentials in `.env` need `s3:PutObject` (for `make seed`) and `s3:GetObject` (for the
worker and `read_span`) on the bucket.

Nothing watches the bucket locally. `make seed` uploads the object and starts its
`IngestWorkflow` in the same step (section 6). In production an S3 event notification does the
starting instead (section 9).

---

## 6. Run the full stack

`make start` starts all services in the background:

```bash
make start
```

Starts (in order):

1. Temporal dev server (`:7233`, Web UI `:8233`), or an already-running one on `:7233`
2. Temporal worker (`pipeline/worker.py`)

The worker is what runs ingestion. `make seed` talks to Temporal directly.

`NO_WORKER=1 make start` leaves the worker out so you can run `make worker` in a foreground
terminal. Killing it mid-ingest and starting it again is the quickest way to watch Temporal
resume a workflow from its last completed activity.

The agent is not one of these processes. `make playground` starts it separately (below).

Logs go to `.local/*.log`. Tail them:

```bash
make app-logs
```

### Create the Atlas Vector Search index (one-time)

```bash
make index
```

This creates the `temporalai_search_index` vector search index on the active collection
(`knowledge_zc` by default) in Atlas. The workflow also creates it automatically on first
ingest, but running `make index` upfront avoids a delay on the first document.

Docs: [Atlas Vector Search](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/)

### Seed a document

```bash
make seed                                          # uploads a short built-in sample
make seed FILE=./seed/ethical_governance.md        # upload a real document
make seed FILE=./my-doc.md KEY=docs/my-doc.md      # choose the S3 key
```

`make seed` uploads the file to `S3_BUCKET` and then starts its `IngestWorkflow`, printing the
workflow id. `make seed-docs` does the same for every `.md` and `.mdx` file in the Temporal docs
repository. With `NO_TRIGGER=1` either one only uploads, which is what you want when an S3 event
notification is wired to the bucket and will start the workflow itself.

Every path starts the same workflow, whose id is derived from the S3 URI. Starting the same key
twice replaces any in-flight ingest rather than duplicating it, and an unchanged document
short-circuits on its content hash.

Markdown is the only supported format (`.md` / `.markdown`, or a `text/markdown` content type).
Any other file yields zero chunks and the workflow clears the document rather than indexing it
(see `docs/LLD.md` section 7).

Watch the workflow run in the Temporal Web UI at [http://localhost:8233](http://localhost:8233).

### Ask the agent

```bash
make playground
```

This runs `agentengine dev up` from the repository root, which builds and starts the agent's
local stack in Docker and serves the Playground on [http://localhost:3000](http://localhost:3000).
Ask it about the document you seeded. Its tools query the same Atlas collection and read the
same bucket the worker just indexed. Section 10 covers what the stack runs and how to post to it
without the Playground.

### Stop everything

```bash
make stop           # stops app processes + Temporal
make stop-app       # stops app processes only (leaves Temporal running)
make restart-app    # restart app processes after .env changes
```

`make stop` does not touch the Playground. It runs in the foreground, so stop it with Ctrl-C in
its terminal, or `agentengine dev stop` from another.

---

## 7. Verify ingestion

```bash
make seed FILE=./my-doc.md KEY=docs/my-doc.md
# watch Temporal UI: IngestWorkflow -> Completed
make query Q="something in that file"
```

`make query` runs `infra/query_atlas.py`, which is the pipeline in miniature and the fastest way
to tell retrieval and reading apart. It calls `pipeline.retrieval.vector_search`, which returns
pointers only (`chunk_id`, `ordinal`, `source_uri`, `span`, `metadata`, `score`) and no text,
then resolves each `chunk_id` through `pipeline.spanio.read_span` to get the snippet it prints.
A hit whose read comes back `stale`, `missing` or `unknown` prints its status instead of text,
which is the same contract the agent works under.

In Atlas, confirm:

- `temporal.knowledge_zc` has pointer + embedding documents (no `text` field).

`tests/test_no_text_invariant.py` and `tests/test_index_pointers.py` pin that invariant, so a
`text` field appearing in that collection means something bypassed the write path.

---

## 8. Backfill + model cutover

Use this when upgrading the embedding model (e.g. `voyage-3.5` to a newer model with different
dimensions).

**Current status: the re-embed step is deferred and does not run end to end.** This is a
design decision, not an unfinished edge: zero-copy means `knowledge_zc` holds pointers and
embeddings but no text, so there is nothing local to re-embed from. `reembed_and_write` raises a
non-retryable `ApplicationError` (type `BackfillDeferred`) and `make backfill` fails on the
first attempt rather than retrying a deferral six times.

**Re-ingest instead.** An embedding-model change is handled by pointing `EMBED_MODEL` at the new
model, recreating the index at the new dimension, and re-running ingestion from S3, which is the
authoritative copy. That path works today and is what the deferral assumes you will do.

The two commands below are kept as the intended shape of a blue/green swap, not a verified one:

```bash
# Deferred: starts a BackfillWorkflow that fails fast on the first activity.
make backfill MODEL=voyage-3-large

# The pointer flip itself is independent of backfill and does work, but only
# against a target collection something else has already populated.
make cutover TO=knowledge_v2
```

Retrieval reads the `temporal_config` collection to know which collection is active, so a
cutover needs no restart. Do not run `make cutover` against an empty `knowledge_v2`: it will
succeed and point every query at a collection with nothing in it.

---

## 9. Production trigger (AWS Lambda)

In production the trigger is an **AWS Lambda** subscribed to the S3 bucket's **ObjectCreated**
event notifications. The Lambda calls `pipeline.lambda_handler`, which runs the **same**
`handle_s3_event` code in `pipeline/trigger.py`: parse the event, start one
`IngestWorkflow` per object. No Kafka, `sources` collection, or Stream Processing is involved.
With the notification in place, seed with `NO_TRIGGER=1` so the upload is the only trigger.

**Deploy notes:**

- Handler entrypoint: `pipeline.lambda_handler.lambda_handler`; package `pipeline/` with the
  `temporalio` dependency.
- Give the Lambda network egress to your Temporal service and set `TEMPORAL_ADDRESS` /
  `TEMPORAL_NAMESPACE` (plus mTLS cert paths for Temporal Cloud) via environment.
- Wire the bucket's `s3:ObjectCreated:*` notifications to the Lambda (S3 console or IaC).

---

## 10. Query plane (hosted deep agent)

The query plane is a separate deployment from everything above: a deep agent on MongoDB Atlas
Agent Engine, defined in `mongodb_agent_engine/` and `agent.yaml`. It is not started by
`make start`. The `agentengine` CLI runs it locally (`agentengine dev up`, which `make playground`
wraps) and deploys it
(`agentengine build`, `agentengine deploy`), always from the repository root, which is the agent
workspace.

`mongodb_agent_engine/app.py` registers two tools on top of the same `pipeline/` code the ingestion
side uses:

- `search_knowledge` wraps `pipeline.retrieval.vector_search` and returns pointers only.
- `read_span` wraps `pipeline.spanio.read_span` and takes only a `chunk_id`.

These are split across two sub-agents, `knowledge-retriever` (search only) and `source-reader`
(read only). `mongodb_agent_engine/README.md` is the authoritative status of this deployment shim and
should be read in full before touching it.

### Running it locally

`agentengine dev up` brings up the three processes the platform runs: the Orchestration Engine, the
Agent Execution Runtime, and the Tool Pod. The OE's port is assigned at start-up, so read it
from `docker port` rather than assuming one:

```bash
docker port $(docker ps --filter name=oe -q) 8000
```

Then either use the Playground, or post a question straight at the OE:

```bash
curl -X POST http://localhost:<oe-port>/invoke \
  -H 'Content-Type: application/json' \
  -d '{"message": "your question here"}'
```

A full retrieve-then-read run takes tens of seconds, because the agent reads several spans
before answering. Give curl a generous `-m` or it will time out on a run that is working.

Two things to know when a run misbehaves:

- **The dev stack hot-reloads.** Editing anything under `pipeline/` or `mongodb_agent_engine/` restarts
  the sandboxes, and a request in flight fails with `connection refused` on the runtime's port.
  That is the reload, not the agent.
- **Secrets live in the tool process, not the container.** They are sourced from
  `.agentengine/tool-boot.env` into the tool sandbox, so `docker exec` into the container will not
  see them, and in dev mode each sandbox has its own venv under `/tmp/agentic-venvs/`. `/app` is
  the bind-mounted host repo, so the host's `.venv` symlinks are broken inside the container.

### Deploying it

The full first-deploy checklist is in `mongodb_agent_engine/README.md`. In short, from the
repository root:

1. `agentengine init` binds the repo to an org, project and workspace.
2. Set the deployed secrets. They are separate from `.env` and never read from it. To import
   keys without putting values on the command line, pipe them in:
   `grep -E '^(MONGODB_URI|MONGODB_DB|VOYAGE_API_KEY|LLM_API_KEY|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY)=' .env | agentengine secret set --from-file -`
3. Add the data-plane IP ranges to the Atlas access list (`agentengine atlas setup-ip-access`).
4. `agentengine build`, then `agentengine deploy`.
5. `agentengine invoke "your question here"` sends a question to the deployed agent.

### Where the boundary actually is

`read_span` takes only a `chunk_id`; the pointer is resolved server-side and the bytes are
verified against the hash recorded at index time. That is a real Python-level guarantee: no
caller, model or otherwise, can name an arbitrary bucket, key or byte range. The wiring contract
is pinned by `tests/test_agent_wiring.py`.

The sub-agent split is weaker, and worth being honest about. The parent agent is built with
`tools=[]` so it has to delegate, and the observed runs do retrieve before they read. But
nothing in this codebase enforces which tool a sub-agent may call: both sub-agents run in the
same Tool Pod under one set of credentials. The split is a declared contract that the platform
honours, not one this repo can enforce.

The capability boundary that does bite is egress. `agent.yaml` is deny-all plus four named
FQDNs, and the Tool Pod's proxy answers CONNECT to anything else with 403. `tests/test_egress.py`
guards it from the code side.

---

## Cloud infra references

When moving from local dev to real cloud infrastructure, use these references:

### MongoDB Atlas

| Topic                   | Documentation                                                                                                                |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Create a cluster        | [mongodb.com/docs/atlas/tutorial/create-new-cluster](https://www.mongodb.com/docs/atlas/tutorial/create-new-cluster/)        |
| Database users          | [mongodb.com/docs/atlas/security-add-mongodb-users](https://www.mongodb.com/docs/atlas/security-add-mongodb-users/)          |
| Network access          | [mongodb.com/docs/atlas/security/ip-access-list](https://www.mongodb.com/docs/atlas/security/ip-access-list/)                |
| Atlas Vector Search     | [mongodb.com/docs/atlas/atlas-vector-search](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/) |

### AWS S3 (production)

The same bucket settings as section 3. Wiring `s3:ObjectCreated:*` to the Lambda in section 9
is what makes ingestion automatic. Until that exists, `make seed` starts each ingest itself
(section 6).

| Topic                                   | Documentation                                                                                                               |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| S3 Getting Started                      | [docs.aws.amazon.com/s3/getting-started](https://docs.aws.amazon.com/AmazonS3/latest/userguide/GetStartedWithS3.html)       |
| S3 Event Notifications (-> Lambda)       | [docs.aws.amazon.com/s3/event-notifications](https://docs.aws.amazon.com/AmazonS3/latest/userguide/EventNotifications.html) |

### Temporal (production)

| Option                | Documentation                                                                    |
| ---------------------- | --------------------------------------------------------------------------------- |
| Temporal Cloud        | [temporal.io/cloud](https://temporal.io/cloud)                                   |
| Self-hosted with Helm | [docs.temporal.io/self-hosted-guide](https://docs.temporal.io/self-hosted-guide) |

Update `TEMPORAL_ADDRESS` in `.env` to the Temporal Cloud endpoint. Add mTLS cert paths for
Temporal Cloud connections.

### Voyage AI (via MongoDB Atlas Models)

| Topic                         | Documentation                                                                                                                                             |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Atlas Models overview         | [mongodb.com/docs/atlas/ai-integrations](https://www.mongodb.com/docs/atlas/ai-integrations/)                                                             |
| Voyage AI embeddings on Atlas | [mongodb.com/docs/atlas/atlas-vector-search/ai-integrations/voyage-ai](https://www.mongodb.com/docs/atlas/atlas-vector-search/ai-integrations/voyage-ai/) |
| Available embedding models    | [mongodb.com/docs/atlas/ai-integrations/voyage-ai/models](https://www.mongodb.com/docs/atlas/ai-integrations/)                                            |
