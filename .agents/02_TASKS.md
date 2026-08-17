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

Status: BUILT
Evidence: Traced production call paths across server composition, AdapterFactory, ConsolidationLoop, TripletExtractor, ingestion_worker, VectorEngine, LLM adapters, projection workers, rebuild/cutover, GraphWriter, and embedding identity. Mapped current ownership: (1) Validation is owned by ValidationPolicy (Mode 0/1/2); (2) Extraction is owned by TripletExtractor with unconditional RebelExtractor init and dual-LLM fallback; (3) Embedding generation is scattered across VectorEngine (direct SentenceTransformer loading / provider callback), LLM adapters (embed/aembed methods), and GraphWriter; (4) Graph projection is owned by GraphWriter into KuzuGraphProvider and MemoryDAO.
Tests: tests/test_r4_durable_policy_snapshot.py
Commit: pending

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit:

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

Status:
Evidence:
Tests:
Commit: