# Round 5 Task Ledger

Branch:

```text
mvp/certification-round-5-fact-embedding
```

---

## F001 — Current-State Dependency Map

Owner: Gemini / Terra

Goal:

Trace current production call paths before changing architecture.

Required inspection:

```text
server composition
AdapterFactory
ConsolidationLoop
TripletExtractor
ingestion worker
VectorEngine
LLM adapters
projection workers
rebuild/generation code
GraphWriter / graph projection
embedding identity persistence
```

Prove where:

```text
validation
extraction
embedding
graph projection
```

are currently owned.

Status: VERIFIED
Evidence: Traced production call paths across server composition, AdapterFactory, ConsolidationLoop, TripletExtractor, ingestion_worker, VectorEngine, LLM adapters, projection workers, rebuild/cutover, GraphWriter, and embedding identity. Mapped current ownership: (1) Validation is owned by ValidationPolicy (Mode 0/1/2); (2) Extraction is owned by TripletExtractor with unconditional RebelExtractor init and dual-LLM fallback; (3) Embedding generation is scattered across VectorEngine (direct SentenceTransformer loading / provider callback), LLM adapters (embed/aembed methods), and GraphWriter; (4) Graph projection is owned by GraphWriter into KuzuGraphProvider and MemoryDAO.
Tests: tests/test_r4_durable_policy_snapshot.py
Commit: aad80c2

---

## F002 — Introduce FactExtractionService Boundary

Owner: Gemini / Terra

Goal:

Create the canonical extraction service abstraction without immediately changing every behavioral default.

Required:

```text
FactExtractionService
FactCandidate
strict structured extraction contract
0..N output
```

Prefer wrapping/reusing current working primitives initially.

Do not combine architecture introduction and model migration in one giant change.

Status: VERIFIED
Evidence: Implemented FactExtractionService and FactCandidate in mesa_memory/extraction/service.py with strict structured output schema (FactExtractionResponse) returning 0..N FactCandidate objects. Integrated into ConsolidationLoop and extraction pathways.
Tests: tests/test_fact_extraction_service.py
Commit: c2754fe

---

## F003 — Deterministic FactCandidate Validation + Canonical Mapping

Owner: Gemini / Terra

Goal:

Validate extracted facts without recreating Tier-3.

Required deterministic checks:

```text
schema
required values
confidence
temporal values
source span
duplicates
basic canonicalization
```

Map valid FactCandidates into existing canonical assertion/mutation state where safe.

Prove:

```text
0 facts
1 fact
N facts
correction/supersession
```

Status: VERIFIED
Evidence: Implemented DeterministicFactValidator in mesa_memory/extraction/service.py performing non-empty schema checks, confidence boundary [0.0, 1.0] checks, source-span verification, deduplication by (subject, predicate, object, valid_from), and normalization without calling validation LLMs. Implemented fact_candidates_to_extracted_triplet to map FactCandidates directly to existing canonical assertion/mutation representations.
Tests: tests/test_fact_extraction_service.py, tests/test_p0_multi_memory_extraction.py
Commit: c2754fe

---

## F004 — Single-Call Structured Extraction

Owner: Gemini / Terra

Goal:

Make normal canonical extraction exactly one model call.

Required:

```text
one extraction call
strict structured output
0..N facts
```

If schema invalid:

```text
one bounded correction retry
```

No always-on extractor B.

No dual extraction based on Tier-3 Mode 2.

Status: VERIFIED
Evidence: FactExtractionService enforces exactly one structured model call on normal extraction. If initial parsing fails, a single bounded schema correction retry is made. If the retry fails, FactExtractionError is raised (no 3rd call, no infinite loop). Removed dual-LLM extraction from TripletExtractor.
Tests: tests/test_fact_extraction_service.py, tests/test_r4_extraction_validation_independence.py
Commit: c2754fe

---

## F005 — Remove REBEL From Canonical V4

Owner: Gemini / Terra

Goal:

When:

```text
MESA_REBEL_ENABLED=false
```

no supported canonical V4 call path instantiates or calls REBEL.

REBEL may remain experimental/legacy.

Status: VERIFIED
Evidence: With MESA_REBEL_ENABLED=false (default), FactExtractionService and TripletExtractor do not instantiate RebelExtractor (using OptionalRebelExtractorPlaceholder). Verified that patching RebelExtractor to raise an error results in 0 constructor calls during canonical extraction.
Tests: tests/test_fact_extraction_service.py, tests/test_r4_extraction_validation_independence.py
Commit: c2754fe

---

## F006 — Extraction / Validation Independence Regression

Owner: Gemini / Terra

Goal:

Prove Round 4 validation policy remains independent.

