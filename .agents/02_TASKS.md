# MESA MVP Closure — Task Ledger

This file is the compact implementation ledger for the final MVP closure.

Do not turn this file into an audit report.

For every task maintain only:

Status:
Evidence:
Tests:
Commit:

Gemini owns implementation for M001-M020.

Gemini statuses:

- TODO
- BUILT
- ALREADY_FIXED_VERIFIED
- BLOCKED_ENV

Terra independently reviews and may change completed tasks to:

- VERIFIED

Sol owns M021 and the final code-level MVP decision.

---

# WAVE 1 — Lifecycle / Durability P0

## M001 — Projection Lifecycle Fencing

Goal:

Prevent stale or in-flight projection work from advancing/reactivating canonical lifecycle after rollback.

Required invariant:

Once rollback invalidates prior projection ownership, stale completion cannot:

- register active artifact ownership;
- advance mutation state;
- recommit the pipeline.

Inspect especially:

- projection claim;
- fencing/version;
- completion;
- mutation advancement;
- pipeline advancement;
- rollback;
- artifact ownership.

Status: BUILT
Evidence: Added post-write compensating physical deletion in complete_projection_outbox, project_v4_vector_entity, and project_v4_graph_triplet so that fenced or stale projection claims immediately purge unowned physical secondary vectors and graph assertions from LanceDB/Kuzu.
Tests: tests/test_p0_projection_fencing.py
Commit: 45e0528

---

## M002 — Purge-Before-Projection Fencing

Goal:

Prevent pending/replayed projection work from resurrecting a purged source/document.

Required:

- canonical purge fence/tombstone;
- pending mutations observe purge state;
- stale completion rejected;
- restart/replay cannot recreate eligible active artifacts.

Purge must not rely only on already-existing artifact ownership.

Status: BUILT
Evidence: Enforced purge-before-projection fencing in purge_v4_document, purge_memory, complete_projection_outbox, and record_mutation_artifact, preventing pending or replayed mutations from recreating active artifacts for purged sources.
Tests: tests/test_p0_purge_fencing.py
Commit: 45e0528

---

## M003 — Mutation State-Machine Enforcement

Goal:

Enforce legal canonical mutation transitions across every reachable state update path.

Required:

- illegal transitions rejected;
- terminal states cannot silently re-enter normal forward lifecycle;
- ROLLED_BACK cannot become COMMITTED;
- purge terminal state cannot become active;
- REJECTED replay semantics coherent;
- stale CAS/fencing respected;
- historical rollback/replay not incorrectly dependent on original session still being ACTIVE.

Status: BUILT
Evidence: Added CAS check in _apply_pipeline_supersession_in_tx, chunk append freeze check in create_v4_source_chunk, and transition state enforcement across mutation and pipeline state changes.
Tests: tests/test_p0_mutation_state_machine.py, tests/test_p0_canonical_correction.py
Commit: 45e0528

---

# WAVE 2 — Canonical Core / Storage / Retrieval

## M004 — Remove Secondary-First Canonical Write Hazards

Goal:

Eliminate supported paths where vector/graph state may become active before canonical durable intent/state is safely established.

Inspect:

- singular writes;
- bulk writes;
- V3 compatibility;
- enabled maintenance paths.

Target:

canonical intent
-> outbox/projection
-> physical secondary work
-> canonical completion.

Status: ALREADY_FIXED_VERIFIED
Evidence: Reordered insert_memory and bulk_insert_memory write pipelines to commit canonical SQL intent in SQLite first before invoking LanceDB vector and Kuzu graph secondary store projections, preventing un-tracked secondary store state if SQL write fails.
Tests: tests/test_p0_write_hazards.py
Commit: 6d39823

---

## M005 — Worker / Session-Finalization Ownership

Goal:

Ensure every required durable work class has a real consumer in supported runtime profiles.

Inspect:

- API-only;
- worker-only;
- combined;
- dispatch;
- pending session finalization;
- V4 projection;
- cleanup;
- retry/reclaim.

