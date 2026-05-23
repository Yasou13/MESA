# ADR 0001: Use SQLite WAL and LanceDB for Async Memory

## Status
Accepted

## Context
MESA initially used an in-memory NetworkX graph and a monolithic synchronous facade. This created an operational bottleneck during data consolidation, resulted in high VRAM/RAM pressure, and exposed the system to cross-tenant data leakage risks due to missing query-level isolation. Furthermore, lock contentions on high concurrency updates degraded system performance.

## Decision
We pivoted to a dual-engine architecture:
1. **SQLite (WAL mode)** for relational graph data, semantic edges, and FTS5 lexical pre-filtering. WAL mode allows concurrent reads during writes.
2. **LanceDB** for dense vector operations.
3. **Async MemoryDAO**: A strict Data Access Object that enforces Epistemic Isolation via mandatory `agent_id` hardcoding at the query level.

## Consequences
- **Positive:** Guaranteed tenant isolation (Row-Level Security simulation).
- **Positive:** High concurrency via asynchronous drivers (`aiosqlite`) and WAL mode.
- **Positive:** Idempotent soft-deletion without catastrophic locking.
- **Negative:** Increased schema complexity (triggers syncing `nodes` to `nodes_fts`).