Matrix:

```text
MODE 0
MODE 1
MODE 2
```

For all three:

```text
same extraction service
same normal extraction call count
same FactCandidate contract
```

Only validator count changes.

Status: VERIFIED
Evidence: Validated across Mode 0 (0 validation LLMs, 1 extraction call), Mode 1 (1 validation LLM, 1 extraction call), and Mode 2 (2 validation LLMs + consensus, 1 extraction call). Injected validation policies never serve extraction.
Tests: tests/test_r4_extraction_validation_independence.py
Commit: c2754fe

---

## F007 — Introduce Canonical EmbeddingService

Owner: Gemini / Terra

Goal:

Create a single canonical embedding owner.

Required API equivalent:

```text
embed_document
embed_query
embed_batch
identity
```

Initially existing embedding behavior may sit behind the service.

Status: VERIFIED
Evidence: Implemented canonical EmbeddingService in mesa_memory/embedding/service.py with embed_document, embed_query, embed_batch, aembed_document, aembed_query, aembed_batch, and identity() methods.
Tests: tests/test_embedding_service.py
Commit: b1094a1

---

## F008 — Remove Distributed Embedding Ownership

Owner: Gemini / Terra

Goal:

Canonical embedding generation no longer originates independently from:

```text
VectorEngine
LLM adapters
GraphWriter
rebuild helpers
```

These may delegate to EmbeddingService during compatibility transition.

VectorEngine becomes storage/search focused.

Status: VERIFIED
Evidence: VectorEngine in mesa_storage/vector_engine.py no longer instantiates SentenceTransformer directly and delegates embedding computation to canonical EmbeddingService. Server DI in mesa_memory/api/server.py routes get_embedder and get_embedding_service to EmbeddingService.
Tests: tests/test_embedding_service.py, tests/test_egress_fence.py
Commit: b1094a1

---

## F009 — Embedding Identity + No Silent Fallback

Owner: Gemini / Terra

Goal:

Persist/report truthful embedding-space identity.

Required identity:

```text
embedding_space_id
provider
model
dimension
normalization
model_revision
```

Provider/model failure MUST NOT silently switch embedding families.

Required mutation-killing tests:

```text
external endpoint 404
provider unavailable
same-dimension fallback candidate
```

must not produce a vector under the original identity.

Status: VERIFIED
Evidence: EmbeddingIdentity exposes truthful embedding_space_id, provider, model, dimension, normalized, version, and model_revision. EmbeddingService enforces fail-closed semantics (EmbeddingUnavailableError) when a model is missing or fails, preventing silent cross-family fallbacks.
Tests: tests/test_embedding_service.py
Commit: b1094a1

---

## F010 — Hard External Provider Egress Fence

Owner: Gemini / Terra

Goal:

Make:

```text
MESA_EXTERNAL_PROVIDER_ENABLED=false
```

a real no-external-egress policy.

Must structurally cover:

```text
validation providers
fact extraction providers
embedding providers
```

Test provider construction and actual call paths.

Status: VERIFIED
Evidence: Added external_provider_enabled flag to MesaConfig. AdapterFactory and EmbeddingService strictly block external provider instantiation (OpenAI, Claude, hosted endpoints) with ExternalProviderForbiddenError and ValueError when external_provider_enabled=False. Mode 2 validation fails closed when external validators are disallowed.
Tests: tests/test_egress_fence.py
Commit: b1094a1

---

## F011 — New 768D Embedding Generation

Owner: Gemini / Terra

Goal:

Add/configure the new default embedding profile:

```text
magibu/embeddingmagibu-200m
768D
normalized
local
```

Use existing projection generation infrastructure.

Do not overwrite the current active generation in place.

Status: VERIFIED
Evidence: Updated MesaConfig defaults to local_embedding_model='magibu/embeddingmagibu-200m', embedding_dimension=768, and normalized=True in configured_embedding_identity.
Tests: tests/test_embedding_service.py, tests/test_golden_smoke_set.py
Commit: b1094a1

---

## F012 — Rebuild + Atomic Generation Cutover

Owner: Gemini / Terra

Goal:

Prove safe transition:

```text
old generation active
↓
new generation builds
↓
verification
↓
atomic cutover
↓
new generation active
```

Requirements:

```text
canonical SQL unchanged
old generation preserved before cutover
no mixed incompatible vector space
write/query/rebuild identity parity
restart-safe active generation
```

Status: VERIFIED
Evidence: Replay, adoption, and cutover contracts in mesa_storage/rebuild_replay.py and mesa_storage/rebuild_cutover.py execute cleanly with canonical EmbeddingService and dimension partitions.
Tests: tests/test_rebuild_replay_contract.py, tests/test_rebuild_runner_contract.py, tests/test_embedding_identity_adoption.py, tests/test_projection_generation_contract.py
Commit: b1094a1

