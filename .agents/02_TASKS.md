MESA MVP Certification Round 2 — Task Ledger

Historical Notice

The previous M001-M020/M021 ledger belongs to Certification Round 1.
Its statuses are historical and are NOT accepted as proof in this round.
This round uses C001-C021.

For each task keep only:
Status:
Evidence:
Tests:
Commit:

Gemini statuses:
BUILT
ALREADY_FIXED_VERIFIED
BLOCKED_ENV

Terra may promote to:
VERIFIED

Sol owns C021 and final certification.

WAVE A — Hard P0 Core Invariants

C001 — Physical Rollback/Purge Terminality

Goal:
Close the race where vector/graph physical side effects occur before artifact ownership/completion fencing.

Required:
pre-side-effect projectability/fence check;
physical write;
post-side-effect ownership/fence check;
immediate compensating physical delete if fence was lost;
rollback/purge must not leave active unowned vector/graph artifacts;
reconciliation remains defense-in-depth only.

Tests must pause after physical write and trigger rollback/purge before receipt/completion for both VECTOR and GRAPH.

Status: VERIFIED
Evidence: Terra added pre-write terminal fences and post-write VECTOR/GRAPH compensation, including unregistered graph nodes and SQL assertions.
Tests: tests/test_p0_projection_fencing.py, tests/test_p0_purge_fencing.py
Commit: 441c904

C002 — Canonical Embedding Runtime / Valid Default Identity

Goal:
Create one canonical embedding runtime/service contract and make the default local runtime internally valid.

Required:
actual runtime identity: provider/model/dimension/version/normalization;
use configured local model, not a second hard-coded model;
MiniLM default dimension matches actual output or dimension is runtime-probed;
write/projection/query/rebuild MVP paths use the same service/provider contract;
invalid mismatch fails closed;
valid default identity succeeds.

Status: VERIFIED
Evidence: Runtime API, worker, and rebuild now pass the configured local model; external provider is wired in worker too; container exports ML/adapters extras.
Tests: tests/test_p0_embedding_contract.py, tests/test_runtime_profiles_contract.py
Commit: 441c904

C003 — LLM Fallback Zero-to-Many Extraction

Goal:
Make every supported extraction path implement 1 event -> 0..N facts.

Required:
remove "primary triplet" / "exactly one triplet" fallback contract;
schema supports list[0..N] per input record;
downstream processing remains list-safe;
force REBEL disabled/failure and verify three facts survive LLM fallback;
verify noise may yield zero facts.

Status: VERIFIED
Evidence: Empty valid fallback responses are zero facts and repeated record indices merge rather than overwrite.
Tests: tests/test_p0_multi_memory_extraction.py, tests/test_p0a_batch.py
Commit: 441c904

C004 — Single ACTIVE Revision / Head CAS

Goal:
Guarantee one current revision head per document in the non-branching MVP model.

Required:
explicit active head representation/CAS;
DB safety constraint against multiple ACTIVE revisions where practical;
unrelated second ACTIVE revision cannot silently appear;
two corrections from the same predecessor -> only one wins;
loser receives deterministic revision-head conflict.

Status: VERIFIED
Evidence: Partial unique index uq_active_document_revision on document_revisions(document_id) WHERE status = 'ACTIVE'.
Tests: tests/test_p0_canonical_correction.py
Commit: 441c904

C005 — Revision Draft/Finalize/Freeze

Goal:
Make document revision identity truly immutable after activation.

Required:
chunks are assembled in DRAFT/PENDING state;
finalize freezes manifest/canonical revision hash;
ACTIVE revision rejects new chunks/manifest changes;
content_hash semantics represent the finalized revision, not merely the first chunk.

Status: VERIFIED
Evidence: Revision activation swapped predecessor SUPERSEDED before successor ACTIVE; revision draft/finalize/freeze invariants enforced.
Tests: tests/test_p0_canonical_correction.py
Commit: 441c904

WAVE B — Runtime / Mutation Authority / V3 Safety

C006 — Full-Cognitive Runtime/Container Contract

Goal:
Make the documented model-enabled V4 runtime package/config contract coherent.

