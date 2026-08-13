# MESA MVP — Certification Round 3 Agent Contract

## 1. Mission

The repository is now in MESA MVP Certification Round 3.

This is a DELTA CERTIFICATION pass.

Do NOT start another broad architecture audit.

Do NOT reopen already-closed areas without concrete executable evidence.

The objective is to close the remaining known code/release blockers discovered after Certification Round 2 and produce the strongest defensible code-level MVP candidate.

The primary unresolved areas are:

- aggregate revision completeness;
- aggregate pipeline completeness;
- historical rollback safety;
- revision hash identity;
- canonical tenant-wide queue accounting;
- immutable Alembic upgrade closure;
- fresh-install embedding/config contract;
- deterministic model-enabled full-cognitive E2E;
- multi-tenant physical catalog identity;
- HTTP/SDK/MCP temporal parity;
- bounded long-lived process state;
- release/runtime hygiene.

---

# 2. Source of Truth

Use this evidence hierarchy:

1. current user instruction;
2. actual executable source code;
3. database schema and migration history;
4. runtime composition/configuration;
5. executable tests that prove the real invariant;
6. `.agents/` certification contract;
7. audit reports, documentation, README, comments and previous AI conclusions.

The two August 14 regression reports are inputs.

They are NOT proof.

Gemini, Terra and Sol must independently verify whether each finding still exists in current code.

---

# 3. Mandatory Reading

Before modifying code read:

1. `AGENTS.md`
2. `.agents/00_RULES.md`
3. `.agents/01_MVP_SCOPE.md`
4. `.agents/02_TASKS.md`
5. `.agents/03_VERIFICATION.md`

Then inspect only the production paths needed for the current certification tasks.

Do not bulk-read historical reports or the complete docs tree.

---

# 4. Frozen Architecture

The intended MVP architecture remains:

V3 compatibility
        \
         -> Canonical MESA Core
        /
V4 native

Canonical durable truth:

SQL / mutation ledger / canonical catalog lifecycle.

Derived projections:

- vector;
- graph.

V3 must not become a second independent authoritative memory engine.

---

# 5. New Central Invariant — Aggregate Completeness

Certification Round 3 introduces a hard invariant:

A parent object must not expose terminal success before its required children have collectively satisfied the success contract.

This applies especially to:

- document revisions;
- pipeline runs.

For a revision containing several chunks/mutations:

one child COMMITTED

does NOT imply:

revision ACTIVE.

For a pipeline containing several mutations:

one child COMMITTED

does NOT imply:

pipeline COMMITTED.

Parent terminal state must represent the entire logical unit.

---

# 6. Revision Activation Contract

Preferred MVP contract:

PENDING revision
-> source/chunk manifest frozen
-> expected child work known
-> all required child mutations successful
-> aggregate revision barrier
-> ACTIVE

A failed/retrying required child keeps the revision non-ACTIVE.

A document must have exactly one ACTIVE head.

Concurrent corrections must fail closed through CAS/head protection.

---

# 7. Pipeline State Contract

Pipeline state must either:

A. be derived from all child mutations;

or

B. schema/service must enforce exactly one mutation per pipeline.

Do not retain an ambiguous model.

If multiple child mutations are permitted, no child may directly declare the entire pipeline COMMITTED.

Recommended semantics:

COMMITTED:
all required children committed.

PROJECTING:
at least one required child is actively projecting and no terminal failure prevents success.

RETRY_PENDING:
retryable children remain.

BLOCKED / DEAD_LETTER:
required child has terminally failed according to policy.

ROLLING_BACK / ROLLED_BACK:
controlled only by lifecycle operations.

---

# 8. Historical Rollback Contract

Rollback must be document-head aware.

MVP behavior:

If the revision created by the target pipeline is not the current document head because a newer descendant exists:

reject rollback deterministically.

Preferred response:

409 NON_HEAD_ROLLBACK_CONFLICT

