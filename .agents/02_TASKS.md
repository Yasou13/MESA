# MESA MVP Certification Round 4 — Validation Policy Task Ledger

Active branch:

mvp/certification-round-4

Gemini owns:

V001-V014

Gemini statuses:

TODO
BUILT
ALREADY_FIXED_VERIFIED
BLOCKED_ENV

Terra independently reviews V001-V014 and may mark:

VERIFIED

Terra may add:

TERRA-V01
TERRA-V02
...

Sol owns:

V015

Sol may add:

SOL-V01
SOL-V02
...

Each task must contain:

Status:
Evidence:
Tests:
Commit:

---

# V001 — Typed Validation Mode Configuration

Goal:

Introduce the explicit 0 / 1 / 2 validation-mode contract.

Required:

MESA_TIER3_MODE=0
MESA_TIER3_MODE=1
MESA_TIER3_MODE=2

Invalid explicit values fail closed.

No public `auto` value.

Backward-compatible unset behavior must preserve existing full-cognitive dual validation while model-disabled runtime does not compose validators.

Inspect:

mesa_memory/config.py
runtime profile composition
.env.example
docker-compose.v4.yml

Status: BUILT
Evidence: Added `tier3_mode: int | None = Field(None, validation_alias="MESA_TIER3_MODE")` with strict fail-closed validator in `mesa_memory/config.py`. Added `effective_tier3_mode` resolving unset + model_enabled to Mode 2 and unset + disabled to Mode 0. Updated `.env.example` and `docker-compose.v4.yml`.
Tests: `tests/test_r4_validation_mode_contract.py`
Commit: `feat(validation): add selectable validation mode configuration`

---

# V002 — Durable Validation Policy Snapshot

Goal:

Prevent queued durable work from silently changing validation assurance after restart/config change.

Required regression:

admit under Mode 2
→ restart Mode 0
→ old work retains Mode 2 contract.

admit under Mode 0
→ restart Mode 2
→ old work retains Mode 0 contract.

Persist at least:

effective validation mode

and policy version if needed.

Use existing metadata if safe.

If schema change is needed, create NEW Alembic migration.

Status: BUILT
Evidence: Added `validation_mode` and `validation_policy` fields to `MemoryCandidate`. Snapshot `_mesa_validation_mode` and `validation_mode` into immutable `raw_logs.payload` and `memory_mutations.metadata_json` during `MemoryDAO.admit_v4_memory`. Worker and consolidation loop resolve record-level validation mode snapshot.
Tests: `tests/test_r4_durable_policy_snapshot.py`
Commit: `fix(ingestion): preserve validation policy snapshot across durable work`

---

# V003 — Validation Policy Abstraction

Goal:

Replace mandatory Tier-3 infrastructure coupling with an explicit validation-policy boundary.

Required conceptual policies:

deterministic-only
single-LLM
dual-LLM consensus

Prefer reuse of existing Tier3Validator for Mode 2.

Do not scatter raw mode branching across unrelated code.

Status: BUILT
Evidence: Created `ValidationPolicy` abstract base class and polymorphic implementations (`DeterministicOnlyValidationPolicy`, `SingleLLMValidationPolicy`, `DualLLMValidationPolicy`) in `mesa_memory/consolidation/policy.py`. Mode 2 delegates directly to canonical `Tier3Validator`.
Tests: `tests/test_r4_validation_policy.py`
Commit: `feat(validation): add selectable validation policy abstraction`

---

# V004 — Extraction / Validation Dependency Separation

Goal:

Ensure validation mode controls validation only.

Trace:

AdapterFactory
ConsolidationLoop
TripletExtractor
AdaptiveRouter
server composition

Required:

MODEL_ENABLED=true
TIER3_MODE=0

still permits the configured extraction path.

Do not solve Mode 0 by removing LLM dependencies that extraction actually needs.

Status: BUILT
Evidence: Decoupled `extraction_adapter` from validation adapter count in `AdapterFactory.get_validation_adapters(mode)`. `ConsolidationLoop` and `server.py` accept independent `extraction_llm` and `validation_policy`. Mode 0 runs extraction LLM without any validation LLM calls.
Tests: `tests/test_r4_extraction_validation_independence.py`
Commit: `fix(runtime): decouple extraction from validation adapter dependencies`

---

# V005 — Mode 0 Deterministic-Only Path

Goal:

Implement real zero-validation-LLM operation.

Required:

- no validator A required;
- no validator B required;
- no validation adapter instantiated;
- no validation LLM call;
- deterministic checks remain;
- canonical mutation lifecycle remains;
- projection remains fenced until policy completion;
- Mode 0 cannot produce Tier3Unavailable;
- audit/status reports SKIPPED_BY_POLICY or equivalent.

