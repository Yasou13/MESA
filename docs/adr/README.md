# Architecture decisions

ADRs record why a design was selected. Use [current architecture](../architecture-v4.md)
for implemented behavior and the [operations guide](../RUNBOOK.md) for procedures.
A historical claim or roadmap is not current capability or release evidence.

## Current decisions

| ADR | What it protects |
|---|---|
| [0009 — Ledger and single owner](0009-v4-ledger-and-single-storage-owner.md) | SQLite authority, ordered projections and one V4 storage writer |
| [0010 — Rank fusion](0010-v4-rrf-and-pagerank-observation.md) | Reciprocal rank fusion |
| [0011 — Tenant/dataset authorization](0011-v4-tenant-dataset-authorization.md) | Server-authored scope and explicit permissions |
| [0012 — DAO repository boundaries](0012-memory-dao-repository-boundaries.md) | Implemented catalog boundary; future extractions remain proposals |
| [0013 — Production dependencies](0013-one-way-production-layer-dependencies.md) | One-way imports and explicit composition roots |
| [0014 — Rebuild closure](0014-v4-closure-and-staged-evolution.md) | Offline full-root rebuild and reversible cutover; historical roadmap separated |
| [0015 — Adaptive query planning](0015-bounded-adaptive-query-planning.md) | Opt-in bounded reformulation with fixed scope and one context-packing pass |

## Retired decisions

The original files are retained in Git history. These summaries preserve the
reason for each transition. Retired numbers are not reused.

<a id="retired-0001"></a>

### 0001 — SQLite WAL and LanceDB

V3 replaced an in-memory graph/synchronous façade with SQLite WAL/FTS and
LanceDB behind an async DAO. Mandatory agent partitioning was its isolation
mechanism. The current runtime uses SQLite as canonical authority and LanceDB
for derived vectors. Tenant and dataset scope define the authorization boundary
([0011](0011-v4-tenant-dataset-authorization.md)).

<a id="retired-0002"></a>

### 0002 — Alpha reranking

The historical score-blending design was superseded for V4 by
[0010](0010-v4-rrf-and-pagerank-observation.md). Its old performance claims are
not current measurements or a guarantee over a vector-only baseline.

<a id="retired-0003"></a>

### 0003 — Adaptive validation routing

The current ingestion runtime uses deterministic validation. See
[ingestion](../concepts-and-ingestion.md) for the supported mutation lifecycle.

<a id="retired-0004"></a>

### 0004 — Background tasks instead of Celery

The old coroutine/background-task rationale and mock benchmark do not define
V4 durability. [0009](0009-v4-ledger-and-single-storage-owner.md) owns the durable
ledger/outbox and single-owner decision. Historical throughput is not release proof.

<a id="retired-0005"></a>

### 0005 — PageRank quarantine

[0010](0010-v4-rrf-and-pagerank-observation.md) superseded quarantine based on
graph centrality. The derived graph projection has since been retired.

<a id="retired-0006"></a>

### 0006 — Spreading activation

The historical design damped activation through high-degree nodes to limit
fan-out and irrelevant context. The [retrieval guide](../retrieval-and-graph.md)
describes the current search lanes and evidence checks.

<a id="retired-0007"></a>

### 0007 — WAL queue and phantom writes

V3 used a durable SQLite `lancedb_wal` queue to recover writes that reached only
one store. That rationale does not create a shared ACID transaction. V4 ordered
projection, fencing and ownership are defined by
[0009](0009-v4-ledger-and-single-storage-owner.md).

<a id="retired-0008"></a>

### 0008 — Comparative benchmark architecture

Comparative retrieval/Full-QA methodology belongs to the external `mesa_benchmark`
project. See [ecosystem ownership](../architecture-v4.md#ecosystem). Core's
deterministic retrieval tests do not replace independent model-quality evidence.

## Maintaining decisions

New decisions record status, scope, rationale, consequences and recovery
implications. Link superseded decisions here and update the current guide;
do not silently turn a historical result or unimplemented proposal into a guarantee.