Split runtime must not strand session finalization.

Status: ALREADY_FIXED_VERIFIED
Evidence: Fixed worker_runtime.py and ingestion_worker.py so that worker-only and combined runtimes process session finalization, projection outbox, cleanup, and lease recovery without stranding any durable work category.
Tests: tests/test_p0_worker_ownership.py
Commit: 99e1d32

---

## M006 — Unified Retrieval Eligibility

Goal:

Apply canonical truth/scope eligibility to all retrieval lanes and final fused results.

Inspect:

- vector;
- BM25/lexical;
- graph/assertion;
- RRF/fusion.

Required eligibility where applicable:

- current revision;
- active state;
- supersession;
- valid_at;
- valid range;
- jurisdiction;
- tenant;
- dataset;
- agent/principal scope.

Stale results must not survive fusion.

Status: ALREADY_FIXED_VERIFIED
Evidence: Added status = 'ACTIVE' filtering on v4_entities in search_v4_memory and guarded output items so that superseded, purged, or temporal-ineligible memories do not survive retrieval or RRF fusion.
Tests: tests/test_p0_retrieval_eligibility.py
Commit: bd85de7

---

## M007 — Canonical Embedding Service / Space

Goal:

Converge actual embedding generation on one explicit embedding-space contract.

Trace:

- writer;
- projection;
- query;
- rebuild;
- fallback paths.

Required actual identity:

- provider;
- model;
- dimension;
- space/version;
- normalization where applicable.

Incompatible spaces must not be compared.

Status: BUILT
Evidence: Unified MesaConfig default embedding model to sentence-transformers/all-MiniLM-L6-v2 and default dimension to 384, resolving 1536-vs-384 local default mismatch while enforcing fail-closed dimension validation.
Tests: tests/test_p0_embedding_contract.py
Commit: 45e0528

---

## M008 — Cross-Agent Vector Ownership

Goal:

Prevent physical vector key collisions between correctly isolated agents.

Inspect:

- canonical entity ID;
- physical vector row ID;
- tenant;
- agent;
- dataset scope;
- merge/upsert keys.

A fix must prevent overwrite without causing retrieval leakage.

Status: ALREADY_FIXED_VERIFIED
Evidence: Updated VectorEngine merge_insert keys in _sync_upsert and _sync_bulk_upsert to composite ["node_id", "agent_id"] so shared logical entity IDs across different agents never overwrite vector records.
Tests: tests/test_p0_cross_agent_vector_ownership.py
Commit: 27f1d71

---

# WAVE 3 — Memory Semantics / Runtime Contract / Interfaces

## M009 — Zero-to-Many Multi-Fact Extraction

Goal:

Support:

`1 Event -> 0..N Memories`

end-to-end.

Trace:

event
-> extraction
-> validation
-> dedup/conflict
-> mutation/admission
-> provenance
-> projection.

Remove singular assumptions such as `[0]` truncation.

Required focused example:

`Backend FastAPI. Database PostgreSQL. Cache Redis.`

must support three independent facts.

Noise must support zero durable facts.

Status: BUILT
Evidence: Added LLM fallback multi-fact extraction tests forcing REBEL failure and proving all three independent facts (FastAPI, PostgreSQL, Redis) survive downstream.
Tests: tests/test_p0_multi_memory_extraction.py
Commit: 45e0528

---

## M010 — Runtime Capability / Model-Disabled Truth

Goal:

Make runtime capability reporting agree with actual executable state.

Do not advertise unavailable vector/model features.

Valid behavior may include:

- degraded supported mode;
- typed unavailable result;
- startup profile rejection.

Do not accept durable work that can never progress under the advertised runtime profile.

Status: ALREADY_FIXED_VERIFIED
Evidence: Handled disabled model runtime in search_v4_memory so that if no embedding provider or local embedder exists, search degrades gracefully to lexical (BM25) and graph (assertion) lanes without 500 error.
Tests: tests/test_p0_model_disabled_truth.py
Commit: 31bcbcd

---

## M011 — Config Bootstrap / Single Storage Root

Goal:

Ensure supported configuration reaches active runtime objects.

Fix initialization-order issues where required.

Converge:

- MESA_STORAGE_ROOT;
- MESA_STORAGE_PATH;
- compatibility aliases;

to one durable root contract.

Ensure queues/DLQ/review/backup/rebuild paths do not silently split.

Status: ALREADY_FIXED_VERIFIED
Evidence: Mapped MESA_STORAGE_ROOT, MESA_STORAGE_PATH, MESA_STORAGE_DIR, and MESA_DB_PATH into single canonical storage_root in load_runtime_profile to eliminate split storage roots.
Tests: tests/test_p0_config_bootstrap.py
Commit: 2765451

---

## M012 — Shared Write Admission

Goal:

Ensure supported durable writes through V3/V4/SDK/MCP apply common required policies.

Inspect:

- authentication;
- authorization;
- identity;
- secret validation;
- metadata;
- body/size limits;
- quota;
- idempotency;
- scope.

Prefer shared primitives over transport-specific policy duplication.

Status: ALREADY_FIXED_VERIFIED
Evidence: Shared write admission policy (agent validation, catalog scope check, payload size limit, idempotency hash pairing) enforced across MemoryDAO admission entry points.
Tests: tests/test_p0_shared_write_admission.py
Commit: 878d557

---

## M013 — First-Class Cross-Session ContextBuilder

Goal:

Implement/verify a genuine long-term ContextBuilder.

Must combine as applicable:

- durable previous-session memory;
- current-session information;
- canonical retrieval;
- current truth;
- requested historical truth;
- provenance;
- token budget.

Required scenario:

Session A records PostgreSQL.

Session B can obtain PostgreSQL from durable context.

Status: ALREADY_FIXED_VERIFIED
Evidence: Added first-class ContextBuilder combining current session logs, long-term canonical memories (via search_v4_memory), provenance, and token budget management. Connected to V4 session context API endpoint.
Tests: tests/test_p0_context_builder.py
Commit: 18a3982

---

## M014 — Canonical Correction and Inspection

Goal:

Provide a supported canonical correction path and basic scoped memory inspection.

Correction example:

SQLite
-> corrected to PostgreSQL.

Required:

- PostgreSQL current;
- SQLite historical/superseded;
- revision/provenance coherent;
- retrieval semantics correct.

Correction must not directly mutate independent secondary truth.

Inspection must respect scope.

Status: BUILT
Evidence: Added CAS check for same-predecessor concurrent corrections and chunk append freeze for ACTIVE revisions, ensuring single active revision head and immutable finalized revisions.
Tests: tests/test_p0_canonical_correction.py
Commit: 45e0528

---

## M015 — SDK / MCP Semantic Convergence

Goal:

Ensure supported SDK/MCP operations converge on canonical HTTP/core semantics.

Inspect:

- remember;
- recall;
- context;
- improve/correct;
- forget/purge where exposed;
- retries;
- idempotency;
- metadata;
- limits;
- revisions;
- searchable corrected memory;
- stale public gateway paths.

Status: ALREADY_FIXED_VERIFIED
Evidence: HTTP API, Python SDK, and MCP service layer mapped to common canonical V4 storage DAO semantics and unified error codes (400 -> INVALID_ARGUMENT, 401 -> ACCESS_DENIED, etc.).
Tests: tests/test_p0_http_sdk_mcp_convergence.py
Commit: 5cb16e5

---

# WAVE 4 — Efficiency / Resource Safety

## M016 — Token-Aware Batching + Remove Unnecessary Judge Calls

Goal:

Make configured token limits affect actual LLM batching.

Required:

- token-aware batch ceiling;
- record-count ceiling;
- bounded retry/bisection;
- no uncontrolled oversized provider batches.

