# PROFILE A REPAIR JOURNAL

## Repair Entry 1
- **Repair ID**: R8-SOAK-001
- **Timestamp**: 2026-08-22T02:12:00Z
- **Run ID**: run-00-pre
- **Soak elapsed time when detected**: 00h 05m
- **Commit under test**: c1a6e35d801957eb3cb9ab8e451be819315d5a5e
- **Failure signature**: LanceDB Table handle retention in VectorEngine and repeated tokenizer re-instantiation causing memory accumulation during continuous mutation workload
- **Evidence**: `_tables` dictionary retained mutated LanceDB Table references indefinitely; tokenizers instantiated on each call.
- **Root cause**: Persistent handle cache in VectorEngine retained mutated dataset instances; tokenizers were not cached at module level.
- **Files changed**: `mesa_memory/adapter/tokenizer.py`, `mesa_storage/vector_engine.py`, `tests/test_vector_engine.py`
- **Exact change**: Removed mutated table handle caching in VectorEngine `_tables`; cached `_CL100K_ENCODING` and `_TRANS_TOKENIZERS` in `tokenizer.py`.
- **Targeted reproduction test**: `tests/test_vector_engine.py::TestMemoryLifecycle`
- **Regression tests**: `tests/test_vector_engine.py`, `tests/test_soak_profile_a.py` (55 passed)
- **Smoke result**: 5-minute embedded smoke PASSED (300s, 0 errors, health healthy, RSS 694.67MB, no leak)
- **Repair commit SHA**: dadfd65
- **Restart timestamp**: 2026-08-22T02:15:00Z
