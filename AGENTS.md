# MESA MVP — Certification Round 7 Agent Contract

## Active Round

Certification Round 7:

> Lifecycle + Catalog Identity Correctness

Active branch:

```text
mvp/certification-round-7-lifecycle-catalog
```

Gemini, Terra and Sol MUST work on the same branch.

Do not implement production changes directly on `main`.

---

# Source of Truth

For Round 7:

```text
1. Current AGENTS.md + current .agents/*
   = active normative contract

2. Current production code
   = implementation truth inspected against the contract

3. Executable tests/runtime evidence
   = certification evidence

4. Historical audits
   = hypotheses and attack maps

5. Agent handoffs/task statuses
   = evidence pointers, never proof

6. Git history
   = historical contract and migration evidence
```

Every historical finding MUST be reproduced against the current Round 7 branch before repair.

---

# Round 7 Goal

Close five bounded MVP correctness areas:

```text
A. Late manifest finalization / revision activation

B. Semantic REJECTED replay truthfulness

C. Historical rollback/replay authorization

D. Revision hash semantic separation

E. Public vs physical catalog identity boundary
```

Round 7 is NOT a general lifecycle or catalog rewrite.

---

# Frozen Round 4–6 Baseline

Do not redesign previously certified architecture.

Preserve:

```text
ValidationPolicy
≠
FactExtractionService
≠
EmbeddingService

Mode 0 → 0 validators
Mode 1 → 1 validator
Mode 2 → 2 validators

normal extraction call count = 1

REBEL absent from canonical V4

0..N FactCandidate

canonical SQL truth independent from graph

EmbeddingService canonical ownership

full embedding-space identity fencing

generation rebuild/cutover

fact/assertion semantic retrieval

tenant-scoped RBAC

RBAC schema migration authority

ContextBuilder untrusted evidence boundary

strict canonical token budget

bounded provenance rendering
```

Round 7 changes must not weaken these contracts.

---

# A — Late Manifest Finalization

## Current Risk

A revision may have all required child mutations committed while its manifest is still unfrozen.

Current commit-time activation barrier correctly refuses activation before:

```text
manifest_frozen_at != NULL
```

However, finalizing/freezing the manifest later must itself reconcile revision activation.

Hard invariant:

```text
all manifest chunks have committed canonical children
+
manifest is frozen
↓
revision activation barrier is re-evaluated
```

Do not require an unrelated future mutation transition to trigger activation.

---

# Activation Requirements

Manifest finalization must be:

```text
atomic
idempotent
race-safe
head-safe
```

Correct conceptual flow:

```text
children commit early
↓
revision remains PENDING
↓
manifest finalized
↓
same canonical activation invariant is re-evaluated
↓
revision becomes ACTIVE exactly when complete
```

Do not create a second lifecycle engine.

Reuse/extract the existing activation barrier.

The same invariant must govern both:

```text
mutation commit
manifest finalization
```

---

# B — REJECTED Replay Truthfulness

A semantic:

```text
REJECTED
```

mutation is terminal under the current MVP state machine.

Round 7 MUST NOT advertise it as replayable simply because its parent pipeline is:

```text
DLQ
```

MVP target:

```text
semantic REJECTED
→ NON_REPLAYABLE
→ explicit typed conflict / HTTP 409 at public API
```

Equivalent naming is allowed.

Do NOT implement a new revalidation workflow in Round 7.

A future explicit:

```text
REVALIDATE
```

operation may be designed post-MVP.

Projection/cleanup failures that are genuinely replayable must continue to replay.

---

# C — Historical Rollback / Replay Authorization

Historical mutation administration must not require the originating session to remain:

```text
ACTIVE
```

A closed historical session is still durable evidence of:

```text
tenant
workspace
dataset
agent
principal/session ownership
```

Required authorization concept:

```text
authenticated principal
+
historical session access/ownership
+
correct tenant/dataset scope
+
explicit dataset ROLLBACK permission
```

must authorize supported historical rollback/replay operations.

Do not weaken authorization.

Removing the ACTIVE requirement MUST NOT remove:

```text
principal identity
tenant scope
workspace/dataset scope
session ownership/access
ROLLBACK permission
```

REJECTED replay remains denied regardless of permission.

---

# D — Revision Hash Semantics

Three identities must remain conceptually distinct:

```text
1. declared whole-revision content hash

2. manifest hash over the frozen source-chunk manifest

3. individual source-chunk content hash
```

A first chunk's SHA-256 MUST NOT silently become the declared whole-revision hash.

Preferred semantic naming:

```text
declared_content_hash
manifest_hash
source_chunk.content_hash
```

Equivalent implementation is allowed if public/storage semantics are unambiguous.

---

# Declared Revision Hash

Explicit revision creation may accept a caller-declared whole-revision SHA-256.

Direct chunk/memory insertion without a declared whole-revision hash must NOT fabricate one from the first chunk.

Use:

```text
NULL / absent / explicit unknown
```

or another truthful representation.

Do not calculate a whole-document hash unless the complete declared document bytes are actually available under a defined canonicalization contract.

---

# Hash Migration

If schema changes are required:

```text
migration must preserve existing databases
```

