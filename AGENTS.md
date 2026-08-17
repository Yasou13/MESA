# MESA MVP — Certification Round 5 Agent Contract

## Active Round

Certification Round 5:

> Fact Extraction + Embedding Boundary

Active branch:

```text
mvp/certification-round-5-fact-embedding
```

Gemini, Terra and Sol MUST work on the same branch.

Do not implement or merge work on `main` during this round.

---

## Source of Truth

For this round:

```text
production code
    >
tests/evidence
    >
current Round 5 control files
    >
historical audits/reports
    >
agent handoff claims
```

Historical audit reports are hypotheses and design input.

They were written before the Round 4 validation-policy separation and MUST NOT be copied blindly into Round 5.

Every historical finding must be re-validated against the current branch.

---

## Central Architectural Invariant

Round 4 established:

```text
Source Validation Policy
≠
Fact Extraction
≠
Embedding
```

Round 5 MUST preserve and strengthen this separation.

Target ownership:

```text
ValidationPolicy
→ source/admission validation
→ Mode 0 / 1 / 2

FactExtractionService
→ raw/source text to 0..N FactCandidate
→ one normal extraction model call

Deterministic Fact Validation
→ schema/fact correctness checks
→ no validation LLM

EmbeddingService
→ all canonical document/query embeddings
→ owns embedding-space identity

VectorEngine
→ vector storage/search only

GraphProjector
→ derived graph projection from canonical facts
```

No component may silently assume another component's responsibility.

---

## Frozen Target Pipeline

```text
RAW EVENT
    ↓
Deterministic Admission
    ↓
Source ValidationPolicy
Mode 0 / 1 / 2
    ↓
FactExtractionService
    ↓
one structured extraction call
    ↓
0..N FactCandidate
    ↓
Deterministic Fact Validation
    ↓
Conflict / Temporal Logic
    ↓
Canonical Mutation / SQL Truth
       ┌───────────────┴───────────────┐
       ↓                               ↓
EmbeddingService                GraphProjector
       ↓                               ↓
VectorEngine                         Kuzu
       ↓
LanceDB
```

---

## Extraction Contract

Canonical extraction MUST NOT be based on `TripletExtractor` as the public/core abstraction.

Target abstraction:

```text
FactExtractionService
```

Normal path:

```text
text
→ exactly one extraction model call
→ strict structured output
→ 0..N FactCandidate
```

Default extraction profile:

```text
provider = local/Ollama-compatible
model = qwen3:1.7b
thinking = false
```

The architecture MUST remain model-independent.

Changing the model later must be a configuration change, not another architecture rewrite.

---

## FactCandidate

Minimum canonical extraction contract:

```text
FactCandidate

fact_text
subject
predicate
object
valid_from
valid_to
confidence
source_span
```

Optional compatibility/semantic fields may include:

```text
supersedes
metadata
```

Do not create a large new memory-type hierarchy.

Round 5 SHOULD map FactCandidate into the existing canonical assertion/mutation representation where safe.

Avoid a database schema rewrite unless executable evidence proves it is required.

---

## Extraction Model Count

Normal canonical extraction:

```text
exactly one extraction model call
```

Do NOT implement:

```text
Extractor A + Extractor B
```

for every event.

Do NOT use:

```text
TIER3_MODE=2
```

to imply dual extraction.

Tier-3 validation count and extraction count are independent.

Round 5 does NOT introduce low-confidence second-model extraction consensus.

If structured output is invalid:

```text
first extraction
→ one bounded schema correction retry
→ still invalid
→ explicit failure/retry/review state
```

Do not silently accept malformed extraction.

---

## REBEL Contract

REBEL is NOT a canonical MVP dependency.

Target:

```text
MESA_REBEL_ENABLED=false
```

must guarantee:

```text
supported canonical V4 path
→ does not instantiate RebelExtractor
→ does not depend on REBEL success/failure
```

REBEL implementation may remain in experimental/legacy code if moving/deleting it would create unnecessary risk.

But canonical FactExtractionService MUST NOT depend on it.

---

## Deterministic Fact Validation

Round 5 MUST NOT recreate Tier-3 inside the extraction layer.

After extraction:

```text
FactCandidate
→ deterministic validation
```

Examples:

```text
schema correctness
required fields
confidence range
temporal shape
source-span integrity
empty values
duplicate candidates
basic canonicalization
```

This stage does not call validation LLM A or B.

Source validation remains owned by ValidationPolicy.

---

## Embedding Ownership

Round 5 introduces one canonical owner:

```text
EmbeddingService
```

Required conceptual API:

```text
embed_document(text)
embed_query(text)
embed_batch(texts)
identity()
```

All supported canonical embedding production MUST flow through this service.

Target responsibility split:

```text
LLMService
→ generation/extraction

EmbeddingService
→ embedding generation

VectorEngine
→ vector storage/search/delete
```

VectorEngine MUST NOT decide which embedding model to load.

LLM adapters MUST NOT be the canonical owners of embedding generation.

---

## Embedding Identity

EmbeddingService MUST expose/persist truthful identity sufficient for MVP:

```text
embedding_space_id
provider
model
dimension
normalization
model_revision
```

Equivalent existing fields may be reused.

Do not add a large model registry.

Do not introduce automatic model routing.

The identity persisted with vectors/mutations MUST describe the model that actually produced the vector.

---

## Silent Fallback Is Forbidden

Forbidden:

```text
configured embedding model unavailable
→ silently generate vector with another model family
```

Same dimension does NOT imply same embedding space.

Correct behavior:

```text
embedding provider/model unavailable
→ explicit unavailable/retry/failure
```

or an explicitly versioned new embedding generation.

Round 5 chooses fail-closed/retry for normal provider failure.

No cross-family silent fallback.

---

