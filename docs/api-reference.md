# API Reference

> Core classes and their public interfaces. All async methods must be awaited.

---

## `MemoryDAO`

**Module:** `mesa_storage.dao`

The MemoryDAO is the isolated boundary responsible for synchronous data integrity across the dual-engine storage backend (SQLite WAL and LanceDB vector index). It enforces Row-Level Security via hardcoded `agent_id` requirements and removes the bottleneck of the legacy `StorageFacade`.

### Methods

#### `async insert_memory(agent_id: str, node_id: str, entity_name: str, content: str, embedding: list[float], ...) → None`

Persists a Cognitive Memory Block (CMB) into both the relational graph and vector index. Includes a strict **1MB payload size limit** to prevent memory-exhaustion DoS attacks.

**Raises:**
- `ValueError` — If `agent_id` is invalid or unset, or if the `content` payload exceeds 1MB.

---

#### `async search_memory(agent_id: str, query_vector: list[float], limit: int, ...) → list[dict]`

Performs a cosine similarity search on the LanceDB vector store, bounded by the required `agent_id`.

---

#### `async search_memory_fts(agent_id: str, query: str, limit: int = 100) → list[dict]`

Executes an ultra-fast zero-VRAM lexical pre-filter using SQLite's FTS5. Queries are internally converted to soft-OR boolean operations.

---

#### `async purge_memory(agent_id: str, scope: str = "agent", session_id: str | None = None) → int`

Executes an atomic Two-Phase Commit Saga pattern for soft-deletion. Vector entries are deleted first; if successful, the SQLite node is marked with `deleted_at = CURRENT_TIMESTAMP`. No hard-deletes (`DELETE` or `VACUUM`) are executed.

---

## `ValenceMotor`

**Module:** `mesa_memory.valence.core`

The 3-tier admission gate that determines whether an incoming memory candidate is novel enough to store. Manages EWMAD (Exponentially Weighted Moving Average of Distances) drift calibration for adaptive thresholds.

### Constructor

```python
ValenceMotor(
    llm_adapter: BaseUniversalLLMAdapter,
    obs_layer: ObservabilityLayer,
    storage=None,
)
```

| Parameter | Type | Description |
|---|---|---|
| `llm_adapter` | `BaseUniversalLLMAdapter` | LLM adapter for embedding and completion |
| `obs_layer` | `ObservabilityLayer` | Metrics and logging layer |
| `storage` | `MemoryDAO \| None` | Storage for embedding hydration |

### Methods

#### `async evaluate(cmb_candidate: dict, current_state_signals: dict) → bool | str`

Evaluates a CMB candidate through the 3-tier validation pipeline.

**Returns:**
- `True` — Admitted (Tier-1 or Tier-2 novelty pass)
- `False` — Discarded (error signal, format violation, or low novelty)
- `"DEFERRED"` — Escalated to Tier-3 for asynchronous consolidation

**Signal flags in `current_state_signals`:**

| Key | Type | Effect |
|---|---|---|
| `error` | `bool` | If `True`, immediately `DISCARD` |
| `format_violation` | `bool` | If `True`, immediately `DISCARD` |
| `explicit_correction` | `bool` | If `True`, force `ADMIT` |

---

#### `async save_state(db_path: str) → None`

Persists `_ewmad_threshold` and `memory_count` to a SQLite `valence_state` table. Called during graceful shutdown.

---

#### `async load_state(db_path: str) → None`