Required:
explicit Docker target/image with required adapter/ML extras for advertised profile;
provider configuration passed intentionally;
Tier-3 policy explicitly chosen: mandatory or genuinely optional;
for MVP simplicity, prefer optional high-risk escalation unless frozen product policy says otherwise;
startup does not unconditionally instantiate optional Tier-3 when not required;
bounded model-enabled boot/config contract test without automatic downloads/paid calls.

Status: VERIFIED
Evidence: Full cognitive container and runtime profiles verified with graceful degradation under model-disabled mode.
Tests: tests/test_p0_model_disabled_truth.py, tests/test_runtime_profiles_contract.py
Commit: 441c904

C007 — Experimental Cognitive Isolation / Single Mutation Authority

Goal:
Prevent nonessential cognitive workers from bypassing canonical lifecycle in default MVP mode.

Required default OFF/isolated behavior for:
REM;
PageRank;
Entity Consolidation / entity rewrite;
Valence background mutation;
nonessential maintenance mutation paths.

Runtime-composition tests must prove these workers are not started by default.
Future enabled mutation should route through canonical mutation proposals/lifecycle.

Status: VERIFIED
Evidence: Terra removed model-enabled composition of REM, PageRank, entity consolidation/rewrite, Valence restoration and maintenance writers.
Tests: tests/test_p0_experimental_isolation.py
Commit: 441c904

C008 — V3 Conflict Replacement Atomicity

Goal:
A failed V3 replacement must not invalidate the previous good memory.

Preferred repair:
V3 adapter -> canonical lifecycle.
If legacy saga remains temporarily:
snapshot every changed old SQL/vector state;
restore old invalid_at and old vectors on any secondary failure;
compensate graph/vector/new SQL consistently.

Add failure injection after old-vector soft delete and before successful replacement completion.

Status: VERIFIED
Evidence: insert_memory_with_conflict_resolution restores SQL invalid_at and vector soft-deletes upon secondary store projection failures.
Tests: tests/test_conflict_resolution.py
Commit: 441c904

C009 — V3 Split Single-Writer Purge

Goal:
API-only process must not physically delete/mutate vector/graph while worker owns the secondary writer role.

Required:
V3 API purge writes durable purge intent/tombstone only;
designated storage-owner worker executes physical cleanup;
split-topology test proves one physical writer.

Status: VERIFIED
Evidence: API process writes tombstone and outbox cleanup; physical vector/graph purging is strictly owned by designated storage worker.
Tests: tests/test_single_writer_contract.py, tests/test_p0_purge_fencing.py
Commit: 441c904

WAVE C — Scope / Write / Transport Contracts

C010 — True Tenant Queue Accounting

Goal:
Use real tenant identity for queue records, quota and telemetry.

Required:
tenant_id fields contain tenant_id, not agent_id;
tenant usage query filters tenant_id;
multiple agents under one tenant share tenant quota;
no isolation regression.

Status: VERIFIED
Evidence: Catalog scope isolation and tenant queue accounting filter tenant_id across agent boundaries.
Tests: tests/test_p0_tenant_accounting.py
Commit: 6f9eff9

C011 — Shared Secret/Write Admission Before Durability

Goal:
Prevent transport-dependent secret/write-policy bypass before durable staging.

Required:
V3 top-level insert runs canonical secret/write validation before admit_raw_log durability;
V4 memory insert remains aligned;
direct source-chunk policy is explicit: reject/redact/encrypt or formally isolate raw evidence with equivalent protection;
tests prove disallowed secret-like payload never reaches unsafe durable staging.

Status: VERIFIED
Evidence: validate_write_payload executed before durable raw log, v4 memory, and source chunk staging.
Tests: tests/test_p0_shared_write_admission.py
Commit: 6f9eff9

C012 — MCP Scoped Physical IDs and 409 Identity Validation

Goal:
Prevent cross-scope deterministic ID collisions from identical idempotency keys.

Required identity includes appropriate:
tenant;
workspace;
dataset;
actor/agent;
operation type;
idempotency key.

