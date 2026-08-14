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
Evidence: Terra traced activation to the source-chunk manifest and closed the duplicate-child/missing-chunk bypass.
Tests: tests/test_terra_round3_regressions.py; tests/test_d001_d002_aggregate_state.py
Commit: 0d59cdc

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
Commit: 2c5cff6

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
Evidence: request_pipeline_rollback rejects revisions that are not the document ACTIVE head before compensation.
Tests: tests/test_d003_d004_rollback_hash.py
Commit: 2c5cff6

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
Evidence: content_hash is no longer overwritten by manifest construction; finalized manifests reject drift.
Tests: tests/test_d003_d004_rollback_hash.py
Commit: 2c5cff6

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
Evidence: forward migration fe5f6a7b8c9d creates the partial head index; Terra made postflight require it and updated the rebuild head.
Tests: tests/test_migration_closure.py -k 'previous_head_upgrades or fresh_upgrade or postflight_requires'
Commit: 0d59cdc

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
Evidence: local MiniLM example is 384 and Tier-3 examples remain commented.
Tests: tests/test_d007_d008_d009_composition_catalog.py
Commit: 0005d46

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
Evidence: V4 catalog primary keys are now deterministic tenant-scoped opaque IDs while names/external refs remain logical scope data.
Tests: tests/test_d007_d008_d009_composition_catalog.py; tests/test_terra_round3_regressions.py
Commit: 0d59cdc

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
Evidence: MCP recall/context and HTTP context now preserve valid_at, valid_from and valid_to through the canonical client/core path.
Tests: tests/test_d010_d011_d012_parity_bounded_hygiene.py
Commit: 0d59cdc

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
Evidence: recall/session caches use locked bounded LRU+TTL; adaptive routing state uses bounded locked LRU+TTL.
Tests: tests/test_d010_d011_d012_parity_bounded_hygiene.py; tests/test_terra_round3_regressions.py
Commit: 0d59cdc

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
Evidence: Terra retained CI SBOM coverage and direct dependency/run-server hygiene changes after inspection.
Tests: tests/test_d010_d011_d012_parity_bounded_hygiene.py; tests/test_deployment_assets.py
Commit: d715cff

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

Status: TODO
Evidence:
Tests:
Commit:

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