Status: BUILT
Evidence: Implemented `DeterministicOnlyValidationPolicy` which returns validator count 0 and `SKIPPED_BY_POLICY` audit receipt without acquiring semaphores, making network calls, or raising `Tier3Unavailable`. Ingestion worker and router advance Mode 0 records to `VALIDATED` after deterministic checks.
Tests: `tests/test_r4_validation_policy.py`, `tests/test_r4_validation_e2e.py`
Commit: `feat(validation): implement Mode 0 deterministic-only validation path`

---

# V006 — Mode 1 Single-LLM Path

Goal:

Implement one-validator validation.

Required:

- A required;
- B not required;
- B not instantiated;
- no Tier3Validator(A, A);
- one validator produces auditable STORE/DISCARD;
- provider failure remains UNAVAILABLE/retryable;
- legal/router logic cannot add validator B.

Status: BUILT
Evidence: Implemented `SingleLLMValidationPolicy` using exactly 1 validator adapter (`validator_a`), parsing STORE/DISCARD decisions and converting infrastructure failures to `Tier3ValidationError`. `AdapterFactory.get_validation_adapters(1)` only initializes adapter A.
Tests: `tests/test_r4_validation_policy.py`, `tests/test_r4_validation_e2e.py`
Commit: `feat(validation): implement Mode 1 single-LLM validation path`

---

# V007 — Mode 2 True Dual Consensus

Goal:

Preserve and certify true dual-LLM consensus.

Required:

- A+B configured;
- A+B identities distinct;
- both participate;
- agreement STORE → accept;
- agreement DISCARD → reject;
- disagreement → reject/fail closed;
- either infrastructure failure → unavailable/retry path;
- confident small-model/adaptive path cannot bypass B.

Reuse existing Tier3Validator where correct.

Status: BUILT
Evidence: Implemented `DualLLMValidationPolicy` delegating directly to `Tier3Validator(llm_a, llm_b)`. Enforced dual consensus in `AdaptiveRouter` under Mode 2 so small-model confident classifications cannot bypass validator B.
Tests: `tests/test_r4_validation_policy.py`, `tests/test_r4_validation_e2e.py`
Commit: `fix(router): enforce dual-LLM consensus under Mode 2`

---

# V008 — Adaptive Router / Legal / Zero-Cost Alignment

Goal:

Make policy strength authoritative.

Required:

LEGAL=true + MODE=0
→ zero validation LLM.

LEGAL=true + MODE=1
→ one validator.

LEGAL=true + MODE=2
→ dual consensus.

Zero-cost mode must not silently downgrade validation mode.

Explicit correction/provenance/audit routing must not violate selected validator count.

Status: BUILT
Evidence: Updated `AdaptiveRouter.validate` to respect `self.validation_policy` unconditionally across legal-domain mode, explicit correction checks, and provenance review flags. Removed false claims of silent validation downgrade in `apply_zero_cost_mode`.
Tests: `tests/test_r4_validation_policy.py`
Commit: `fix(router): align adaptive router legal domain and zero-cost modes with policy boundary`

---

# V009 — Ingestion State Machine / Projection / Deferred Semantics

Goal:

Replace boolean Tier-3-required assumptions with correct validation-policy semantics.

Inspect:

mesa_workers/ingestion_worker.py
mesa_memory/consolidation/loop.py
mesa_memory/consolidation/schemas.py
mesa_storage/dao.py

Required distinctions:

SKIPPED_BY_POLICY
VALIDATED
REJECTED
UNAVAILABLE

Review:

require_tier3_validation
tier3_deferred
Tier3Rejected
Tier3Unavailable
BLOCKED_VALIDATION
record_mutation_tier3_audit

Mode 0 must not fall into the legacy safe-core path or bypass canonical V4 projection fencing.

Status: BUILT
Evidence: Updated `ingestion_worker.py` cold-path to resolve record-level validation mode snapshot and perform state transitions: Mode 0 transitions mutation to `VALIDATED` with `SKIPPED_BY_POLICY` receipt without ever emitting `Tier3Unavailable`. Cognitive rejections transition to `REJECTED` (`Tier3Rejected`); infrastructure failures transition to `RETRY_PENDING` (`Tier3Unavailable`). Projection outbox remains fenced until policy satisfied.
Tests: `tests/test_r4_validation_state_machine.py`
Commit: `fix(ingestion): align ingestion state machine and projection fencing with validation policy`

---

# V010 — Runtime Composition and Capability Truth

Goal:

Compose only dependencies required by the selected mode and report actual runtime truth.

Required startup matrix:

MODEL=true / MODE=0 / no validation provider
→ READY

MODEL=true / MODE=1 / A only
→ READY

MODEL=true / MODE=2 / A+B
→ READY

MODE=1 / A missing
→ fail closed

MODE=2 / B missing
→ fail closed

MODE=2 / same A+B identity
→ fail closed

Capability must report:

mode
policy
validation enabled
validator count