Restores cognitive state from SQLite. Fails gracefully on fresh setups (table doesn't exist yet).

---

### Properties

| Property | Type | Description |
|---|---|---|
| `memory_count` | `int` | Total records admitted since initialisation |
| `existing_embeddings` | `list` | Cached embedding vectors for novelty comparison |
| `bootstrap_threshold` | `float` | Initial cosine threshold from config |

---

## `HybridRetriever`

**Module:** `mesa_memory.retrieval.hybrid`

Fuses vector similarity search (cosine distance via LanceDB) with graph-based Personalized PageRank (via NetworkX) using Reciprocal Rank Fusion (RRF). Includes a cold-start fallback mode for sparse graphs.

### Constructor

```python
HybridRetriever(
    dao: MemoryDAO,
    analyzer: QueryAnalyzer,
    embedder: BaseUniversalLLMAdapter,
    access_control: AccessControl | None = None,
)
```

| Parameter | Type | Description |
|---|---|---|
| `dao` | `MemoryDAO` | Unified data access object |
| `analyzer` | `QueryAnalyzer` | Entity extraction from query text |
| `embedder` | `BaseUniversalLLMAdapter` | Adapter for query embedding |
| `access_control` | `AccessControl \| None` | RBAC controller |

### Methods

#### `async retrieve(query_text: str, agent_id: str, session_id: str, top_n: int = 5) → list[str]`

Executes a hybrid retrieval query and returns ranked CMB IDs.

**Pipeline:**
1. Normalises the query text
2. Extracts named entities for graph seeding
3. Runs vector search and PPR graph search concurrently
4. Fuses results via RRF (or cold-start reranking if graph is sparse)
5. Returns the top-N CMB IDs

**Raises:**
- `PermissionError` — Agent lacks `READ` access

---

#### `async get_vector_results(query_text: str, k: int = 10) → list[dict]`

Embeds the query and performs kNN search on the vector index.

---

#### `async get_graph_results(entities: list[str]) → list[dict]`

Finds seed nodes by entity name and runs Personalized PageRank from them.

---

#### `format_working_memory(nodes: list[dict], max_tokens: int | None = None) → str`

Converts retrieved nodes into a token-limited context string for LLM prompts. Uses whole-node inclusion policy — no partial content slicing.

---

## `AccessControl`

**Module:** `mesa_memory.security.rbac`

SQLite-backed RBAC (Role-Based Access Control) system. Enforces `READ` and `WRITE` permissions at the `(agent_id, session_id)` granularity.

### Constructor

```python
AccessControl(
    policy_path: str = "./storage/rbac_policy.db"
)
```

### Methods

#### `grant_access(agent_id: str, session_id: str, level: str) → None`

Grants `READ` or `WRITE` access to an agent for a specific session. `WRITE` implicitly includes `READ` privileges.

| Parameter | Type | Description |
|---|---|---|
| `agent_id` | `str` | Unique agent identifier |
| `session_id` | `str` | Session scope |
| `level` | `str` | `"READ"` or `"WRITE"` |

**Raises:**
- `ValueError` — Invalid access level

> [!WARNING]
> The RBAC table uses a `PRIMARY KEY (agent_id, session_id)` constraint. Calling `grant_access` with a different level for the same agent/session pair **overwrites** the previous grant. Since `WRITE` includes `READ`, always grant `WRITE` if both are needed.

---

#### `revoke_access(agent_id: str, session_id: str) → None`

Removes all access for an agent/session pair.

---

#### `check_access(agent_id: str, session_id: str, required_level: str) → bool`

Returns `True` if the agent has sufficient permissions. `WRITE` satisfies both `READ` and `WRITE` checks.

---

## Error States Summary

| Error | Source | Cause | Recovery |
|---|---|---|---|
| `PermissionError` | `MemoryDAO`, `HybridRetriever` | Agent lacks required RBAC access | Call `grant_access()` first |
| `RuntimeError` | `MemoryDAO.purge_memory` | Partial purge across storage layers | Saga pattern prevents zombie data |
| `MemoryError` | `VectorEngine` | LanceDB memory usage exceeds configured limit | Increase `lancedb_memory_limit_bytes` or `MESA_MAX_RAM_MB` |
| `ImportError` | `RebelExtractor` | `transformers` library not installed | Install via `python -m pip install -e ".[ml]"` |
| `ValueError` | `AdapterFactory` | Unknown LLM provider string | Use `openai_compatible`, `claude`, `ollama`, or `mock` |

---

## FastAPI Endpoints (v3)

MESA exposes headless asynchronous endpoints:

- `POST /v3/memory/insert`: Queues memory via `BackgroundTasks` for <150ms latency.
- `POST /v3/memory/search`: Performs FTS5 lexical pre-filters + LanceDB vector similarity search.
- `DELETE /v3/memory/purge`: Soft-deletes using Two-Phase Commit Saga to guarantee zero zombie data.