Do not reactivate an old predecessor underneath a newer ACTIVE descendant.

Explicit descendant cascade/rebase semantics are post-MVP.

---

# 9. Revision Hash Contract

Do not overload one hash field with multiple identities.

Declared document/content hash and computed source-chunk manifest hash represent different concepts.

They must remain distinct.

A caller-provided immutable content hash must not later be overwritten by a manifest hash.

Manifest identity must freeze at revision finalization.

---

# 10. Tenant Accounting Contract

Canonical V4 queue accounting must use the real tenant identity.

Never substitute:

agent_id

for:

tenant_id.

The following must agree:

- admission quota;
- dispatch journal;
- dispatch queue;
- receipts;
- telemetry/audit;
- queue usage.

Two agents belonging to one tenant share the tenant quota.

Creating another agent must not bypass tenant-wide admission limits.

---

# 11. Migration Immutability

Released Alembic revisions are immutable historical artifacts.

Do not fix upgrade behavior by editing an already-released migration.

If a critical index/constraint was previously added by mutating historical migration content:

restore historical migration semantics where practical and add a NEW Alembic revision at current head.

Upgrade safety must be proven from a previous released schema.

Fresh install and upgraded install must converge on the same critical schema invariants.

---

# 12. Embedding / Fresh Install Contract

The default local embedding profile must be internally consistent.

If the default model is:

sentence-transformers/all-MiniLM-L6-v2

the corresponding default dimension must be:

384

unless runtime-probed identity defines another valid configuration.

`.env.example` must not override a correct code default with an incompatible value.

Tier-3/provider examples must accurately represent the supported full-cognitive profile.

---

# 13. Full-Cognitive Certification Contract

Packaging/config existence is not sufficient.

Round 3 requires a deterministic model-enabled full-composition test.

It does NOT require a paid provider.

A deterministic fake/local test provider may be used if it exercises the real runtime composition.

The test must prove:

full image/runtime composition
-> startup READY
-> session/catalog setup
-> remember/event
-> extraction
-> 0..N admission
-> projection
-> recall
-> context
-> restart
-> same durable memory retrievable

Model-disabled smoke is not proof of model-enabled composition.

---

# 14. Catalog Physical Identity

Client-visible scoped identifiers must not cause cross-tenant global-PK collisions.

Preferred MVP direction:

- server-generated globally unique physical IDs;
- caller-provided stable names/external refs scoped by tenant/workspace/dataset.

If existing client-provided IDs remain, physical identity must still prevent one tenant from reserving another tenant's natural identifier namespace.

Do not perform an unnecessarily huge schema rewrite if a safe compatibility layer can solve the problem.

---

# 15. Temporal Transport Parity

HTTP, SDK and MCP must express the same supported temporal query contract.

If HTTP supports:

- valid_at;
- valid_from;
- valid_to;

SDK and MCP must expose and forward the same semantics.

Avoid transport-specific narrowing of canonical retrieval capability.

Prefer one shared validation/request model where practical.

---

# 16. Long-Lived Process Bounds

Long-running process state must not grow without bound.

Review:

- MCP recall cache;
- MCP session locks;
- adaptive routing state;
- similar scope-indexed dictionaries touched by Round 3.

Use bounded TTL/LRU or equivalent bounded structures.

Required characteristics:

- max entries;
- expiration;
- eviction/pruning;
- safe concurrency;
- observable size where practical.

Do not build distributed cache infrastructure for MVP.

---

# 17. Release Hygiene

Round 3 also closes low-cost release hazards that directly affect supported runtime/developer confidence.

Relevant items include:

- main full-cognitive image vulnerability/SBOM coverage;
- explicitly declared direct runtime dependencies;
- stale `scripts/run_server.py` composition root;
- misleading search score semantics;
- supported/deprecated surface clarity.

Do not turn this into a broad cleanup project.

---

# 18. Already-Closed Areas

Do not broadly redesign previously closed areas unless Round 3 changes expose a regression.