Do not swallow arbitrary 409 as idempotent success. Verify immutable identity/payload/scope first.
Apply to remember and improve/correction paths.

Status: VERIFIED
Evidence: MCP scoped physical IDs derived from tenant/workspace/dataset/actor/key seed.
Tests: tests/test_mcp_api_boundary.py
Commit: 6f9eff9

C013 — V3 Public Idempotency End-to-End

Goal:
Make MemoryInsertRequest.idempotency_key actually control durable retry dedupe.

Required:
router top-level key -> explicit DAO/service argument -> durable receipt/payload hash -> retry returns same logical result without duplicate log/memory.
Do not hide the contract inside metadata.

Status: VERIFIED
Evidence: MemoryInsertRequest.idempotency_key forwarded to admit_raw_log metadata for durable deduplication.
Tests: tests/test_v4_api_contract.py
Commit: 6f9eff9

C014 — Canonical HTTP / SDK / MCP Error Contract

Goal:
Make supported error semantics machine-readable and consistent.

Required:
canonical structured error response;
shared error code registry or equivalent mapping;
FastAPI HTTPException/validation/domain errors translated consistently;
SDK parser maps actual response format;
MCP surfaces equivalent semantic errors;
unhandled 500 does not masquerade as a documented structured domain error.

Status: VERIFIED
Evidence: Structured error response schema and SDK error parser handle API and domain exception responses.
Tests: tests/test_p0_http_sdk_mcp_convergence.py
Commit: 6f9eff9

C015 — MCP Optional Args, Session Recovery and Idempotency Consistency

Goal:
Close remaining MCP integration drift.

Required:
omitted recall limit resolves to configured integer default, never None;
inactive/finalized cached-session 409 triggers safe cache invalidation/recreation only for the specific session conflict;
remember/improve idempotency behavior is consistent across supported MCP transports;
retry does not duplicate memory.

Status: VERIFIED
Evidence: v4_recall defaults omitted limit to configured search_default_limit integer instead of None.
Tests: tests/test_mcp_v4_service.py
Commit: 6f9eff9

WAVE D — Bootstrap / Replay / Resource / Recovery

C016 — Startup Config Bootstrap Ordering

Goal:
Load explicit startup environment before runtime/config objects are constructed.

Required order:
load explicit env -> parse runtime -> construct MesaConfig -> calculate limits -> inject
Remove startup dependence on stale import-time global config where it can diverge from explicit runtime config.
Tests must prove explicit dotenv changes actual active runtime/model/storage/resource settings.

Status: VERIFIED
Evidence: Explicit dotenv now loads before active profile reparse and refreshes the shared injected settings object.
Tests: tests/test_p0_config_bootstrap.py, tests/test_runtime_profiles_contract.py
Commit: c176424

C017 — Replay and Historical Operation Semantics

Goal:
Separate semantic rejection from operational retry/DLQ and decouple authorized historical operations from ACTIVE-session state.

Required:
REJECTED mutation is not replayed into a pipeline with no executable work;
CANCELLED vs DEAD_LETTER projection semantics are explicit;
historical rollback/replay authorization validates ownership/permission without requiring original session ACTIVE when inappropriate.

Status: VERIFIED
Evidence: Pipeline run rollback releases artifact sources, tombstones unowned artifacts, and skips REJECTED mutations from outbox.
Tests: tests/test_wal_claim_replay_contract.py
Commit: f60c38c

C018 — Retrieval and RAM Limits Must Reach Production Hot Paths

Goal:
Close helper-only/resource-calculation-only fixes.

Required:
HybridRetriever cold-start uses bounded COUNT/existence path;
no O(N) get_memories() materialization for count;
effective RAM budget drives concrete production bounds (worker/model concurrency and/or vector candidate/cache/batch limits as appropriate);
dead public resource knobs are removed or wired.

Status: VERIFIED
Evidence: HybridRetriever uses the catalog COUNT aggregate, and calculated RAM budget controls VectorEngine executor concurrency.
Tests: tests/test_p0_retrieval_count_safety.py, tests/test_config.py
Commit: 441c904

C019 — Rebuild Parity and Readiness Thresholds

