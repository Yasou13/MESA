# Retrieval, graph and evidence

## Four lanes and scoring

`MemoryDAO.search_v4_memory` is the canonical search primitive. Tenant, dataset,
agent, committed-state, jurisdiction and temporal eligibility are checked
before candidate rank contribution. Physical vector IDs and graph assertions
must agree with canonical ownership/evidence.

| Lane | Candidate signal |
|---|---|
| Vector | Compatible `ASSERTION_VECTOR` semantic representation; query embedding |
| BM25 | FTS5 passage/evidence and lexical matching; scoped typed citation lookup |
| Assertion | Predicate, subject, object/literal, evidence, token coverage and confidence |
| Graph | Bounded paths from legal, vector, lexical and entity-name seeds |

For candidate `c`, `RRF(c) = Σ w_lane / (k + rank_lane(c))`, with ranks starting
at 1, default `k=60`, and all four default weights `1.0`. Duplicate backend hits
cast one vote. Ties use candidate identity. The source is
`mesa_storage/retrieval_scope.py`; runtime overrides are in [configuration](configuration.md).

Lexical exact citation +200 and same-statute +40 change the BM25/lexical lane
ordering. Assertion exact citation +150 and same-statute +40 change assertion
ordering. These are **not RRF score increments**. After fusion a query-dependent
legal factor is applied: exact statute/article 1.5, same statute 1.25, competing
statute 0.25, otherwise 1.0. A query without recognized legal citations has
factor 1.0. Search `score` is final fused relevance; higher is better.

Typed ingestion identity uses a bounded alias ontology and immutable provenance.
It improves scoped pre-rank candidate discovery, including article chunks with
little title context. Reserved caller metadata cannot opt into trusted identity.
This contract checks structural linkage; it cannot prove a producer attached
the correct law, revision or real-world interpretation.

## Graph bounds and failure behavior

SQLite owns entities/assertions; Kùzu is a projection. Search uses at most 20
ranked seeds and 3 hops. The DAO asks for at most 500 graph hits; the provider
bounds frontiers to 64 and per-node fan-out to 32, with bounded native fetches.
The default graph request timeout is 15 seconds. These are traversal/resource
bounds, not exhaustive multi-hop completeness or latency guarantees.

Graph evidence preserves path assertion IDs, entities, predicates and direction,
and is reconciled against authorized canonical records. PageRank is telemetry
only. `graph_mode=disabled` turns off this lane for matched ablation; it does
not disable canonical assertion retrieval or graph projection writes.
Responses expose bounded `scope_audit`, `graph_ablation` and `vector_lane`
diagnostics. Their hashes identify contracts/config/scope, not a certification.

A configured operational backend failure yields explicit `503` rather than
silently claiming complete search. Projection-only graph adapters have no
neighbor implementation and report the capability false. A request timeout does
not permanently mark the graph provider unhealthy. Custom embedding providers
are configured but non-operational until a successful embedding call.

## Context and adaptive search

`GET /v4/sessions/{session_id}/context` defaults to `token_budget=2048` and
`retrieval_mode=single`. `ContextBuilder` packs evidence once using `cl100k_base`.
The budget applies to formatted model-visible context, not the entire HTTP
response. Missing tokenizer fails; it does not silently substitute an estimate.
Complete graph proof must fit atomically; compact proof retains canonical
supporting evidence and may omit optional structural details.

Adaptive mode plans only if Q0 is empty or no top-three candidate has two-lane
support. It sends only normalized query text to one planner call, accepts at
most two reformulations, rejects invented legal citations and reuses the original
scope. Outer query fusion uses Q0=1.0, Q1/Q2=0.5. `query_fusion_score` and
`query_origins` remain separate from lane provenance. Planner/expansion failure
preserves Q0. This mode does not generate an answer or certify model quality.

## Interpreting examples

For a neutral single-hop “archive hours” query, inspect source evidence and
mutation state. For a multi-hop query, require every supporting assertion in
the path; a reachable entity alone is insufficient. An empty result is a valid
no-evidence outcome, not proof that a statement is false. Version/date-sensitive
queries should supply timezone-qualified `valid_at` or an interval and, when
needed, jurisdiction. These are request patterns, not measured benchmark results.

Core context packing is distinct from the independently sealed Profile B
context and scorer in [E2E Certification](ecosystem.md).
