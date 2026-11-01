# Retrieval, graph and evidence

## Four lanes and scoring

`MemoryDAO.search_v4_memory` is the canonical search primitive. Tenant, dataset,
agent, committed-state, jurisdiction and temporal eligibility are checked
before candidate rank contribution. Physical vector IDs and graph assertions
must agree with canonical ownership/evidence.

BM25 uses a temporary FTS5 index containing only authorized assertions, so
out-of-scope rows do not enter its corpus statistics. The scope cache reuses
that index only while SQLite's data version matches the pinned read snapshot.
The `scope_audit` describes eligibility; negative tests also check rank and
observable diagnostic isolation.

| Lane | Candidate signal |
|---|---|
| Vector | Compatible `ASSERTION_VECTOR` semantic representation; query embedding |
| BM25 | FTS5 passage/evidence and lexical matching; scoped typed citation lookup |
| Assertion | Predicate, subject, object/literal, evidence, token coverage and confidence |
| Graph | Bounded paths from legal, vector, lexical and entity-name seeds |

For candidate `c`, `RRF(c) = Σ w_lane / (k + rank_lane(c))`, with ranks starting
at 1 and default `k=60`. Runtime defaults are vector weight `8.0` and the other
three weights `1.0`, selected by the [dev benchmark](#round-14-dev-benchmark).
Duplicate backend hits cast one vote. Ties use candidate identity. The RRF
helper in `mesa_storage/retrieval_scope.py` uses unit weights when none are
passed; the server passes its [configuration](configuration.md) to the DAO.

Lexical exact citation +200 and same-statute +40 change the BM25/lexical lane
ordering. Assertion exact citation +150 and same-statute +40 change assertion
ordering. These are **not RRF score increments**. After fusion a query-dependent
legal factor is applied: exact statute/article 1.5, same statute 1.25, competing
statute 0.25, otherwise 1.0. A query without recognized legal citations has
factor 1.0. HTTP/SDK `final_score` is final fused relevance; higher is better.
`rrf_score`, `legal_factor` and lane `raw_score` are separate diagnostics;
MCP response adapters may expose a normalized `score` field.

Optional [reranking](configuration.md#providers-and-capacity) orders fused
candidates by a cross-encoder score after the search connection is released.
It adds `rerank_score`; `rrf_score` and `final_score` retain their fusion values.
Equal reranker scores retain fused order. Reranking is off by default.

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
response. If canonical BPE cannot load, the current OpenAI counting path uses
one token per UTF-8 byte as a conservative offline upper bound, even when
`strict=True` is passed. This may pack less evidence and is not exact cl100k
measurement. Exceptions raised by the counting interface propagate.
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
context and scorer in [E2E Certification](architecture-v4.md#ecosystem).

## Round 14 dev benchmark

Measured on 2026-11-01 using the frozen Tier M corpus: 141 documents and 5,041
chunks ingested through this branch's authenticated document endpoint, with
article chunking enabled, extraction off, embeddings and graph on. Only the
101-question dev split was evaluated (85 answerable, 16 no-answer); the test
split was not accessed. The unmodified `mesa_eval.py` computes passage hit@5
and MRR on answerable questions; latency covers all 101 questions. Retrieval
limit and reranker depth are both 50, with max_length 1024 for the reranker.

Embeddings use `BAAI/bge-m3` (1024 dimensions, normalized), and reranking uses
`BAAI/bge-reranker-v2-m3`. Each row below uses the same complete store, GPU
models and other lane weights of 1. Model revisions, package versions, source
hashes, unrounded MRR and the selection rule are in
[the measurement data](benchmarks/round14_dev.json).

| Vector weight | Reranker | Passage hit@5 | Passage MRR | Search p50 (s) | Search p95 (s) |
|---:|---|---:|---:|---:|---:|
| 1 | Off | 0.5176 | 0.3953 | 1.2693 | 2.0035 |
| 1 | On | 0.7412 | 0.6816 | 1.9548 | 2.5736 |
| 2 | Off | 0.5765 | 0.4565 | 1.2708 | 1.9640 |
| 2 | On | 0.7529 | 0.7055 | 1.9569 | 2.6364 |
| 4 | Off | 0.6588 | 0.5152 | 1.2836 | 1.9469 |
| 4 | On | 0.7765 | 0.7069 | 1.9279 | 2.6100 |
| 8 | Off | 0.6706 | 0.5663 | 1.2849 | 1.9115 |
| 8 | On | 0.7647 | 0.6973 | 1.9161 | 2.5360 |

The default is selected from **reranker-off** MRR because reranking is off by
default. Weight 8 has unrounded MRR 0.566347, versus 0.395339 at weight 1: a
gain of 0.171008, exceeding the required 0.02. The runtime default
is therefore **8.0**. Without that gain the default would stay 1; ties use the
smaller weight. Weight 4 has the best reranker-on MRR in this sweep. Overrides
remain available through `MESA_RRF_VECTOR_WEIGHT`. These measurements describe
this Turkish legal corpus and these models; other corpora/models need their own
measurements.

A separate device pass at vector weight 1 measured the optional reranker:

| Inference device / reranker | Passage hit@5 | Passage MRR | Search p50 (s) | Search p95 (s) |
|---|---:|---:|---:|---:|
| GPU / Off | 0.5176 | 0.3953 | 1.2531 | 1.9953 |
| GPU / On | 0.7412 | 0.6816 | 1.9627 | 2.6029 |
| CPU / On | 0.7412 | 0.6816 | 26.9158 | 33.8756 |

Hardware: Colab A100-SXM4 40GB and Intel Xeon 2.20GHz CPU, with eight CPU
threads. Python 3.13.16, Torch 2.11.0+cu130, sentence-transformers 5.7.0,
transformers 5.21.0, NumPy 2.5.4, LanceDB 0.41.0 and Kùzu 0.11.3. These are
compatible installed Colab packages, rather than the lockfile's exact ML pins;
the local gate uses the locked dev environment. Both models were explicitly
acquired before runtime startup. The device pass moves both models to CPU
without quantization for its CPU row.

Requests use the actual authenticated HTTP route through ASGI, without TCP.
Passes run sequentially, reusing the store and caches; one first-query warmup
is excluded from each pass (warmup values are in the data). These are descriptive
latencies, including embedding, scoped retrieval and optional reranking, rather
than a controlled hardware speedup claim. The benchmark store was ingested with
the measured source commit and then reused after the search-lock change, which
kept all 101 dev top-50 result dictionaries identical. The original frozen
corpus and question files were read only throughout.