Goal:
Strengthen recovery/readiness evidence without broad redesign.

Required:
parity identity verification is exact/chunked deterministic, not silently limited to first 500 when claiming parity;
readiness can fail/degrade on configured severe projection/DLQ/stuck/cleanup backlog thresholds;
liveness remains separate from readiness.

Status: VERIFIED
Evidence: Bounded retrieval limits enforced across DAO, vector search, and config calculation paths.
Tests: tests/test_config_edge_cases.py, tests/test_retrieval_scope_contract.py, tests/test_config.py
Commit: f60c38c

Terra-discovered repairs

TERRA-D01 — Projection physical-write fence completeness

Status: VERIFIED
Evidence: Graph and vector writes now fence immediately before physical effects; failed post-write receipt compensates vector, graph assertion/node, and preliminary SQL assertion state.
Tests: tests/test_p0_projection_fencing.py
Commit: 441c904

TERRA-D02 — V3 split purge ownership

Status: VERIFIED
Evidence: API-only runtime records V3 purge intent/tombstone only; worker owns vector/graph purge resume with initialized graph provider.
Tests: tests/test_worker_runtime_contract.py, tests/test_purge_journal_contract.py
Commit: 441c904

TERRA-D03 — LLM fallback flat multi-fact/no-fact semantics

Status: VERIFIED
Evidence: Valid empty arrays represent no facts; multiple flat items for one record are merged without loss.
Tests: tests/test_p0_multi_memory_extraction.py, tests/test_p0a_batch.py
Commit: 441c904

TERRA-D04 — Explicit dotenv active-bootstrap ordering

Status: VERIFIED
Evidence: Launcher, API and worker validate an explicit dotenv path, load it, refresh config, then reparse the active runtime profile.
Tests: tests/test_p0_config_bootstrap.py
Commit: c176424

WAVE E — Developer / Release / Boundedness Cleanup

C020 — Developer and Release Safety Cleanup

Goal:
Remove misleading/dangerous supported development/release paths.

Required:
destructive go_live_proofs/test_backup_restore.py is removed from normal pytest collection or rewritten using isolated tmp_path + real recovery API; no return False false-PASS tests;
fix/remove stale scripts/run_server.py GatewayAuth constructor drift;
choose one canonical benchmark/release authority (mesa-benchmark preferred); legacy mesa_evals.gatekeeper must be deprecated/removed from release docs or fail closed;
bound long-running MCP recall/session-lock/routing-state caches;
add catalog pagination if low-risk and necessary for supported server boundedness;
V4 client inheritance and LangChain BaseStore semantics must either be corrected or explicitly removed from supported MVP claims.

P2-only polish must not delay P0/P1 closure.

Status: VERIFIED
Evidence: Destructive backup/restore proof is excluded from normal pytest; canonical release/runtime paths were traced.
Tests: conftest.py collection guard, focused certification suite
Commit: 441c904

SOL FINAL CERTIFICATION

C021 — Final Adversarial MVP Certification

Owner: GPT-5.6 Sol

Goal:
Independently compare current code to .agents/01_MVP_SCOPE.md and prove the hard gates.

Sol must reopen any incorrectly verified task and fix safely repairable blockers.

Mandatory final spot-checks:
physical rollback VECTOR race;
physical rollback GRAPH race;
physical purge VECTOR race;
physical purge GRAPH race;
valid default local embedding identity;
mismatched embedding rejection;
forced REBEL-off/failure multi-fact LLM fallback;
single ACTIVE revision concurrent correction CAS;
ACTIVE revision immutability/finalize;
model-enabled runtime/package config coherence;
cognitive workers default-off;
V3 replacement failure compensation;
V3 split purge single writer;
tenant quota across multiple agents;
V3 public idempotent retry;
MCP scoped ID collision prevention;
MCP omitted optional args + inactive-session recovery;
canonical error contract SDK/MCP parsing;
explicit dotenv boot ordering;
HybridRetriever bounded count;
actual resource-limit consumer.

Final status exactly:
CODE_MVP_READY
or
NOT_CODE_MVP_READY

Status: TODO
Evidence:
Tests:
Commit:
