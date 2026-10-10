# 2. Run ingestion on Temporal and the query agent on Atlas Agent Engine

- Status: Accepted
- Date: 2026-10-11

## Context

Two loops need different guarantees. Ingestion fetches an object, stages hundreds of span
pointers, embeds them in batches and writes the index; a crash halfway must not lose or repeat
work. Question answering runs a model that calls tools holding database, embedding and AWS
credentials; it needs those credentials kept away from the model, a narrow egress policy, and a
place for people to ask questions.

## Decision

- **Ingestion runs on Temporal.** An S3 ObjectCreated event starts `IngestWorkflow` directly,
  through the AWS Lambda in production or `make seed` locally, with no broker in between. Each
  embed batch is an activity, so a crashed worker resumes at the first unfinished batch.
- **The query agent runs on Atlas Agent Engine.** A deep agent with two sub-agents: one searches
  and gets pointers, the other is the only one given `read_span`. Both tools run in the tool
  sandbox, which alone holds the credentials, with egress limited to named hosts.
- The two planes never call each other. They share the `knowledge_zc` collection and the bucket,
  and the ETag and hash in each pointer keep them consistent.

## Consequences

- Each plane can be deployed, scaled and replaced on its own.
- Nothing tells the agent that an ingest is in progress, so a question asked mid-ingest can see a
  partly indexed document.
- The agent inherits Agent Engine's Public Preview status: no SLAs, and limits such as a fixed
  sandbox pool per deployment.
- Both tools share one tool sandbox, and Agent Engine does not isolate tools within a sandbox, so
  the search tool can reach the AWS credentials too. Real isolation would take two agents.

## Alternatives considered

- **Run the agent on Temporal as well**, with Temporal's Deep Agents or LangGraph integration.
  Stronger durability for the agent loop, but the sandboxing, per-sandbox secrets, egress policy
  and Playground would have to be built and run separately.
- **Run ingestion inside Agent Engine.** Sessions are reserved per invocation and reclaimed after
  an idle timeout, which does not fit a long batch job that must survive crashes.
