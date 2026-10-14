# Novel LLM Architecture

## Current Position

Novel LLM is a local, single-author workbench with an architecture-first runtime: durable workflows, queueable jobs, review gates, versioned story data, and deterministic recovery are built before generation-quality features. The interface defaults to Simplified Chinese.

## System Shape

```text
Next.js workbench
  REST /api/v1 + durable SSE event stream
FastAPI API
  domain actions, uploads, exports, job control, review
Python domain package
  domain rules | infrastructure | workflow definitions | typed contracts
Worker
  queue claiming | project lock | LangGraph execution | heartbeat/fencing
PostgreSQL 16
  story data | workflow state | events | traces | zhparser FTS | pgvector
Local content-addressed storage
  uploads | exports | snapshots | large artifacts
```

Only the web interface binds to `127.0.0.1`. API, Worker, PostgreSQL, and storage stay on the trusted Docker network. There is no registration, login, account, sharing, public ingress, or LAN listener.

## Decision Records

- [0001 Single-author architecture-first](./adr/0001-single-author-architecture-first.md)
- [0002 PostgreSQL as authoritative state](./adr/0002-postgres-authoritative-local-artifacts.md)
- [0003 Durable two-layer workflow](./adr/0003-durable-two-layer-workflow.md)
- [0004 Canonical versions and memory gating](./adr/0004-canonical-versions-and-memory-gating.md)
- [0005 Local single-author workbench](./adr/0005-local-single-author-workbench.md)
- [0006 Plaintext local credentials](./adr/0006-local-plaintext-credentials.md)
- [0007 PostgreSQL Chinese full-text search](./adr/0007-postgresql-chinese-full-text-search.md)
- [0008 Typed versioned contracts](./adr/0008-typed-versioned-contracts.md)
- [0009 No physical foreign keys](./adr/0009-no-physical-foreign-keys.md)

Terminology is maintained in [../GLOSSARY.md](../GLOSSARY.md). ADRs record hard-to-reverse choices; this document records the current integrated view.

## Runtime Model

A Workflow Run is a business-process instance bound to a code-owned definition key, definition version, and payload schema version. It schedules Jobs. A Job owns steps, checkpoints, attempts, idempotency keys, heartbeat/lease data, and a fencing token. LangGraph checkpoints the inner quality or Bible graph in PostgreSQL; `python-statemachine` validates outer transitions, while database rows remain authoritative.

Jobs support cooperative pause and cancel, crash recovery, heartbeat timeouts, transient retry, and immutable terminal states. Worker concurrency is configurable but defaults to one; jobs for the same project are serialized. The deterministic fake provider must pass the complete framework acceptance scenario before live-model quality work begins.

## Story Model

Projects contain versioned outlines, chapters, Story Bibles, memory, jobs, reviews, and exports. A chapter points to one immutable Canonical Chapter Version; model-generated, model-revised, and author-edited variants remain Candidates until reviewed. Manual edits use the same review gate and explicit base-version conflict handling.

Domain references are typed ID columns with indexes rather than physical foreign keys. Domain services validate references transactionally, while archive and supersede replace hard deletion. Direct SQL maintenance can therefore create orphans and is outside the product's supported integrity guarantees.

A Story Bible has editable drafts and immutable published versions. Bible identities persist across versions; Bible entries are frozen in each published version. Extraction and interview output become fine-grained change proposals, never direct overwrites. First-version Bible entry types are character, relationship, location, organization, item, rule, event, style, and open question.

Memory is layered into published Bible facts, canonical-chapter state increments, and project-scoped retrieval documents. Only canonical chapters update long-term state. Rewriting an earlier chapter marks downstream projections stale without invalidating later chapters.

## Retrieval

The system uses a custom PostgreSQL 16 image containing zhparser and pgvector. Chinese full-text search uses zhparser, a project text-search configuration, GIN indexes, and `websearch_to_tsquery`. Structured entity, alias, and event recall complements segmentation. Context assembly records selected and excluded sources with scores and reasons under a versioned Context Profile.

## Model Execution

LiteLLM provides provider access behind the shared model gateway. Provider credentials and versioned model profiles are separate database objects. Jobs bind their model profile, prompt-template versions, Bible version, outline version, upstream canonical versions, memory projection, user instructions, and context profile at creation time.

Writing models return constrained Markdown and metadata; evaluation, diagnosis, extraction, and projection return typed JSON. Invalid output receives at most one repair call. Full prompts, completions, model metadata, latency, token use, cost, and repair traces remain in PostgreSQL. Langfuse is not a default dependency.

## Delivery Phases

1. Engineering skeleton, migrations, queue/worker runtime, domain invariants, fake provider, and framework acceptance tests.
2. Bible interview workflow, chapter generation workflow, review, canonicalization, memory projection, and export.
3. Existing-fiction extraction, hybrid Bible bootstrap, stronger quality diagnostics, advanced editing, and optional model improvements.
