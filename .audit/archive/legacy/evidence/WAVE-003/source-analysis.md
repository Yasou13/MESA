# Source analysis

`PersistentQueue` was append-only JSONL with a process-local lock. The replay worker read items by index and removed them after an opaque `run_batch()` result; no durable owner, lease, attempt or poison state existed. Raw logs now have WAVE-003 SQLite fencing but still have no durable dispatcher, quota or readiness integration. `/health/init` checks storage only. `cold_path_trace.txt` was hard-coded at runtime in ingestion worker (not import time).