Do not blindly reinterpret historical values as stronger evidence than they are.

Historical `content_hash` rows may have mixed provenance due to previous direct-insert semantics.

Choose a migration strategy that is explicit and safe.

Do not fabricate certainty.

---

# E — Public vs Physical Catalog Identity

Public IDs are tenant-scoped client-facing identifiers.

Physical IDs are storage-private identifiers.

Hard boundary:

```text
external/public ID
≠
internal physical ID
```

for newly created catalog identities.

New physical IDs MUST be:

```text
server-generated
opaque
not derived from user authority
```

UUID/UUID7/ULID or equivalent is sufficient.

Do not build a new identity service.

---

# Resolver Rule

The public resolver must resolve:

```text
tenant + kind + external_id
→ physical_id
```

It must NOT accept an internal physical ID merely because the caller supplied it in the external/public ID field.

Internal physical IDs are not public aliases.

---

# Legacy Compatibility Rule

Do NOT perform a broad rewrite of every existing legacy physical ID merely to make historical rows cosmetically opaque.

Existing mappings may remain internally if:

```text
public lookup is external-ID based

internal IDs are not accepted as public aliases

internal IDs are not exposed in public responses

authorization remains public/tenant scoped
```

A destructive global catalog-ID rewrite is out of scope unless current code proves it is strictly necessary for correctness.

---

# Public Response Boundary

Public API, SDK and MCP surfaces must not leak storage-private:

```text
workspace physical IDs
dataset physical IDs
document physical IDs
revision physical IDs
chunk physical IDs
```

where the contract expects public identifiers.

Do not only patch one known endpoint.

Audit:

```text
mutation status
pipeline-run payloads
sessions
documents
revisions
chunks
catalog/list responses
rollback/replay responses
SDK DTOs
MCP results
```

Use explicit response translation/whitelisting where practical.

---

# Round 7 Explicit Scope

In scope:

```text
manifest-finalization activation reconciliation

REJECTED non-replayable semantics

historical rollback/replay authorization

revision declared hash semantics

source-chunk vs manifest hash separation

required schema migration

new opaque physical ID generation

public resolver authority boundary

public physical-ID leak sweep

API/SDK/MCP contract updates directly required

bounded lifecycle/catalog documentation

adversarial regression tests
```

---

# Explicitly Deferred to Round 8

Do NOT pull these into Round 7:

```text
backup consistency/quiescence

SQLite synchronous durability contract

V3 cold-start compatibility
```

---

# Explicitly Deferred Post-MVP

Do not start:

```text
MemoryDAO full repository split

MCP ToolRegistry redesign

SDK V3/V4 architecture rewrite

domain/legal plugin migration

experimental package reorganization

benchmark package split

broad dead-code cleanup

new IAM service

microservices

event bus
```

---

# Non-Goals

Do not add:

```text
new lifecycle framework

new workflow engine

REVALIDATE subsystem

new catalog microservice

global historical physical-ID rewrite

hash service

distributed transaction layer

new authorization system
```

Reuse current MESA primitives.

---

# Agent Roles

## Gemini

Primary implementation agent.

Owns:

```text
R701-R711
```

Allowed statuses:

```text
BUILT
ALREADY_FIXED_VERIFIED
BLOCKED_ENV
```

Gemini may not issue the final Round 7 verdict.

---

## Terra

Independent falsifier and repairer.

Must independently reproduce Gemini's claims.

May add:

```text
TERRA-R701
TERRA-R702
...
```

Allowed final task states:

```text
VERIFIED
BLOCKED_ENV
```

Terra may not issue the Round 7 code verdict.

---

## Sol

Final adversarial certifier.

Owns:

```text
R712
```

May add:

```text
SOL-R701
SOL-R702
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

for Round 7.

Do not use:

```text
MVP_FULLY_VERIFIED
```

---

# Commit Policy

Every independent root-cause repair:

```text
reproduce
↓
smallest coherent fix
↓
mutation-killing regression
↓
focused verification
↓
ledger evidence
↓
commit
```

Good examples:

```text
fix(lifecycle): reconcile revision activation on manifest freeze

fix(lifecycle): reject semantic replay of rejected mutations

fix(auth): authorize historical rollback without active session

fix(catalog): separate declared revision and chunk hashes

fix(catalog): generate opaque physical identities

fix(api): prevent physical catalog id leakage
```

Avoid giant mixed commits and trivial micro-commits.

---

# Resource Safety

Do not automatically:

```text
download Qwen
download Magibu
call paid providers
rewrite real user databases
pytest -n auto
run destructive migration tests on real storage
```

Use temporary databases and deterministic fixtures.

---

# Round 7 Completion Meaning

Round 7 succeeds only when:

```text
late manifest freeze can complete activation correctly

REJECTED cannot masquerade as replayable

historical authorized rollback/replay works after session closure

revision/chunk/manifest hash semantics are truthful

new internal catalog IDs are opaque

internal physical IDs cannot be used as public aliases

public surfaces do not leak physical catalog identities

Round 4–6 critical contracts remain green
```

Round 7 completion does NOT certify recovery, durability or full MVP release readiness.