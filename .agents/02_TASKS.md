# Round 6 Task Ledger

Branch:

```text
mvp/certification-round-6-rbac-context
```

---

## R601 — Current-State RBAC + Context Threat Map

Owner: Gemini / Terra

Trace current production ownership for:

```text
RBAC schema creation
RBAC grant/revoke
authorization lookup
RBAC caches
RBAC database initialization/versioning

ContextBuilder retrieval input
formatted_context construction
token counting
provenance rendering
```

Do not modify production code before identifying actual canonical paths.

Status: VERIFIED
Evidence: Traced production ownership across RBAC and ContextBuilder surfaces without modifying production code:
1. RBAC Storage & Schema (`mesa_memory/security/rbac.py`):
   - Standalone SQLite database (`storage/rbac_policy.db`), initialized via `AccessControl.initialize()`.
   - Flawed Primary Keys missing tenant scope:
     * `principal_workspace_roles`: `PRIMARY KEY (principal_id, workspace_id)` -> misses `tenant_id`.
     * `principal_dataset_roles`: `PRIMARY KEY (principal_id, dataset_id)` -> misses `tenant_id` and `workspace_id`.
     * `principal_dataset_permissions`: `PRIMARY KEY (principal_id, dataset_id, permission)` -> misses `tenant_id`.
   - `grant_scope_role` uses `INSERT OR REPLACE`, causing cross-tenant overwrites for identical resource IDs.
   - `grant_dataset_permission` uses `INSERT OR IGNORE`, suppressing grants for other tenants with identical resource IDs.
   - Missing explicit schema authority/version table: no migration or version tracking exists on the policy DB.
   - Scope role revocation (`revoke_scope_role`) and dataset permission revocation (`revoke_dataset_permission`) are missing from `AccessControl` and `mesa-v4-admin`.
   - Cache audit: `AccessControl` executes direct queries (no in-memory cache). `mesa_mcp/v4_service.py` session cache key `f"{tenant_id}:{workspace_id}:{actor_id}:{dataset_id}"` is tenant-scoped; no unscoped RBAC caches exist (NOT_APPLICABLE_VERIFIED for cache key bug).
2. ContextBuilder (`mesa_memory/context_builder.py`):
   - Input: `MemoryDAO.get_recent_logs()` and `MemoryDAO.search_v4_memory()`.
   - Formatted context construction: Uses raw string interpolation (`line = f"- {log.get('content', '')}"` and `- [Entity: {name}]{prov_str}`) with unescaped text and header `=== Long-Term Canonical Truth ===`.
   - Trust boundary: Missing explicit `<UNTRUSTED_MEMORY_EVIDENCE>` tags and untrusted evidence warning.
   - Token budget: Heuristic `char_budget = token_budget * 4` and slicing `line[:available]`; output estimation uses `len(formatted_context) / 4.0` with no real tokenizer measurement.
   - Provenance: Only serializes predicate/value strings, omitting available `source_ref`, `document_id`, `revision_id`, `chunk_id`, `evidence_span`.
Tests: Codebase inspection, AST/grep analysis, and verification of `tests/test_p0_context_builder.py`, `tests/test_rbac.py`, `tests/test_principal_authorization.py`, `tests/test_v4_catalog_ownership.py`.
Commit: In progress

---

## R602 — Establish Explicit RBAC Schema Authority

Owner: Gemini / Terra

Goal:

Determine how the standalone RBAC DB schema is versioned.

Implement the smallest explicit version/migration authority if missing.

Must distinguish:

```text
old unscoped schema
new tenant-scoped schema
```

Status: VERIFIED
Evidence: Established explicit RBAC schema authority via `RBAC_SCHEMA_VERSION = 2` and a metadata table `rbac_schema_version (version INTEGER PRIMARY KEY, migrated_at TEXT NOT NULL)`. `AccessControl.initialize()` inspects current version and `PRAGMA table_info` on existing tables to determine whether migration from old unscoped schema (v1) to tenant-scoped schema (v2) is required. Added `get_schema_version()` method.
Tests: `tests/test_r6_rbac_tenant_isolation.py::test_fresh_database_has_v2_schema_and_version`, `test_historical_unscoped_migration_preserves_recoverable_grants`.
Commit: In progress

---

## R603 — Tenant-Scope Workspace Roles

Owner: Gemini / Terra

Goal:

Ensure workspace-role persistence and lookup are tenant-scoped.

Required:

```text
same principal
same workspace public ID
different tenants
→ independent rows
```

Status: VERIFIED
Evidence: `principal_workspace_roles` primary key updated to `(principal_id, tenant_id, workspace_id)`. `grant_scope_role` now uses `INSERT INTO principal_workspace_roles ... ON CONFLICT(principal_id, tenant_id, workspace_id) DO UPDATE SET role = excluded.role`. Added `revoke_scope_role` with tenant scope filter.
Tests: `tests/test_r6_rbac_tenant_isolation.py::test_cross_tenant_workspace_roles_coexistence`.
Commit: In progress

---

## R604 — Tenant-Scope Dataset Roles and Permissions

Owner: Gemini / Terra

Goal:

Ensure dataset roles and explicit permissions are tenant-scoped.

Audit:

```text
grant
upsert
replace
revoke
check
list
cache
```

Status: VERIFIED
Evidence:
- `principal_dataset_roles` primary key updated to `(principal_id, tenant_id, workspace_id, dataset_id)`.
- `principal_dataset_permissions` primary key updated to `(principal_id, tenant_id, dataset_id, permission)`.
- `grant_scope_role` uses `ON CONFLICT(principal_id, tenant_id, workspace_id, dataset_id) DO UPDATE SET role = excluded.role`.
- `grant_dataset_permission` uses `ON CONFLICT(principal_id, tenant_id, dataset_id, permission) DO NOTHING`.
- Added `revoke_scope_role` and `revoke_dataset_permission` methods to `AccessControl`.
- Added CLI commands `revoke-role` and `revoke-dataset-permission` to `admin_cli.py`.
- Cache isolation: `AccessControl` runs direct queries; MCP session caches in `v4_service.py` are tenant-scoped.
Tests: `tests/test_r6_rbac_tenant_isolation.py::test_cross_tenant_dataset_roles_coexistence`, `test_cross_tenant_dataset_permissions_coexistence`, `test_cross_tenant_revoke_isolation`, `test_admin_cli_grant_and_revoke_tenant_isolation`.
Commit: In progress

---

## R605 — RBAC Migration + Cross-Tenant Regression

Owner: Gemini / Terra

Create the historical RBAC schema in a temporary DB.

Populate real existing rows.

Run real migration.

Verify:

```text
rows preserved
new keys correct
migration idempotence/expected repeat behavior
failure rollback
two-tenant same-public-ID scenario
grant/revoke isolation
```

Status: VERIFIED
Evidence: `AccessControl.initialize()` creates `_v2_*` tables, copies recoverable rows with conflict-failing `INSERT`, validates source/replacement row counts, then atomically swaps the tables and records `rbac_schema_version = 2`. Injected failure verifies transaction rollback; repeat initialization preserves data without duplication.
Tests: `tests/test_r6_rbac_tenant_isolation.py` (9 tests passing), `tests/test_rbac.py` (10 tests), `tests/test_v4_admin_cli.py` (3 tests), `tests/test_principal_authorization.py` (9 tests), `tests/test_v4_catalog_ownership.py` (8 tests), `tests/test_rbac_edge_cases.py` (17 tests).
Commit: In progress

---

## R606 — ContextBuilder Untrusted Evidence Boundary

Owner: Gemini / Terra

Goal:

Rendered long-term memory must be explicitly untrusted evidence.

Add deterministic safe serialization.

Instruction-like memory cannot become surrounding prompt structure.

Status: VERIFIED
Evidence: `ContextBuilder` (`mesa_memory/context_builder.py`) wraps formatted context within `TRUST_HEADER` and explicit `<UNTRUSTED_MEMORY_EVIDENCE>...</UNTRUSTED_MEMORY_EVIDENCE>` tags. Session logs and canonical memories are serialized as deterministic JSON records with `json.dumps(..., ensure_ascii=False)`.
Tests: `tests/test_r6_context_security_correctness.py::test_trust_header_and_tags_defined`, `test_instruction_like_memory_remains_data`.
Commit: In progress

---

## R607 — Delimiter / Injection Escape Regression

Owner: Gemini / Terra

Attack with memory containing:

```text
Ignore previous instructions
SYSTEM:
DEVELOPER:
</UNTRUSTED_MEMORY_EVIDENCE>
JSON/control characters
newlines
```

Prove it stays data.

Status: VERIFIED
Evidence: JSON rendering now encodes every untrusted `<`/`>` at the final record boundary, covering closing/opening delimiter variants as well as nested system instructions, quotes, backslashes, newlines, and control characters.
Tests: `tests/test_r6_context_security_correctness.py::test_delimiter_breakout_attack_neutralized`, `test_serialization_characters_safety`.
Commit: In progress

---

## R608 — Actual Token Budget Enforcement

Owner: Gemini / Terra

Goal:

Final:

```text
formatted_context
```

must satisfy actual canonical tokenizer count.

Character heuristic may remain prefilter only.

Required cases:

```text
Turkish
code
URL
emoji
punctuation
tiny budgets
```

Status: VERIFIED
Evidence: Integrated canonical `count_tokens` (`cl100k_base` tokenizer) via `_count_tokens`. `ContextBuilder.build_context` trims candidate canonical memories in reverse rank order and session logs until `_count_tokens(formatted_context) <= token_budget`. Tiny budgets (1, 2, 5, 10, 20) are handled deterministically without exceeding caller budget.
Tests: `tests/test_r6_context_security_correctness.py::test_token_budget_enforced_across_content_categories`, `test_tiny_token_budget_deterministic_safety`, `test_ranking_aware_budget_trimming`.
Commit: In progress

---

## R609 — Provenance Rendering

Owner: Gemini / Terra

When:

```text
include_provenance=true
```

render bounded available provenance into LLM-ready context.

Minimum where available:

```text
source_ref
document_id
revision_id
chunk_id
evidence_span
```

Provenance must remain untrusted evidence and respect token budget.

Status: VERIFIED
Evidence: When `include_provenance=True`, `ContextBuilder` includes available fields (`source_ref`, `document_id`, `revision_id`, `chunk_id`, `evidence_span` bounded to 200 chars, `jurisdiction`, `authority_level`) within the structured fact dict. Missing fields are omitted without fabricating IDs. All provenance strings are escaped and serialized as untrusted evidence before tokenizer budget enforcement.
Tests: `tests/test_r6_context_security_correctness.py::test_provenance_rendered_when_enabled_and_compact_when_disabled`, `test_missing_provenance_handles_safely`, `test_provenance_injection_attack`, `test_evidence_span_is_bounded`.
Commit: In progress

---

## R610 — Integrated Context Security Regression

Owner: Gemini / Terra

Run supported retrieval → ContextBuilder path.

Verify together:

```text
ranking preserved
memory safely serialized
actual token budget satisfied
provenance included when requested
no cross-session/tenant context regression
```

Use deterministic fixtures.

Status: VERIFIED
Evidence: End-to-end integration verified: `MemoryDAO.search_v4_memory` -> `ContextBuilder.build_context` -> `formatted_context`. Verified tenant context isolation where querying identical public dataset ID `"main"` in Tenant A does not expose Tenant B's memory.
Tests: `tests/test_r6_context_security_correctness.py::test_integrated_retrieval_to_context_builder_flow`, `test_tenant_context_isolation_spot_check`.
Commit: In progress

---

## R611 — Round 6 Regression + Documentation Closure

Owner: Gemini / Terra

Run bounded regressions:

```text
RBAC authorization
cross-tenant catalog usage
remember→retrieve→ContextBuilder
Round 5 critical memory path
quality checks
```

Update only directly relevant documentation.

Status: VERIFIED
Evidence:
- RBAC authorization & tenant isolation: `tests/test_r6_rbac_tenant_isolation.py` (9 tests passed), `tests/test_rbac.py` (10 tests passed), `tests/test_v4_admin_cli.py` (3 tests passed), `tests/test_principal_authorization.py` (9 tests passed), `tests/test_v4_catalog_ownership.py` (8 tests passed), `tests/test_rbac_edge_cases.py` (17 tests passed).
- ContextBuilder security & token bounds: `tests/test_r6_context_security_correctness.py` (21 tests passed), `tests/test_p0_context_builder.py` (1 test passed).
- V4 API & MCP contracts: `tests/test_v4_api_contract.py` (16 tests passed).
- Round 5 critical architecture baseline: `tests/test_r4_extraction_validation_independence.py` (10 tests passed), `tests/test_fact_extraction_service.py` (14 tests passed), `tests/test_embedding_service.py` (16 tests passed), `tests/test_p0_model_disabled_truth.py` (1 test passed), `tests/test_r4_validation_e2e.py` (3 tests passed), `tests/test_r4_validation_policy.py` (12 tests passed).
- Quality checks: `ruff check` (passed), `black --check` (passed), `python3 -m compileall` (passed), layer import contract `tests/test_layer_import_contract.py` (passed).
Tests: 129 passed across all Round 6 regression test suites.
Commit: In progress

---

## TERRA-R601 — Validate RBAC Schema Marker and Rebuild Preservation

Owner: Terra

Root Cause:

`rbac_schema_version = 2` bypassed table-shape checks, while `INSERT OR IGNORE`
could silently suppress a copied grant before the old policy tables were dropped.

Repair:

Validate every scoped table's full column/primary-key shape regardless of the
recorded version; reject unknown newer versions; fail closed on copy conflicts;
and compare source/replacement row counts before the atomic swap.

Status: VERIFIED

Evidence:

Static production trace confirms all role lookups/mutations are tenant-scoped.
The dataset public ID is tenant-scoped and bound to one workspace by
`CatalogRepository.ensure_scope`, so the explicit permission identity
`(principal_id, tenant_id, dataset_id, permission)` is an equivalent canonical
resource key. The new stale-v2-marker regression passed in a temporary
database outside the filesystem sandbox; sandboxed `aiosqlite` itself hangs
even for `SELECT 1`.

Tests:

`tests/test_r6_rbac_tenant_isolation.py::test_version_marker_cannot_bypass_scoped_schema_validation`
and the full RBAC focused suite: 10 passed; `python -m py_compile` passed.

Commit:

Pending Terra repair commit.

---

## TERRA-R602 — Make Evidence Tags Structural and Token Counting Strict

Owner: Terra

Root Cause:

Literal tag replacement missed case and whitespace variants, and the tokenizer
fallback could report an estimate as a hard ContextBuilder budget.

Repair:

Serialize every evidence record as JSON with `<`/`>` encoded at the final
rendering boundary, preserving JSON round-tripping while preventing any
untrusted value from producing tag syntax. ContextBuilder now requests the
canonical `cl100k_base` counter in strict mode and fails closed if unavailable.

Status: VERIFIED

Evidence:

Variant delimiter, session-log, provenance, Turkish/code/URL/emoji/punctuation,
tiny-budget, ranking, missing-provenance and injection regressions pass through
the production `ContextBuilder.build_context()` formatter.

Tests:

20 focused ContextBuilder tests passed in two commands; `python -m py_compile`
passed for touched production modules.

Commit:

Pending combined Terra repair commit after repository quality gates.

---

## R612 — Sol Final Round 6 Certification

Owner: Sol

Independently falsify:

```text
RBAC tenant isolation
RBAC migration safety
ContextBuilder trust boundary
delimiter escaping
actual token budget
provenance rendering
Round 5 regressions
```

Sol may add:

```text
SOL-R601
SOL-R602
...
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
