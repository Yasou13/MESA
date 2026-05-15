# API Reference

> Core classes and their public interfaces. All async methods must be awaited.

---

## `StorageFacade`

**Module:** `mesa_memory.storage`

Unified interface to the three-layer storage backend (SQLite raw log, LanceDB vector index, NetworkX knowledge graph). All write operations enforce RBAC and maintain cross-layer consistency via atomic commit/rollback.

### Constructor

```python
StorageFacade(
    raw_log_path: str = "./storage/raw_log.db",
    vector_uri: str = "./storage/vector_index.lance",
    graph_db_path: str = "./storage/knowledge_graph.db",
    graph_rocks_path: str = "./storage/kg_history.rocks",
    access_control: AccessControl | None = None,
    graph_provider: BaseGraphProvider | None = None,
)
```

| Parameter | Type | Description |
|---|---|---|
| `raw_log_path` | `str` | Path to the SQLite raw log database |
| `vector_uri` | `str` | Path to the LanceDB vector index directory |
| `graph_db_path` | `str` | Path to the SQLite-backed knowledge graph |
| `graph_rocks_path` | `str` | Path to the RocksDB MVCC history archive |
| `access_control` | `AccessControl \| None` | RBAC controller; auto-created if `None` |
| `graph_provider` | `BaseGraphProvider \| None` | Custom graph backend; defaults to `NetworkXProvider` |

### Methods

#### `async initialize_all(valence_motor=None) → None`

Initialises all storage layers (creates tables, hydrates caches). If a `ValenceMotor` is provided, restores its cognitive state from the raw log database.

**Must be called before any read/write operations.**

```python
facade = StorageFacade()
await facade.initialize_all(valence_motor=motor)
```

---

#### `async persist_cmb(cmb: CMB, agent_id: str, session_id: str) → None`

Persists a Cognitive Memory Block to both the raw log and vector index atomically. If the vector write fails, the raw log insert is automatically rolled back via soft-delete.

| Parameter | Type | Description |
|---|---|---|
| `cmb` | `CMB` | Pydantic model containing content, embedding, metadata |
| `agent_id` | `str` | Identity of the writing agent |
| `session_id` | `str` | Session scope for RBAC check |

**Raises:**
- `PermissionError` — Agent lacks `WRITE` access for the session
- `RuntimeError` — Vector write failed (raw log is auto-reverted)

---

#### `async get_cmb(cmb_id: str, agent_id: str, session_id: str) → dict | None`

Retrieves a single CMB record by ID from the raw log.

**Raises:**
- `PermissionError` — Agent lacks `READ` access for the session

---

#### `async soft_delete_all(cmb_id: str) → None`

Purges a CMB record from all three storage layers in dependency order (raw_log → vector → graph). Partial failures are logged for manual reconciliation.

**Raises:**
- `RuntimeError` — Partial purge; includes list of completed layers

---

#### `async reconcile_orphans() → int`

Finds raw log records with no matching vector entry (crash-between-stores scenario) and soft-deletes them. Returns the count of reconciled orphans.

---

#### `load_embedding_cache(limit: int | None = None) → list[list[float]]`

Synchronous convenience method that loads embeddings from the vector store for `ValenceMotor` hydration during initialisation.

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
| `storage` | `StorageFacade \| VectorStorage \| None` | Storage for embedding hydration |

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
    storage_facade: StorageFacade,
    analyzer: QueryAnalyzer,
    embedder: BaseUniversalLLMAdapter,
    access_control: AccessControl | None = None,
)
```

| Parameter | Type | Description |
|---|---|---|
| `storage_facade` | `StorageFacade` | Unified storage interface |
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
| `PermissionError` | `StorageFacade`, `HybridRetriever` | Agent lacks required RBAC access | Call `grant_access()` first |
| `RuntimeError` | `StorageFacade.persist_cmb` | Vector write failed after raw log insert | Auto-reverted; retry the operation |
| `RuntimeError` | `StorageFacade.soft_delete_all` | Partial purge across storage layers | Manual reconciliation required |
| `MemoryError` | `VectorStorage` | LanceDB memory usage exceeds configured limit | Increase `lancedb_memory_limit_bytes` or `MESA_MAX_RAM_MB` |
| `ImportError` | `RebelExtractor` | `transformers` library not installed | Install via `requirements-ml.txt` |
| `NotImplementedError` | `MemgraphProvider` | Provider is a future roadmap stub | Use `NetworkXProvider` instead |
| `ValueError` | `AdapterFactory` | Unknown LLM provider string | Use `openai_compatible`, `claude`, `ollama`, or `mock` |