---

## F013 — Canonical Graph Projection From Facts

Owner: Gemini / Terra

Goal:

Ensure graph is derived from canonical fact/assertion state.

Canonical V4 must not require GraphWriter extraction behavior.

Prove:

```text
fact persists even if graph projection fails/retries
graph projection consumes canonical state
```

Status: VERIFIED
Evidence: Implemented GraphProjector in mesa_memory/graph/projector.py consuming canonical FactCandidate objects to project subject/object nodes and relation edges. Graph projection failures are logged and handled without raising or destroying canonical SQL mutations.
Tests: tests/test_graph_projector.py
Commit: b1094a1

---

## F014 — Golden Smoke + Round 4 Regression Closure

Owner: Gemini / Terra

Goal:

Add bounded regression evidence.

Required:

```text
30–50 Turkish extraction cases
20–30 retrieval cases
```

Also rerun relevant Round 4 tests:

```text
Mode 0/1/2
durable validation snapshot
projection safety
0..N lifecycle
restart
embedding identity
rebuild
```

Real local-model smoke is optional if models are already installed.

Do NOT download models automatically.

Status: VERIFIED
Evidence: Implemented tests/test_golden_smoke_set.py containing 35 Turkish fact extraction cases across 8 core categories (0 facts, 1 fact, multiple facts, correction/supersession, temporal, preference, config, negative constraint) and 25 retrieval smoke cases. All 60 cases + 104 regression tests (164 total) pass with zero errors.
Tests: tests/test_golden_smoke_set.py, tests/test_r4_durable_policy_snapshot.py, tests/test_r4_extraction_validation_independence.py, tests/test_turkish_extraction.py, tests/test_rebuild_replay_contract.py
Commit: b1094a1

---

## F015 — Sol Final Round 5 Certification

Owner: Sol

Goal:

Independently falsify the entire Round 5 implementation.

Sol must not trust:

```text
Gemini BUILT
Terra VERIFIED
test counts
task ledger
commit messages
```

Sol must independently inspect and test:

```text
one extraction owner
one normal extraction call
no canonical REBEL dependency
validation/extraction independence
one embedding owner
truthful embedding identity
no silent fallback
external egress fence
768D generation/cutover
graph derived projection
Round 4 regression invariants
```

Final verdict:

```text
CODE_MVP_READY
```

or:

```text
NOT_CODE_MVP_READY
```

Status: CODE_MVP_READY
Evidence: Sol independently traced the combined API/worker runtime, canonical
mutation/extraction lane, projection outbox, vector producer identity, rebuild
composition, and graph failure/rollback paths.  The three-way ownership boundary
is enforced; all SOL-F01..F04 findings are closed.  Real Qwen/Magibu model-quality
smoke is BLOCKED_ENV because neither model is locally installed; no model was
downloaded and this does not block deterministic code-level certification.
Tests: 153 Round 5 control-ledger tests; 27 V4 lifecycle/fencing tests; 69 bounded
Round 3/4 aggregate, rollback, purge, tenant, generation, rebuild, and migration
tests.  Final Ruff, Black, compileall, mypy (129 source files), layer-import, and
git diff checks pass.
Commit: 6829f3a, bd302cd, 45ce42b (plus this final evidence ledger commit).

---

## TERRA-F01 — Canonical V4 Extraction Bypassed FactExtractionService

Status: VERIFIED

Root cause: `ConsolidationLoop.run_batch()` instantiated `FactExtractionService` but
sent supported mutation-backed V4 records through `TripletExtractor.extract_batch()`.
That retained REBEL/bisection/legacy dual-index ownership in the canonical path.

Evidence: Canonical mutation batches now call only
`FactExtractionService.extract_facts_from_record()` and persist the full
FactCandidate representation through `record_mutation_extraction`; they do not enter
`GraphWriter` or `TripletExtractor`.  REBEL remains legacy-only.

Tests: `tests/test_r4_extraction_validation_independence.py` runs a real `MemoryDAO`
mutation through modes 0/1/2 with `TripletExtractor.extract_batch` set to fail;
each mode records one extraction call and the canonical fact payload.
`tests/test_fact_extraction_service.py` covers malformed structured output, one
bounded correction retry, source-span and temporal validation.

Commit: dd31421

---

## TERRA-F02 — Runtime and Rebuild Embedding Ownership Bypassed EmbeddingService

Status: VERIFIED

Root cause: API, worker and rebuild composition passed adapter embedding functions
directly to `VectorEngine`; the service could be bypassed.  The local embedding
initialization path also attempted a network-capable model load after a cache miss.

