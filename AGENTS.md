# MESA MVP — Certification Round 8 Agent Contract

## Active Round

Certification Round 8:

> Recovery + Durability + V3 Compatibility

Active branch:

```text
mvp/certification-round-8-recovery-durability-compat
```

Round 7 certified baseline commit:

```text
704298257b53b4af4cd1055453599aed58b981e7
```

Gemini, Terra and Sol MUST work on the same Round 8 branch.

Do not implement production changes directly on `main`.

---

# Source of Truth

For Round 8:

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
   = historical implementation/migration evidence
```

Do not treat an old audit statement as current fact until reproduced.

---

# Round 8 Goal

Close exactly three bounded MVP risk areas:

```text
A. Backup consistency / quiescence authority

B. SQLite durability contract

C. V3 cold-start compatibility
```

Round 8 is NOT a storage rewrite.

Round 8 is NOT a disaster-recovery platform.

Round 8 is NOT a V3 modernization project.

---

# Frozen Round 4–7 Baseline

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

normal extraction count = 1

REBEL absent from canonical V4

canonical SQL truth independent from graph

EmbeddingService canonical ownership

embedding-space identity fencing

generation rebuild/cutover

fact/assertion semantic retrieval

tenant-scoped RBAC

RBAC physical-schema validation

ContextBuilder untrusted evidence boundary

strict ContextBuilder token budget

bounded provenance rendering

late manifest activation reconciliation

semantic REJECTED is non-replayable

historical rollback/replay authorization

declared revision / manifest / chunk hash separation

opaque new catalog physical IDs

physical IDs are not public aliases

public physical-ID leakage boundary
```

Round 8 changes must not weaken these contracts.

---

# A — Backup Consistency Authority

## Historical Risk

The historical backup path could trust a caller-supplied declaration equivalent to:

```text
stores_stopped=True
```

without independently proving that the relevant storage state was actually quiescent/safe.

A caller-controlled boolean is NOT sufficient proof of backup consistency.

---

# Backup Hard Invariant

A backup may be reported as:

```text
consistent / successful
```

only if consistency is established by the backup/storage implementation itself.

Valid strategies may include, depending on current architecture:

```text
actual verified store lifecycle state

existing write/quiesce lock

existing coordinated store stop/close primitive

SQLite's supported consistent backup/snapshot primitive

another already-present repository primitive with equivalent proof
```

Do not invent a distributed snapshot protocol.

---

# Caller Declaration Rule

A value such as:

```text
stores_stopped=True
```

may be retained only as:

```text
hint
compatibility input
precondition request
```

if required for API compatibility.

It MUST NOT be the sole authority that permits an unsafe raw copy.

---

# Canonical vs Derived Data

Respect the already-certified architecture:

```text
canonical SQL truth
≠
derived vector projection
≠
derived graph projection
```

If backup semantics intentionally treat derived stores as rebuildable rather than canonical, that must be explicit and tested.

Do not promote vector or graph state into a second canonical truth merely to simplify backup.

---

# Backup Consistency Scope

The implementation must explicitly answer:

```text
What is included?

What is authoritative?

What must be quiescent?

What may be rebuilt?

When is a backup declared complete?
```

Do not leave these semantics implicit in a caller boolean.

---

# Backup During Writes

An active-write backup must either:

```text
obtain a safe snapshot/quiescent state
```

or:

```text
fail closed / refuse the unsafe backup
```

It must not silently produce an artifact claimed to be consistent while writes are racing with raw filesystem copies.

---

# Backup Completion

If the current backup implementation uses:

```text
temporary directory
manifest
completion marker
final rename
```

preserve or improve its completion semantics.

An interrupted/failed backup must not be indistinguishable from a successfully completed backup.

Do not create a new backup format unless necessary.

---

# Restore Proof

Round 8 backup verification must include bounded restore/readback evidence.

At minimum prove that backed-up canonical state can be reopened and yields expected data.