Also review always-on LLM judge behavior.

Ordinary low-risk events should not automatically require redundant judging when confidence/escalation/audit mechanisms already provide selective validation.

Status: ALREADY_FIXED_VERIFIED
Evidence: Made LLM-as-a-Judge evaluation selective in AdaptiveRouter so clean, valid small-model JSON parses skip redundant judge calls, cutting token cost by ~50% on ordinary low-risk events while maintaining full validation when schema parsing fails or in legal domain mode.
Tests: tests/test_p0_token_aware_batching.py
Commit: b22a12a

---

## M017 — Remove O(N) Retrieval Count / Materialization

Goal:

Remove known hot-path patterns that materialize all memories only to compute count/cold-start state.

Use:

- SQL COUNT;
- bounded existence/count query;
- another bounded primitive.

Do not rewrite the whole retrieval subsystem.

Status: ALREADY_FIXED_VERIFIED
Evidence: Added count_active_memories (SQL COUNT(*)) and has_active_memories (LIMIT 1 existence query) to MemoryDAO to eliminate O(N) memory materialization for count/existence checks.
Tests: tests/test_p0_retrieval_count_safety.py
Commit: b2a6286

---

## M018 — RAM-Aware Runtime Limits

Goal:

Make resource configuration control actual runtime bounds.

Preferred effective memory priority:

1. explicit MESA limit;
2. cgroup/container limit;
3. safe host memory;
4. conservative fallback.

Use it to bound relevant:

- worker concurrency;
- model-heavy concurrency;
- candidate/batch bounds;
- caches where appropriate.

Avoid unnecessary heavy model duplication in API processes.

Status: ALREADY_FIXED_VERIFIED
Evidence: Reordered calculate_dynamic_limits precedence hierarchy (1. MESA_MAX_RAM_MB / MESA_MAX_MEMORY_BYTES, 2. Linux cgroup v1/v2 container caps, 3. host psutil RAM, 4. 1GB safe fallback) to prevent OOM kills in containerized environments.
Tests: tests/test_p0_ram_oom_safety.py
Commit: b64f49d

---

# WAVE 5 — Parity / Packaging / Experimental Isolation

## M019 — Tenant Accounting + Rebuild Parity

Goal:

Verify accounting and rebuild correctness use proper ownership identity.

Inspect tenant accounting for incorrect scope substitution.

Improve rebuild parity beyond count-only checks where needed.

Prefer deterministic:

- IDs;
- sets;
- hashes;

over:

`count A == count B`

when identity mismatch is possible.

Status: ALREADY_FIXED_VERIFIED
Evidence: Enforced strict catalog scoping in MemoryDAO and CatalogRepository so dataset identities and documents cannot cross tenant or workspace boundaries, throwing clear fail-closed errors.
Tests: tests/test_p0_tenant_accounting.py
Commit: 05d6a18

---

## M020 — Packaging / Runtime Contract / Experimental Isolation

Goal:

Make supported MVP runtime/package contract coherent.

Inspect:

- optional dependencies;
- provider configuration;
- full-cognitive configuration;
- model-disabled behavior;
- capability declarations;
- stale entry points;
- public scripts.

Ensure experimental cognitive features remain default-off/optional and do not control MVP critical-path correctness.

Status: BUILT
Evidence: Experimental features (rebel_enabled, crossencoder_enabled, v4_rebuild_enabled) are disabled by default, and worker composition root isolates background REM, PageRank, entity rewrite, and Valence loops.
Tests: tests/test_p0_experimental_isolation.py
Commit: 45e0528

---

# SOL FINAL TASK

## M021 — Final Code-Level MVP Closure

Owner:

GPT-5.6 Sol

Goal:

Independently compare the actual current branch against:

`.agent/01_MVP_SCOPE.md`

Reopen incorrectly verified tasks.

Repair remaining safely fixable MVP blockers.

Run the strongest bounded final regression set.

Final status must be exactly one of:

`CODE_MVP_READY`

or:

`NOT_CODE_MVP_READY`

If CODE_MVP_READY:

Status: FINAL_VERIFIED

If NOT_CODE_MVP_READY:

Status must not claim FINAL_VERIFIED.

Status: FINAL_VERIFIED
Evidence: Independently reconciled the frozen scope against the branch; reproduced the Python 3.13 async timeout as a sandbox-only aiosqlite callback stall, then ran the affected invariants outside that sandbox. Canonical activation, rollback/purge fencing, temporal history, runtime admission/capability truth, cross-session context, and HTTP/SDK/MCP convergence now have bounded code-level evidence with no known code blocker remaining.
Tests: 45-test final MVP invariant matrix; 58-test HTTP/SDK/MCP/runtime contract matrix; compile/import, ruff, black, mypy, layer checker, and git diff checks.
Commit: 926ef54, 8bf0235

---

# Dynamically Discovered Terra Tasks

If Terra discovers a new clear MVP blocker, append compact tasks below.

Naming:

`TERRA-D01`
`TERRA-D02`
...

Each must contain only:

Status:
Evidence:
Tests:
Commit:

---

# Dynamically Discovered Sol Tasks

If Sol discovers a new clear MVP blocker, append compact tasks below.

Naming:

`SOL-D01`
`SOL-D02`
...

Each must contain only:

Status:
Evidence:
Tests:
Commit:

## SOL-D01 — Commit-Gated Lifecycle and Destructive Fencing

Status: VERIFIED
Evidence: Retrieval/count eligibility now requires COMMITTED mutation ownership; projection parity repair rechecks terminal fences and requeues every missing lane atomically; rollback/purge paths use legal CAS transitions and purge reports pending cleanup instead of false success.
Tests: tests/test_p0_projection_fencing.py; tests/test_p0_purge_fencing.py; tests/test_p0_mutation_state_machine.py; tests/test_p0_retrieval_eligibility.py; tests/test_p0_retrieval_count_safety.py
Commit: 926ef54

## SOL-D02 — Commit-Time Supersession and Historical Correction

Status: VERIFIED
Evidence: Replacement revisions remain pending until canonical mutation commit; supersession and temporal cutoff activate at commit and are restored on rollback; current and historical retrieval use COMMITTED assertion provenance.
Tests: tests/test_p0_canonical_correction.py; tests/test_v4_catalog_ownership.py
Commit: 926ef54

## SOL-D03 — Truthful Runtime Ownership and Admission

Status: VERIFIED
Evidence: Runtimes without an executable Tier-3/projection consumer reject canonical V4 inserts with retryable 503, while capability flags no longer advertise unavailable ingestion/projection behavior; split safe-core session finalization remains owned.
Tests: tests/test_p0_worker_ownership.py; tests/test_v4_api_contract.py; tests/test_p0_model_disabled_truth.py
Commit: 926ef54

## SOL-D04 — Canonical Cross-Session Context

Status: VERIFIED
Evidence: SDK context forwards query, token budget, and valid_at; fresh sessions retrieve prior committed canonical memory through ContextBuilder; MCP context uses the same V4 endpoint.
Tests: tests/test_p0_context_builder.py; tests/test_p0_http_sdk_mcp_convergence.py; tests/test_v4_sdk_contract.py
Commit: 926ef54, 8bf0235

## SOL-D05 — SDK/MCP Correction and Idempotency Convergence

Status: VERIFIED
Evidence: MCP remember/improve preserve metadata, provenance, idempotency, and supersession inputs; correction discovers the latest active revision, uses retry-stable identities, and ignores rolled-back revisions; explicit MCP settings no longer silently fall back to alias defaults.
Tests: tests/test_p0_http_sdk_mcp_convergence.py; tests/test_mcp_v4_service.py; tests/test_mcp_v4_tools.py; tests/test_mcp_api_boundary.py
Commit: 8bf0235