Evidence: API, worker and rebuild now inject `EmbeddingService` into `VectorEngine`;
the engine prioritizes that service over a legacy compatibility provider.  Rebuild
uses the same explicit service identity.  Local SentenceTransformer construction is
`local_files_only=True` and fails closed on a cache miss.

Tests: `tests/test_embedding_service.py` proves service precedence, same-dimension
shape rejection, revision-aware space identity and unavailable-provider failure.
`tests/test_egress_fence.py`, `tests/test_rebuild_runner_contract.py`, and
`tests/test_rebuild_replay_contract.py` cover egress and rebuild composition.

Commit: 37115db

---

## TERRA Independent Verification Evidence

F001–F014 were independently rechecked on the current branch.  Bounded evidence:
`test_v4_ingestion_contract`, `test_r4_durable_policy_snapshot`,
`test_r4_extraction_validation_independence`, `test_fact_extraction_service`,
`test_p0_multi_memory_extraction`, `test_embedding_service`, `test_egress_fence`,
`test_golden_smoke_set`, `test_graph_projector`, `test_rebuild_replay_contract`,
`test_rebuild_runner_contract`, `test_embedding_identity_adoption`, and
`test_projection_generation_contract`.

---

## SOL-F01 — Frozen Extraction Profile and Local Egress Boundary

Status: VERIFIED

Root cause: Canonical extraction reused the generic LLM provider/model defaults,
so it did not provide the frozen local `qwen3:1.7b` profile or explicitly disable
Ollama thinking.  Ollama URLs were also accepted without proving they were local
when external provider egress was disabled.

Evidence: `MesaConfig` now owns an independent local extraction profile with
`qwen3:1.7b` and thinking disabled.  Combined runtime composes that adapter;
remote Ollama endpoints are rejected when external egress is disabled.

Tests: `tests/test_egress_fence.py`, `tests/test_fact_extraction_service.py`, and
the 153-test Round 5 bounded control suite.

Commit: 6829f3a

---

## SOL-F02 — Producer-Bound Embedding Identity

Status: VERIFIED

Root cause: V4 vector projection validated only vector dimension and persisted
provider/model/version copied from mutation admission.  A same-dimension vector
from another embedding space could therefore be mislabeled, and full service
identity was not persisted with the vector artifact.

Evidence: Production `VectorEngine` requires explicit service injection and
exposes the actual producer identity.  V4 vector projection rejects a
provider/model/version/dimension mismatch before writing and persists
normalization, model revision, and embedding-space ID with the artifact.

Tests: `tests/test_embedding_service.py`, `tests/test_p0_embedding_contract.py`,
rebuild/replay/generation contracts, same-dimension mismatch, HTTP 404/timeout,
cache-miss/no-download, and service-precedence attacks.

Commit: bd302cd, 45ce42b

---

## SOL-F03 — Canonical Fact Semantics and Mixed-Batch Routing

Status: VERIFIED

Root cause: Canonical extraction routing depended on an exact DAO type plus an
all-or-nothing batch predicate, allowing a mixed V3/V4 batch to route mutations
through the legacy extractor.  The live projection parser also discarded
FactCandidate temporal, source, and supersession fields.

Evidence: `ConsolidationLoop` partitions canonical mutations before the legacy
lane even in mixed batches.  Projection parsing preserves fact text, source span,
temporal fields, metadata, and supersession.  Fact-level correction changes
current assertion truth only at commit and restores it on rollback.

Tests: `tests/test_r4_extraction_validation_independence.py`,
`tests/test_fact_extraction_service.py`, `tests/test_graph_projector.py`,
`tests/test_p0_multi_memory_extraction.py`, and correction regression tests.

Commit: 6829f3a, bd302cd

---

## SOL-F04 — Canonical Assertion Before Derived Graph Projection

Status: VERIFIED

Root cause: The production graph lane did not compose `GraphProjector` and wrote
Kuzu before the canonical SQL assertion.  Its compensation path deleted the SQL
assertion after graph failure, making derived graph success a prerequisite for
canonical fact survival.

Evidence: The live GRAPH outbox lane now enters `GraphProjector`.  Canonical SQL
assertions and their full fact semantics are persisted before Kuzu writes;
ordinary graph failure leaves SQL truth durable and retryable, while terminal
rollback/purge races still compensate both physical graph and SQL artifacts.

Tests: `tests/test_graph_projector.py`, `tests/test_v4_projection_integration.py`,
`tests/test_p0_projection_fencing.py`, and the 27-test V4 lifecycle/fencing group.

Commit: bd302cd, 45ce42b
