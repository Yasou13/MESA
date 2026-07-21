# Controlled recovery

- Expired `raw_logs` claims return to `DEFERRED`; terminal rows are not changed.
- Expired `lancedb_wal` `PROCESSING` rows return to `PENDING`; `ACKED` rows remain immutable to replay claiming.
- `MemoryDAO.initialize()` invokes both bounded recovery methods.
- `replay_lancedb_wal()` claims rows, performs vector I/O outside the SQLite transaction, releases on failure, and ACKs each row only after successful upsert.

E3 process crash/restart recovery is not verified.
