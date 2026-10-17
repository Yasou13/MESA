# Architecture decisions

ADRs record why a design was selected. Use [current architecture](../architecture-v4.md)
for implemented behavior and the [operations guide](../RUNBOOK.md) for procedures.
A historical claim or roadmap is not current capability or release evidence.

## Current decisions

| ADR | What it protects |
|---|---|
| [0009 — Ledger and single owner](0009-v4-ledger-and-single-storage-owner.md) | SQLite authority, ordered projections and one V4 storage writer |
| [0010 — RRF and PageRank](0010-v4-rrf-and-pagerank-observation.md) | Rank fusion; PageRank cannot quarantine evidence |
| [0011 — Tenant/dataset authorization](0011-v4-tenant-dataset-authorization.md) | Server-authored scope and explicit permissions |
| [0012 — DAO repository boundaries](0012-memory-dao-repository-boundaries.md) | Implemented catalog boundary; future extractions remain proposals |
| [0013 — Production dependencies](0013-one-way-production-layer-dependencies.md) | One-way imports and explicit composition roots |
| [0014 — Rebuild closure](0014-v4-closure-and-staged-evolution.md) | Offline full-root rebuild and reversible cutover; historical roadmap separated |
| [0015 — Adaptive query planning](0015-bounded-adaptive-query-planning.md) | Opt-in bounded reformulation with fixed scope and one context-packing pass |

## Retired decisions

The original files are retained in Git history. These summaries preserve the
reason for the transition; they do not remove the supported V3 compatibility code.
Retired numbers are not reused.

<a id="retired-0001"></a>

### 0001 — SQLite WAL and LanceDB

V3 replaced an in-memory graph/synchronous façade with SQLite WAL/FTS and
LanceDB behind an async DAO. Mandatory agent partitioning was its isolation
mechanism. V4 now uses SQLite as canonical authority plus derived vector/graph
stores ([0009](0009-v4-ledger-and-single-storage-owner.md)); tenant/dataset
scope replaces agent identity as the security boundary
([0011](0011-v4-tenant-dataset-authorization.md)).

<a id="retired-0002"></a>

### 0002 — Alpha reranking

The historical score-blending design was superseded for V4 by
[0010](0010-v4-rrf-and-pagerank-observation.md). Its old performance claims are
not current measurements or a guarantee over a vector-only baseline.

<a id="retired-0003"></a>

### 0003 — Adaptive validation routing

Automatic small-model/dual-model selection was superseded by explicit validation
modes 0/1/2. Routing cannot weaken the chosen assurance level; see
[ingestion](../concepts-and-ingestion.md). This is distinct from query planning.

<a id="retired-0004"></a>

### 0004 — Background tasks instead of Celery

The old coroutine/background-task rationale and mock benchmark do not define
V4 durability. [0009](0009-v4-ledger-and-single-storage-owner.md) owns the durable
ledger/outbox and single-owner decision. Historical throughput is not release proof.

<a id="retired-0005"></a>

### 0005 — PageRank quarantine

Superseded in full by [0010](0010-v4-rrf-and-pagerank-observation.md): centrality
is observational and cannot remove or quarantine retrieval artifacts.

<a id="retired-0006"></a>

### 0006 — Spreading activation

The historical design damped activation through high-degree nodes to limit
fan-out and irrelevant context. Current V4 uses bounded path retrieval with
canonical evidence checks; [retrieval](../retrieval-and-graph.md) defines its
limits, without promising exhaustive spreading activation.

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