Do not turn Round 8 into a full disaster-recovery product.

---

# B — SQLite Durability Contract

## Historical Risk

The historical SQLite configuration used:

```text
PRAGMA synchronous=NORMAL
```

for canonical storage connections without a sufficiently explicit production durability contract.

For MESA canonical SQL truth, production durability behavior must be deliberate rather than an accidental driver default.

---

# Production Durability Target

For canonical production/default SQLite writes:

```text
synchronous = FULL
```

or an existing repository setting with equivalent or stronger durability semantics is required.

Do not claim stronger guarantees than SQLite/filesystem/hardware can provide.

The contract is:

```text
MESA requests SQLite's durable FULL synchronization behavior
for canonical production writes.
```

---

# Test / Development Profiles

A bounded test/development profile may use a weaker mode such as:

```text
NORMAL
```

only when explicitly selected through the repository's existing environment/profile/config pattern.

Do NOT let test performance configuration silently become production default.

---

# Connection Coverage

Durability policy must apply to every relevant canonical write-capable SQLite connection.

Audit:

```text
main MemoryDAO connection

RBAC database if separately SQLite-backed

catalog/migration connections

worker/background canonical write connections

admin/rebuild paths that write canonical SQL

other production SQLite connection factories
```

Do not mechanically force unrelated read-only or temporary test databases unless the contract requires it.

---

# Centralization

If an existing SQLite connection/configuration helper exists:

```text
reuse it
```

If current PRAGMA configuration is duplicated, a small shared helper is acceptable.

Do NOT introduce:

```text
new database abstraction framework

new ORM

new connection pool architecture
```

solely for Round 8.

---

# Journal Mode

Do not change:

```text
WAL
DELETE
TRUNCATE
```

journal mode globally merely because Round 8 concerns durability.

Preserve existing journal-mode semantics unless current code proves they violate the required contract.

Round 8's known target is synchronization durability, not a journal-mode redesign.

---

# SQLite Verification

Verify actual effective runtime PRAGMA values on opened production-profile connections.

Configuration source alone is not proof.

Required equivalent:

```text
PRAGMA synchronous
→ expected durable mode
```

after the production connection is initialized.

---

# Commit / Reopen Semantics

Use temporary databases to prove:

```text
committed canonical data
→ close/reopen
→ still present
```

and:

```text
uncommitted transaction
→ rollback/connection loss
→ not falsely durable
```

Do not simulate filesystem/hardware failure with unsafe destructive host operations.

---

# C — V3 Cold-Start Compatibility

## Historical Risk

Historical V3/legacy cold-start logic, including HybridRetriever-related startup/counting behavior, could reach V4-specific artifact/catalog assumptions.

A valid legacy/V3 database must not crash at cold start simply because V4-only schema/artifacts do not exist yet.

---

# V3 Compatibility Target

Given an actual supported V3/pre-V4 database:

```text
cold start
↓
legacy/V3 initialization
↓
V3 retrieval path
```

must not require V4-only tables merely to determine startup/retriever state.

---

# No V4 Table Assumption

A V3 compatibility path must not unconditionally query:

```text
V4 artifact tables
V4 catalog tables
V4-only lifecycle tables
```

before capability/schema presence is established.

Use the smallest truthful capability/schema check or V3-native query.

---

# No Silent V4 Bootstrap Side Effect

Do not "fix" V3 cold start by silently creating unrelated V4 schema during a read/retriever startup path unless that is already the documented migration contract.

Compatibility should not mutate a legacy database merely to satisfy a count operation.

---

# V3 Retrieval Preservation

A supported V3 dataset/database must still retrieve expected legacy data after cold start.

The goal is:

```text
compatibility
```

not merely:

```text
no exception
```

---

# V4 Regression

Fixing V3 must not weaken or bypass:

```text
V4 catalog ownership

V4 tenant scope

V4 fact retrieval

V4 embedding-space fencing
```

---

# No V3 Rewrite

Do not:

```text
port all V3 code to V4

delete V3 adapters

rewrite HybridRetriever

build a compatibility service

duplicate V4 storage
```

Apply the smallest compatibility repair.

---

# Round 8 Explicit Scope

In scope:

```text
backup consistency authority

backup quiescence/snapshot verification

backup unsafe-concurrency failure behavior

bounded backup restore/readback proof

SQLite production durability policy

SQLite effective PRAGMA verification

coverage of canonical write connections

bounded commit/reopen durability tests

V3/pre-V4 cold-start reproduction

V3-native/capability-aware startup repair

V3 retrieval compatibility regression

fresh/current DB regression

previous-version/V3 fixture regression

directly required docs/tests
```

---

# Explicitly Deferred to Final MVP Certification

Do not pull these into Round 8 unless needed to reproduce a Round 8 defect:

```text
full fresh-install certification

full previous-release upgrade matrix

safe-core Docker E2E

model-enabled Docker/provider E2E

real Qwen compatibility smoke

real Magibu compatibility smoke

worker kill/reclaim certification

rollback/purge race certification

cross-tenant full-stack certification

temporal/current-history benchmark

multi-fact benchmark

full restart durability matrix

final GO / NO-GO
```

---

# Explicitly Deferred Post-MVP

Do not start:

```text
MemoryDAO decomposition

new backup service

distributed snapshots

new storage coordinator

new database abstraction

MCP ToolRegistry redesign

SDK V3/V4 architecture rewrite

domain plugin migration

experimental package restructuring

benchmark package cleanup

broad dead-code cleanup
```

---

# Agent Roles

## Gemini

Primary implementation agent.

Owns:

```text
R801-R811
```

Allowed statuses:

```text
BUILT
ALREADY_FIXED_VERIFIED
BLOCKED_ENV
```

Gemini may not issue the final Round 8 verdict.

---

## Terra

Independent falsifier and repairer.

May add:

```text
TERRA-R801
TERRA-R802
...
```

Allowed task statuses:

```text
VERIFIED
BLOCKED_ENV
```

Terra may not issue the Round 8 code verdict.

---

## Sol

Final adversarial certifier.

Owns:

```text
R812
```

May add:

```text
SOL-R801
SOL-R802
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

for Round 8.

Never use:

```text
MVP_FULLY_VERIFIED
```

---

# Commit Discipline

For every independent root-cause repair:

```text
reproduce
↓
smallest coherent fix
↓
mutation-killing regression
↓
focused verification
↓
ledger update
↓
coherent commit
```

Examples:

```text
fix(backup): verify quiescence before filesystem snapshot

fix(backup): fail closed on unsafe live-store copy

fix(sqlite): enforce durable production synchronous mode

fix(sqlite): apply durability policy to canonical connections

fix(v3): avoid v4 artifact dependency during cold start

test(round8): cover recovery durability compatibility contracts
```

Do not create one giant mixed commit.

Do not create trivial micro-commits.

---

# Control-File Tracking

Do not change `.gitignore` or `.agents/` tracking policy as part of Round 8 unless the active repository contract explicitly requires it.

Control-file governance cleanup is not a Round 8 production objective.

---

# Resource Safety

Do not automatically:

```text
download Qwen

download Magibu

call paid providers

destroy real databases

run crash tests against user storage

rewrite actual user backups

pytest -n auto
```

Use temporary databases/directories and deterministic fixtures.

---

# Round 8 Completion Meaning

Round 8 succeeds only when:

```text
backup consistency is implementation-proven, not caller-declared

unsafe live-copy behavior is impossible or fails closed

bounded backup restore/readback succeeds

canonical production SQLite uses explicit durable synchronization

effective runtime SQLite configuration is verified

test/dev weakening cannot silently become production default

V3/pre-V4 cold start no longer assumes V4-only schema

V3 retrieval remains functional

Round 4–7 critical contracts remain green
```

Round 8 success does NOT mean final MVP release certification is complete.