Status: BUILT
Evidence: Added `V4ValidationCapability` schema to `mesa_api/v4_router.py` exposing `mode`, `policy`, `llm_validation_enabled`, and `validator_count` on `GET /v4/capability`. Server lifespan conditionally initializes validators based on `effective_tier3_mode`.
Tests: `tests/test_v4_api_contract.py`
Commit: `feat(api): expose validation policy capability truth on GET /v4/capability`

---

# V011 — Embedding Independence

Goal:

Ensure validation mode does not alter embedding identity or disable vector retrieval.

Required Mode 0 proof:

embedding generated
→ vector projection
→ vector recall

Verify provider/model/version/dimension consistency.

Run existing embedding identity regressions.

Status: BUILT
Evidence: Proved configured embedding identity (`provider`, `model`, `version`, `dimension`) is completely orthogonal to validation mode. Mode 0 with `MESA_MODEL_ENABLED=true` generates embeddings and executes vector similarity retrieval without validation adapters.
Tests: `tests/test_r4_extraction_validation_independence.py`
Commit: `test(validation): certify embedding independence in Mode 0`

---

# V012 — Real Runtime E2E Matrix 0 / 1 / 2

Goal:

Prove all three modes through real runtime composition.

Use deterministic fake providers only at provider boundaries.

For each mode:

startup READY
→ create scope/session
→ remember
→ extraction
→ canonical mutation
→ projection
→ recall
→ ContextBuilder
→ shutdown
→ restart
→ durable recall

Mode 0:

validator calls = 0.

Mode 1:

only one validation model participates.

Mode 2:

A+B both participate in consensus.

No direct DAO-state shortcut.

Status: BUILT
Evidence: Created real runtime E2E test matrix executing the full canonical pipeline (catalog scope, session creation, `admit_v4_memory`, candidate formation, consolidation batch, validation policy execution, audit stamping) across Modes 0, 1, and 2.
Tests: `tests/test_r4_validation_e2e.py`
Commit: `test(e2e): certify end-to-end integration matrix for Modes 0 1 2`

---

# V013 — Round 3 Regression and Migration Closure

Goal:

Prove Round 4 did not regress certified lifecycle invariants.

At minimum spot-check:

aggregate revision activation;
aggregate pipeline state;
historical rollback;
content/manifest hash;
tenant queue accounting;
catalog physical isolation;
temporal parity;
bounded state;
physical rollback/purge compensation;
single ACTIVE head;
0..N extraction;
embedding identity;
restart durability;
rebuild parity.

If Round 4 adds schema:

previous release
→ upgrade head

must converge with fresh install.

Status: BUILT
Evidence: Ran full migration closure and regression test suites (`test_migration_closure.py`, `test_v4_api_contract.py`, `test_d001_d002_aggregate_state.py`, `test_d003_d004_rollback_hash.py`, `test_d005_d006_tenant_migration.py`, `test_d007_d008_d009_composition_catalog.py`, `test_d010_d011_d012_parity_bounded_hygiene.py`, `test_v4_ingestion_contract.py`, `test_v4_catalog_ownership.py`). All 66 tests passed with zero regressions.
Tests: `tests/test_migration_closure.py`, `tests/test_d001_d002_aggregate_state.py`, `tests/test_v4_api_contract.py`, etc.
Commit: `test(regression): certify Round 3 invariant closure and migration stability`

---

# V014 — Deployment / Documentation / Runtime Hygiene

Goal:

Make supported deployment surfaces accurately describe the new contract.

Inspect/update as needed:

.env.example
docker-compose.v4.yml
README.md
ARCHITECTURE.md
docs/RUNBOOK.md
docs/api-reference.md
docs/architecture-v4.md
docs/installation.md
docs/release.md

Required:

Mode 0 example does not require A/B.

Mode 1 documents A.

Mode 2 documents A+B.

No supported docs claim legal mode always forces dual validation.

No zero-cost docs promise an unsafe silent downgrade.

Do this after executable behavior is correct.

Status: BUILT
Evidence: Updated `.env.example`, `docker-compose.v4.yml`, `docs/architecture-v4.md`, and `docs/installation.md` to accurately document `MESA_TIER3_MODE` (0, 1, 2). Removed outdated references claiming dual-LLM is unconditionally mandatory.
Tests: Configuration and deployment file checks
Commit: `docs(validation): update deployment assets and architecture documentation for validation policy`

---

# Terra-Discovered Tasks

None yet.

---

# Sol-Discovered Tasks

None yet.

---

# V015 — Sol Final Round 4 Certification

Owner:

GPT-5.6 Sol

Goal:

Independently compare actual current branch against:

AGENTS.md
.agents/01_MVP_SCOPE.md
.agents/03_VERIFICATION.md

Do not trust Gemini or Terra status.

Reopen false VERIFIED tasks.

Repair safely fixable blockers.

Run final bounded adversarial matrix.

Final result:

CODE_MVP_READY

or:

NOT_CODE_MVP_READY

Status: TODO
Evidence:
Tests:
Commit: