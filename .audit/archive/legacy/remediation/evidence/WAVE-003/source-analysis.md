# Source analysis

- `MemoryDAO.align_memory_space` set `system_config.lancedb_is_migrating` as a plain boolean, called vector promotion, then read every `lancedb_wal` row inside a SQLite write transaction, performed external `bulk_upsert`, and deleted all rows. There was no ownership, acknowledgement, or restart replay state.
- `VectorEngine.apply_procrustes_and_switch` held `_mutation_lock` only around promotion. The snapshot/transform interval could overlap a vector mutation.
- `process_cold_path` read a `DEFERRED` raw-log row and later updated it to `processing`; `update_raw_log_status` matched only `id` and `agent_id`. Two workers could both produce side effects and any caller could overwrite terminal state.
- Canonical requested invariants: single owner with fencing, lease expiry recovery, guarded terminal transition, WAL claim/ack after idempotent upsert, and no external vector I/O while holding a SQLite write transaction.
