# Graph fixture failure classification

## A — TEST_HARNESS_MISMATCH (9 direct failures)

Tests: `TestInsertMemory::{test_insert_returns_node_id,test_insert_auto_generates_id,test_insert_custom_id}`, `TestBulkInsert::test_bulk_inserts_multiple`, `TestGetMemories::{test_filters_unconsolidated_only,test_with_limit}`, `TestMarkConsolidated::test_marks_node`, `TestEdgeOperations::{test_insert_edge_auto_id,test_insert_edge_custom_id}`.

Signature: `TypeError: object MagicMock can't be used in 'await' expression` at `graph_provider.insert_node`.

Fixture: `tests/test_dao.py:dao_env` had `insert_entity` but no async `insert_node`. WAVE-002’s approved fail-closed contract now awaits `insert_node` before the SQLite commit. Expected contract is an awaitable graph insert; actual fixture supplied an ordinary generated MagicMock. Action: added only `mock_kuzu.insert_node = AsyncMock()`. Product impact: none; production behavior was not changed.

## B — EXPECTED_CONTRACT_UPDATE (4 original failure paths; 3 remain after A)

Tests: `TestGetNeighbors::{test_both_direction,test_out_direction,test_in_direction,test_no_neighbors}`.

Signature after group A: empty neighbor list instead of expected mock neighbor. The WAVE-002 canonical tombstone/read filter returns only graph neighbor IDs that exist in tenant-scoped SQLite. Fixture originally returned fixed `n2`, which was not the generated SQLite node ID. Action: `_seed` now makes the mock return its actual tenant node ID; the explicit empty-neighbor test keeps its empty override. Product impact: fixture correction only.

Re-run result: `tests/test_dao.py` = 33 passed. No product regression or new finding was found.
