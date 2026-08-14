# MESA MVP Certification Round 3 — Delta Task Ledger

Gemini owns D001-D012.

Gemini statuses:

TODO
BUILT
ALREADY_FIXED_VERIFIED
BLOCKED_ENV

Terra independently reviews and may mark:

VERIFIED

Sol owns D013.

Keep each task compact:

Status:
Evidence:
Tests:
Commit:

---

# D001 — Aggregate Revision Activation Barrier

Goal:

Prevent revision ACTIVE state before every required child mutation/chunk succeeds.

Required regression:

one revision with at least three required children.

Child A -> COMMITTED
Child B -> RETRY_PENDING
Child C -> DEAD_LETTER or equivalent failure

Expected:

revision remains non-ACTIVE.

Then repair/complete all required children.

Expected:

revision becomes ACTIVE exactly once and remains the single document head.

Inspect actual expected-child manifest/work-set semantics.

Status: VERIFIED
Evidence: Sol added an explicit fail-closed manifest freeze barrier, rejected vacuous/partial work sets, and restored strict child lifecycle transitions.
Tests: tests/test_d001_d002_aggregate_state.py; tests/test_terra_round3_regressions.py; tests/test_p0_canonical_correction.py
Commit: 9b9203d

---

# D002 — Aggregate Pipeline State

Goal:

Prevent individual child mutations from directly declaring a multi-child pipeline COMMITTED.

Choose explicitly:

A. enforce exactly one mutation per pipeline;

or

B. recompute parent state from all child mutations.

Prefer B if multiple-child pipelines are already supported.

Required:

COMMITTED iff all required children COMMITTED.

Test mixed states and later failure/retry behavior.

Status: VERIFIED
Evidence: Parent state is recomputed from all child mutation states on each CAS transition.
Tests: tests/test_d001_d002_aggregate_state.py
Commit: 9b9203d

---

# D003 — Descendant-Aware Historical Rollback

Goal:

Prevent rollback of a non-head historical revision from reactivating an older predecessor beneath a newer current head.

Required test:

R1 ACTIVE
-> R2 ACTIVE supersedes R1
-> R3 ACTIVE supersedes R2

Attempt rollback of pipeline producing R2.

Expected:

typed 409 conflict.

R3 remains ACTIVE.

No R1 reactivation.

Status: VERIFIED
Evidence: request_pipeline_rollback permits safe PENDING rollback but rejects historical non-head rollback through a typed API 409 conflict.
Tests: tests/test_d003_d004_rollback_hash.py; tests/test_v4_api_contract.py
Commit: 9b9203d

---

# D004 — Separate Content Hash and Manifest Hash

Goal:

Preserve immutable identity semantics.

Do not overwrite caller-declared content hash with chunk manifest hash.

Required:

- declared content hash remains stable;
- manifest hash evolves only while PENDING;
- finalization freezes manifest;
- ACTIVE revision rejects chunk mutation;
- idempotent create retry remains valid after manifest construction.

Status: VERIFIED
Evidence: content_hash remains caller identity while manifest_hash freezes independently; finalized manifests reject drift.
Tests: tests/test_d003_d004_rollback_hash.py
Commit: 9b9203d

---

# D005 — Canonical Tenant-Wide V4 Queue Accounting

Goal:

Use real tenant identity in `admit_v4_memory()` and all corresponding queue/journal/receipt/accounting rows.

Required regression:

same tenant T
agent A
agent B

Combined usage exceeds tenant quota.

Agent B cannot bypass quota.

Telemetry/rows store T as tenant_id.

Status: VERIFIED
Evidence: canonical admission and dispatch journal/queue/receipt now carry tenant_id, and quota queries use tenant_id.
Tests: tests/test_d005_d006_tenant_migration.py
Commit: 73f7d4a

---

# D006 — Immutable Alembic Upgrade Closure

Goal:

Fix ACTIVE-head invariant through a NEW migration rather than mutated historical migration content.

Required:

- new migration at current head;
- detect existing duplicate ACTIVE heads;
- deterministic repair or explicit fail-safe;
- add partial unique ACTIVE index;
- include invariant in blocking schema/postflight validation;
- previous-release -> current-head upgrade regression.