## Default Embedding Profile

Target MVP default:

```text
provider = local
model = magibu/embeddingmagibu-200m
dimension = 768
normalize = true
```

This is an engineering default, not a claim that it is universally the best embedding model.

Architecture must remain model-independent.

---

## Embedding Migration

Do NOT overwrite existing vector generations in place.

Use the existing projection-generation/rebuild/cutover infrastructure where possible.

Required transition:

```text
existing canonical SQL
    ↓
new EmbeddingService
    ↓
new embedding generation
    ↓
new vector projection
    ↓
parity/smoke verification
    ↓
atomic generation cutover
    ↓
old generation retained until safe cleanup
```

Canonical SQL truth MUST NOT be rewritten merely because the embedding model changes.

---

## Graph Contract

Canonical memory is not the graph.

Graph remains a derived projection.

Target:

```text
Canonical Fact / Assertion
        ↓
GraphProjector
        ↓
Kuzu
```

GraphWriter or equivalent legacy components MUST NOT perform canonical fact extraction.

A graph projection failure MUST NOT destroy canonical memory truth.

---

## External Provider Egress Fence

`MESA_EXTERNAL_PROVIDER_ENABLED=false` is a hard policy boundary.

When false, supported production composition MUST NOT construct or use external network providers for:

```text
source validation
fact extraction
embedding
```

Examples of external providers:

```text
openai-compatible hosted endpoint
Claude
other hosted APIs
```

Examples of local providers:

```text
local Ollama
local embedding model
explicit test/mock provider
```

A selected validation Mode 2 that cannot obtain two valid permitted validators MUST fail closed rather than downgrade.

---

## Round 4 Regression Invariants

Round 5 MUST preserve all Round 4 behavior.

Especially:

```text
Mode 0 → zero validation LLM
Mode 1 → exactly one validation LLM
Mode 2 → two distinct validation LLMs + consensus
```

Round 5 MUST NOT couple extraction or embedding model count to validation mode.

Also preserve:

```text
durable validation-policy snapshot
SKIPPED_BY_POLICY / VALIDATED / REJECTED / UNAVAILABLE semantics
projection fencing
restart durability
0..N lifecycle
embedding identity
single ACTIVE revision head
rollback/purge safety
tenant accounting
migration integrity
```

---

## Golden Smoke Set

Round 5 adds a small regression safety set.

Target:

```text
30–50 Turkish fact extraction cases
20–30 retrieval cases
```

This is NOT a benchmark platform.

Required extraction categories include:

```text
0 facts
1 fact
multiple facts
correction
temporal change
preference
technical configuration
negative statement
supersession
irrelevant conversation
```

Do not build:

```text
leaderboard
model registry
auto model selector
benchmark dashboard
A/B platform
```

---

## Explicitly Out of Scope

Do NOT pull unrelated historical audit work into Round 5.

Deferred:

```text
RBAC tenant-scoped key migration
ContextBuilder prompt-injection redesign
hard tokenizer budget
provenance rendering redesign
V3/V4 SDK inheritance split
MCP ToolRegistry refactor
MemoryDAO full split
V3/V4 worker split
public/physical ID redesign
backup quiescence redesign
SQLite durability policy change
REJECTED replay redesign
historical repair authorization redesign
broad package cleanup
full domain plugin framework
MESA Law implementation
```

These require separate rounds.

---

## Explicit Non-Goals

Do NOT add:

```text
GLiNER
mREBEL
BGE-M3 multi-vector
dedicated reranker
special Turkish NER model
fine-tuning
new embedding model registry
automatic model selection
new microservices
Kafka
Redis
new memory-type hierarchy
dual extraction on every event
```

---

## Engineering Rule

Prefer:

```text
existing primitive
→ smallest coherent change
→ adversarial regression
```

over a rewrite.

Historical reports describe problems, not mandatory implementations.

Production truth decides the repair.

---

## Agent Roles

### Gemini

Primary implementation agent.

Owns:

```text
F001-F014
```

Gemini may implement and test but may not final-certify the round.

### Terra

Independent reviewer + repairer.

Must independently falsify Gemini's work.

May add:

```text
TERRA-F01
TERRA-F02
...
```

Terra may mark tasks VERIFIED but may not issue the final Round 5 code verdict.

### Sol

Final adversarial certifier + finalizer.

Owns:

```text
F015
```

May add:

```text
SOL-F01
SOL-F02
...
```

Only Sol may issue:

```text
CODE_MVP_READY
```

or:

```text
NOT_CODE_MVP_READY
```

for Round 5.

---

## Commit Policy

Do not produce one giant Round 5 commit.

Every important independent root-cause change should receive a coherent commit.

Examples:

```text
refactor(extraction): introduce canonical fact extraction service

refactor(embedding): centralize embedding ownership

fix(embedding): remove cross-family silent fallback

feat(embedding): add 768d projection generation

refactor(extraction): remove canonical dual extraction

test(memory): add Turkish golden smoke set
```

Do not create meaningless micro-commits.

---

## Resource Safety

Do not automatically run:

```text
unbounded full pytest
pytest -n auto
paid provider calls
large model downloads
Ollama pulls
large benchmarks
24h soak
destructive migration tests
```

Use bounded tests.

If a real local Qwen or embedding model is already available, it may be used for a bounded smoke run.

Do not download large models automatically.

Model-quality evidence requiring unavailable local models should be reported separately from code correctness.

---

## Final Meaning

Round 5 success means:

```text
Fact extraction ownership is coherent
Embedding ownership is coherent
Validation remains independent
No silent embedding-space corruption exists
Canonical lifecycle remains intact
```

It does NOT mean:

```text
all open MESA audit findings are closed
production deployment is fully certified
model quality is universally proven
MVP_FULLY_VERIFIED
```