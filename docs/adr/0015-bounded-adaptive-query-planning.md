# ADR 0015: Bounded adaptive query planning

- Status: Accepted
- Baseline: MESA 0.7.1 at `9f27c82`
- Extends: ADR 0013 and ADR 0014

## Context

`MemoryDAO.search_v4_memory` is the canonical deterministic single-query
retrieval primitive. It applies the authorized scope to the vector, BM25,
assertion and graph lanes, fuses those lanes with RRF and materializes evidence
provenance. `ContextBuilder` calls that primitive once and then owns the only
model-visible evidence-packing and token-budget pass.

The legacy `HybridRetriever` has an optional multi-hop decomposition path.
That path splits a question into research subqueries and is not semantic query
reformulation, so its contract and behavior remain unchanged.

## Decision

Bounded adaptive retrieval will be a `mesa_memory.retrieval` module above the
storage layer. Its small interface accepts one immutable server-authored search
scope and returns one fused candidate list plus bounded diagnostics.

The module always runs the normalized original query first. A pure trigger
requests planning only when the result is empty or none of the first three
candidates has at least two retrieval origins. Planning makes at most one call
through the existing `BaseUniversalLLMAdapter` interface and accepts at most
two validated semantic reformulations. Retrieved evidence is never included in
the planner prompt.

Every accepted reformulation is sent to the same
`MemoryDAO.search_v4_memory` method with the exact original scope arguments.
Query result lists are fused with the existing deterministic RRF function at
weights Q0=1.0, Q1=0.5 and Q2=0.5. Canonical assertion/evidence/candidate
identity deduplicates results, while `query_origins` records query ID and rank.
The existing inner lane provenance and scores are preserved; the outer score is
stored separately as `query_fusion_score`.

`ContextBuilder` gains an opt-in `retrieval_mode` of `adaptive` and accepts the
adaptive retriever by dependency injection. Its default remains `single`,
which follows the existing direct DAO call. Both modes feed exactly one final
candidate list into the unchanged packing implementation.

Runtime composition creates the planner with `AdapterFactory` only for an
adaptive context request. The V4 context endpoint, sync/async clients and
`mesa_get_context` propagate the optional mode. Session authorization continues
to derive tenant, agent and dataset scope on the server; planner output is only
query text and cannot alter scope.

## Bounds and failure behavior

- Default mode: one retrieval, zero planner calls.
- Adaptive mode: at most one planner call and two additional retrievals.
- Context packing: exactly one pass, with the existing 2048-token default.
- Invalid output, provider failure or zero valid expansions preserves Q0 and
  returns a bounded diagnostic status.
- A failed optional expansion preserves Q0 and any successful expansion.
- A failed original retrieval keeps the existing core failure semantics.

## Deliberate exclusions

No recursive planning, question decomposition, planner cache, answer
generation, reranker, learned confidence, dynamic weights, new provider
abstraction, persistent schema or retrieval-lane change is introduced.