Previously closed/high-confidence areas include:

- physical rollback/purge compensation;
- 0..N extraction foundation;
- single ACTIVE head CAS foundation;
- experimental cognitive worker isolation;
- V3 reverse compensation;
- V3 supported single-writer topology;
- V3 idempotency/secret validation;
- MCP scoped idempotency IDs;
- O(N) cold-start count;
- rebuild parity;
- readiness backlog visibility;
- storage-root/config refresh improvements.

Regression-test them where Round 3 changes interact with them.

Do not rewrite them for aesthetics.

---

# 19. No Hallucinated Completion

The following are not sufficient evidence:

- a task marked VERIFIED;
- a class/function name;
- comments;
- README;
- passing test that mocks away the critical boundary;
- fresh-schema migration test when the defect is upgrade-only;
- one committed child when parent aggregation is under test;
- model-disabled Docker smoke when model-enabled composition is under test.

Trace the actual invariant.

---

# 20. Root-Cause Changes

For every task:

1. locate the real implementation;
2. locate callers;
3. identify state/storage ownership;
4. reproduce or prove the issue;
5. search for reusable primitives;
6. make the smallest coherent root-cause repair;
7. add adversarial regression coverage;
8. run bounded verification;
9. commit;
10. continue.

Do not stop for user approval between tasks.

---

# 21. Git Contract

No implementation agent may work on:

main

or:

master.

Gemini creates/uses:

mvp/certification-round-3

Terra continues on the same branch.

Sol continues on the same branch.

Do not create Terra/Sol branches.

Do not merge into main.

Do not force-push main.

Preserve unrelated user changes.

---

# 22. Commit Discipline

Commit coherent repairs independently.

Examples:

fix(lifecycle): enforce aggregate revision activation

fix(lifecycle): derive pipeline state from child mutations

fix(revision): reject non-head historical rollback

fix(catalog): separate content and manifest identity

fix(queue): enforce tenant-wide v4 accounting

fix(migration): add active-head invariant migration

fix(config): align local embedding example

test(runtime): certify model-enabled composition

fix(api): align temporal transport contract

fix(runtime): bound process caches

chore(release): remove stale runtime surface

Do not create one giant Round 3 commit.

---

# 23. Machine Safety

Do NOT automatically run:

- uncontrolled full pytest;
- pytest -n auto;
- 24-hour soak;
- sustained load;
- large research benchmark;
- large model downloads;
- paid provider benchmarks;
- destructive production migration;
- giant Docker topology.

Use focused bounded tests.

A deterministic fake/local provider is preferred for full-cognitive composition certification.

---

# 24. Agent Roles

Gemini:

implements D001-D012.

Allowed status:

- BUILT
- ALREADY_FIXED_VERIFIED
- BLOCKED_ENV

Terra:

independently verifies and repairs D001-D012.

Allowed independent status:

VERIFIED

Sol:

owns D013 final delta certification.

Sol may reopen any prior task.

Sol must repair safely fixable blockers before final decision.

---

# 25. Final Status

Only Sol may return:

CODE_MVP_READY

or:

NOT_CODE_MVP_READY

CODE_MVP_READY requires no known unresolved code-level Round 3 blocker.

MVP_FULLY_VERIFIED requires external runtime/release evidence including the user-run production validation gates.

Do not confuse the two.

---

# 26. Completion Principle

Round 3 is complete when:

- revision success is aggregate;
- pipeline success is aggregate or structurally single-child;
- historical rollback cannot corrupt current head;
- hash identity is stable;
- tenant queue accounting is truly tenant-wide;
- upgrade migration safety is correct;
- fresh-install config is coherent;
- model-enabled composition has deterministic E2E evidence;
- catalog physical identity is multi-tenant safe;
- temporal transport parity exists;
- long-lived state is bounded;
- critical release/runtime drift is closed.

After this, stop static-audit cycling and move to dependency-complete external certification.