Fresh and upgraded schemas must converge.

Status: VERIFIED
Evidence: Historical 9a1 migration semantics are restored; forward migrations repair duplicate heads, create the invariant, freeze only provably terminal manifests, and add tenant-scoped physical identity.
Tests: tests/test_migration_closure.py
Commit: 9b9203d

---

# D007 — Fresh-Install Embedding / Tier-3 Config Contract

Goal:

Remove config drift.

Required:

- `.env.example` local MiniLM dimension = 384;
- code/default/sample values agree;
- Tier-3 provider/model A/B commented examples present;
- configuration validation remains coherent;
- no misleading 1536 MiniLM default.

Status: VERIFIED
Evidence: local MiniLM example uses runtime-recognized provider/model variables at dimension 384 and includes coherent commented Tier-3 A/B profiles.
Tests: tests/test_d007_d008_d009_composition_catalog.py
Commit: 9b9203d

---

# D008 — Deterministic Model-Enabled Full-Cognitive E2E

Goal:

Prove full runtime composition without requiring paid providers.

Use deterministic fake/local provider injected through the real composition contract.

Minimum test:

build/compose or equivalent runtime composition
-> model-enabled startup
-> READY
-> create scope/session
-> remember event
-> extraction
-> canonical mutation
-> projection
-> recall
-> context
-> restart
-> recall same durable memory.

Model-disabled smoke is insufficient.

Status: VERIFIED
Evidence: Terra runs the production combined-runtime lifespan with model_enabled=true; only AdapterFactory/REBEL provider boundaries are deterministic fakes. The durable dispatch, consolidation, mutation ledger, projection, retrieval, ContextBuilder and restart paths are real.
Tests: tests/test_d008_model_enabled_runtime_e2e.py
Commit: 9d85905

---

# D009 — Multi-Tenant Catalog Physical Identity

Goal:

Prevent global physical PK collision/squatting from client-visible scoped IDs.

Preferred approach:

server-generated opaque physical IDs with scoped external refs/names.

Required regression:

Tenant A and Tenant B may both use equivalent natural external identifiers without collision or leakage.

Preserve API compatibility where practical.

Status: VERIFIED
Evidence: A compatibility identity table maps tenant-scoped public IDs to distinct physical catalog keys and translates public lifecycle receipts back to logical IDs.
Tests: tests/test_d007_d008_d009_composition_catalog.py; tests/test_terra_round3_regressions.py
Commit: 9b9203d

---

# D010 — HTTP / SDK / MCP Temporal Parity

Goal:

Expose the same supported temporal query contract across public transports.

Required fields:

valid_at
valid_from
valid_to

Verify:

SDK sync
SDK async
MCP recall
HTTP V4

all forward/validate equivalent semantics.

Status: VERIFIED
Evidence: HTTP, sync/async SDK and MCP recall/context preserve valid_at, valid_from and valid_to through serialized requests.
Tests: tests/test_d010_d011_d012_parity_bounded_hygiene.py; tests/test_mcp_v4_service.py; tests/test_p0_http_sdk_mcp_convergence.py
Commit: f87c0f7

---

# D011 — Bounded Long-Lived Runtime State

Goal:

Bound known process-level maps/caches.

At minimum inspect:

- MCP recall cache;
- MCP session locks;
- adaptive router routing state.

Required:

- TTL/expiry;
- max entries;
- eviction/pruning;
- concurrency safety.

Tests must prove entries disappear or are evicted under bounded load.

Status: VERIFIED
Evidence: recall/session caches use bounded LRU+TTL, adaptive routing uses locked LRU+TTL, and keyed session locks never evict active/waiting scopes or leak canceled waiters.
Tests: tests/test_d010_d011_d012_parity_bounded_hygiene.py; tests/test_terra_round3_regressions.py
Commit: f87c0f7

---

# D012 — Release / Runtime Hygiene Closure

Goal:

Close remaining low-cost supported-runtime/release hazards.

Required review/fix:

- full-cognitive main image security/SBOM gate;
- directly imported runtime dependencies declared directly;
- stale `scripts/run_server.py` removed or made canonical thin wrapper;
- search score higher/lower semantics corrected;
- stale/deprecated supported surfaces accurately marked.

Do not broaden into general cleanup.

Status: VERIFIED
Evidence: CI scans and emits an SBOM for the shipped full-cognitive image; run_server is a thin canonical launcher; score semantics and public support docs are accurate.
Tests: tests/test_d010_d011_d012_parity_bounded_hygiene.py; tests/test_deployment_assets.py; tests/test_ci_coverage_contracts.py
Commit: f87c0f7; 242870e

---

# Sol-Discovered Tasks

## SOL-D01 — Freeze Required Revision Work

Status: VERIFIED
Evidence: PENDING manifests require explicit finalization; activation requires a nonempty frozen chunk set and one committed child per chunk.
Tests: tests/test_d001_d002_aggregate_state.py; tests/test_p0_canonical_correction.py
Commit: 9b9203d

## SOL-D02 — Restore Legal Aggregate State Transitions

Status: VERIFIED
Evidence: Pre-projection and terminal-failure mutations can no longer jump directly to COMMITTED; parent state is recomputed from every child.
Tests: tests/test_d001_d002_aggregate_state.py; tests/test_terra_round3_regressions.py
Commit: 9b9203d

## SOL-D03 — Close Rollback and Upgrade Boundaries

Status: VERIFIED
Evidence: Non-head rollback is a typed HTTP 409 and the previous-release upgrade proof starts without the active-head index and repairs duplicates forward.
Tests: tests/test_d003_d004_rollback_hash.py; tests/test_v4_api_contract.py; tests/test_migration_closure.py
Commit: 9b9203d

## SOL-D04 — Separate Tenant Public and Physical Catalog IDs

Status: VERIFIED
Evidence: Identical workspace/dataset/document/revision/chunk IDs coexist across tenants with distinct physical keys and logical API receipts.
Tests: tests/test_d007_d008_d009_composition_catalog.py
Commit: 9b9203d

## SOL-D05 — Make Process Bounds Cancellation-Safe

Status: VERIFIED
Evidence: Session lock capacity cannot evict an active scope and canceled waiters are pruned; circuit failures retain retryable backend semantics.
Tests: tests/test_d010_d011_d012_parity_bounded_hygiene.py
Commit: f87c0f7

## SOL-D06 — Gate the Shipped Runtime Image

Status: VERIFIED
Evidence: The main runtime image now has vulnerability and CycloneDX SBOM gates, and the stale server composition root delegates to the canonical app.
Tests: tests/test_deployment_assets.py; tests/test_ci_coverage_contracts.py
Commit: f87c0f7

---

# D013 — Sol Final Delta Certification

Owner:

GPT-5.6 Sol

Goal:

Independently compare current code against:

`.agents/01_MVP_SCOPE.md`

Reopen any false VERIFIED status.

Repair safely fixable blockers.

Run final bounded adversarial matrix.

Final status:

CODE_MVP_READY

or:

NOT_CODE_MVP_READY

If CODE_MVP_READY:

Status: FINAL_VERIFIED

Otherwise do not mark FINAL_VERIFIED.

Status: FINAL_VERIFIED
Evidence: Sol independently reopened false D001/D002/D003/D006/D007/D009/D011/D012 closures, repaired the code-level blockers, and verified a single migration head plus clean lint, format, compile, layer, type, and diff gates.
Tests: 59 focused D001-D012 tests; 100 prior-invariant, V3 compensation/ownership, deployment, and CI contract tests.
Commit: 9b9203d; f87c0f7; 242870e

---

# Terra-Discovered Tasks

Append only clear Round 3 certification blockers:

TERRA-D01
TERRA-D02
...

Use:

Status:
Evidence:
Tests:
Commit:

---

# Sol-Discovered Tasks

Append only clear final certification blockers:

SOL-D01
SOL-D02
...

Use:

Status:
Evidence:
Tests:
Commit:
