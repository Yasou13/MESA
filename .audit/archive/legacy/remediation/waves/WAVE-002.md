# WAVE-002 — Triple-store mutation contract

## Metadata

| Alan | Değer |
|---|---|
| Wave | WAVE-002 |
| Status | FIXED_NOT_VERIFIED — E2 checkpoint |
| Run ID | rem-20260719-151831-W002 |
| Branch | audit/production-readiness |
| Start HEAD | c69d1f9c18844c393c26291db6c67628d82167f1 |
| End HEAD | c69d1f9c18844c393c26291db6c67628d82167f1 |
| Started at | 2026-07-19T15:18:31+03:00 |
| Completed at | 2026-07-19T16:05:00+03:00 |

## Scope

`DATA-002`, `DATA-001`, `DATA-004`: `MemoryDAO`/Kuzu/LanceDB/SQLite mutation visibility and vector idempotency.

## Canonical findings

- `DATA-002`: E2 fail-closed/compensation fix applied; remains `FIXED_NOT_VERIFIED` pending real three-store commit/recovery evidence.
- `DATA-004`: E2 fail-closed fix applied; remains `FIXED_NOT_VERIFIED` pending real LanceDB/retry/replay evidence.
- `DATA-001`: canonical SQLite journal/tombstone E2 implementation applied; remains `FIXED_NOT_VERIFIED` pending E3.

## Kök neden

Kuzu node-write exceptions were swallowed after vector mutation. LanceDB merge failures were converted to non-idempotent `add()` writes. DATA-001 lacked a canonical purge lifecycle; user-approved ADR now makes SQLite the coordinator and preserves exact journal scope.

## Etkilenen invariant’lar

1. A failed graph write must not produce a successful API/DAO mutation or an active new vector.
2. A failed vector upsert must not become a duplicate-prone insert.
3. Purge lifecycle now proceeds `PREPARED → TOMBSTONED → KUZU_APPLIED → VECTOR_APPLIED → VERIFIED → FINALIZED`; real-store/runtime evidence remains required.

## Bağımlılıklar

WAVE-000 contract satisfied. WAVE-001 E3 is not a direct technical dependency. WAVE-005 is required for isolated runtime/API evidence but does not block the E2 component work completed here.

## Dokunulan dosyalar

- `mesa_storage/dao.py`
- `mesa_storage/vector_engine.py`
- `mesa_storage/kuzu_provider.py`
- `mesa_storage/schemas.py`
- `mesa_storage/alembic/versions/c4f1a8e2d9b0_add_purge_journal.py` (new additive migration)
- `mesa_api/router.py`
- `tests/test_triple_store_mutation_contract.py` (new)
- `tests/test_purge_journal_contract.py` (new)

## Reproduction plan and result

Before the patch, `tests/test_triple_store_mutation_contract.py` produced 3 deterministic failures: graph failure was swallowed; single/bulk merge failure called `add()`. The same suite passed 3/3 after the patch. See `../evidence/WAVE-002/before.txt` and `after.txt`.

## Uygulanan değişiklikler

- `MemoryDAO.insert_memory` and `bulk_insert_memory` now log graph failure, soft-delete newly written vectors when applicable, and propagate the failure before SQLite mutation.
- `VectorEngine._sync_upsert` and `_sync_bulk_upsert` now reject merge failure instead of falling back to `add()`.

## Target and related tests

- Target/focused: 3 passed in 1.41s using `/storage/mesa-lab/storage/WAVE-002/pytest-20260719-151831-fix`.
- `py_compile` of both source files and the new test passed.
- Existing DAO/chaos suites were not run because their hard-coded cleanup paths fall outside this wave’s permitted lab storage root.

## Runtime / integration gate

Not run. No API, worker, Docker, provider, Ollama, migration or production interaction. E3/E4 are not claimed.

## Data-integrity impact

The exact demonstrated silent graph-failure split-brain and duplicate-prone fallback are fail-closed at E2. SQLite commit failure after successful secondary writes, partial bulk compensation, restart recovery, and purge/maintenance Kuzu lifecycle remain unresolved.

## New findings

No new canonical ID: the purge lifecycle limitation is existing `DATA-001`.

## Remaining risk

`DATA-001` remains open; `DATA-002` and `DATA-004` are not closed. Canonical P0/P1 totals and `NO_GO` remain unchanged.

## Rollback procedure

Use the two external pre-edit copies listed in `../evidence/WAVE-002/rollback-status.txt` only after review; then rerun the focused suite. No Git destructive command is authorized.

## Wave result

`FIXED_NOT_VERIFIED` — DATA-001/002/004 deterministic E2 fixes passed. DATA-001 ADR is now implemented with synthetic migration/lifecycle evidence; real Kuzu/Lance, E3 runtime, backup/restore ledger reconciliation and full recovery proof remain.

## DATA-001 approved ADR continuation

User-approved decision is canonical: SQLite owns purge metadata/state and exact scope; Kuzu and LanceDB are downstream projections. The additive journal stores `purge_id`, principal/agent/session scope, exact node IDs, state, per-store result, retry count, last error and idempotency key. Tombstoning is committed before downstream work. Kuzu failure skips vector work; vector failure retains Kuzu state and recovery resumes only vector. Router requires active principal plus target-agent `PURGE` permission and maps pending work to 503.

E2 result: `tests/test_purge_journal_contract.py` (7 passed) plus the existing WAVE-002 tests (3 passed). E3/E4 are absent; no finding is closed and `NO_GO` remains.
