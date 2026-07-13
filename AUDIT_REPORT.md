## MESA Full Repository Audit

Started: 2026-07-13 12:25:00
Repo commit/version: 0.5.2

## Phase 0 — Metadata

- **Total file count:** 5344
- **Total `.py` file count:** 190
- **Total Python line count:** 42040

**Version Check:**
- `pyproject.toml` version: 0.5.2
- README badge version: 0.5.2
- `CHANGELOG.md` latest entry: 0.5.2 (2026-07-13)
- **MISMATCH FLAG:** Hardcoded `version="0.4.0-dev"` found in `scripts/run_server.py` (Line 180). This conflicts with the official 0.5.2 version.

**Top-Level Directories:**
- `.agents`: Custom agent behavior configurations and skills.
- `.benchmarks`: Data and results for performance benchmarking.
- `.git`: Git version control directory.
- `.githooks`: Git hooks for pre-commit/pre-push actions.
- `.github`: GitHub Actions CI/CD workflows and configuration.
- `__pycache__`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.test_storage_tmp`: Cache and temporary files from testing and linting tools.
- `data`: General data storage directory for tests or demos.
- `docs`: Documentation files and ADRs.
- `examples`: Example scripts (e.g., `hello_mesa.py`).
- `mesa-benchmark`: Benchmark suite sub-package.
- `mesa_api`: FastAPI daemon layer and v3 REST server routing.
- `mesa_client`: Python HTTP SDK and LangChain adapter.
- `mesa_evals`: Evaluation pipeline and synthetic dataset tools.
- `mesa_mcp`: Model Context Protocol server integration for Claude Desktop.
- `mesa_memory`: Core cognitive memory engine, consolidation, and extraction logic.
- `mesa_storage`: Storage facade for KuzuDB, LanceDB, and SQLite WAL.
- `mesa_workers`: Background tasks (maintenance, rem cycle).
- `notebooks`: Jupyter notebooks, including Colab setups.
- `scripts`: Utility, dev execution, and migration scripts.
- `storage`: Runtime storage data directory for databases.
- `tests`: Test suite for the application.
- `venv`: Python virtual environment.

## Phase 1 — Root Hygiene

**Root Files List:**
`.dockerignore`, `.env`, `.env.example`, `.gitignore`, `ARCHITECTURE.md`, `BENCHMARK_METHODOLOGY.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `Dockerfile`, `LICENSE`, `MANIFEST.in`, `Makefile`, `README.md`, `beam_eval_report.json`, `colab_sonuc.md`, `conftest.py`, `docker-compose.yml`, `install.sh`, `mega-analiz.md`, `pyproject.toml`, `report_reproduce_seed_42.md`, `reproducibility_report.json`, `results_reproduce_seed_42.jsonl`, `rewrite_harness.py`, `state.json`, `version_bump.py`.

**Flags:**
- **Test files in root:** `conftest.py` should be moved to `tests/`.
- **Scratch/Debug scripts:** `rewrite_harness.py`.
- **Runtime databases/caches:** `state.json` is present in root.
- **Manual debugging/output files:** `beam_eval_report.json`, `colab_sonuc.md`, `report_reproduce_seed_42.md`, `reproducibility_report.json`, `results_reproduce_seed_42.jsonl`.
- **Secrets/Env:** `.env` is present in the root.

**.gitignore Gaps:**
- While `.gitignore` covers many runtime files (like `*.db`, `*.lance`), it fails to explicitly ignore `beam_eval_report.json` and `rewrite_harness.py`.

**Requirements Check & Dockerfile Drift:**
- **CRITICAL MISMATCH:** `requirements-core.txt` and `requirements-ml.txt` DO NOT EXIST in the repo (presumably deleted during `pyproject.toml` migration). 
- However, `Dockerfile` still contains `COPY requirements*.txt ./` and `RUN pip install -r requirements-core.txt`. **The Docker image will completely fail to build.**
- `install.sh` is correct (it uses `pip install -e .`).

**Makefile Check:**
- **BROKEN TARGET:** `make bench` points to `python scripts/run_investor_demo.py`, but this file DOES NOT EXIST.

## Phase 2 — Core Storage Layer (`mesa_storage/`)

**Files Audited:**
`kuzu_setup.py`, `schemas.py`, `sqlite_engine.py`, `kuzu_provider.py`, `dao.py`, `vector_engine.py`.

**1. File Stats & Completeness:**
- `dao.py` (1732 lines): Classes: `MemoryDAO`. Type hints/Docstrings: Present.
- `vector_engine.py` (1385 lines): Classes: `VectorEngine`, `VectorMetrics`. Type hints/Docstrings: Present.
- `kuzu_provider.py` (735 lines): Classes: `BaseGraphProvider`, `KuzuGraphProvider`. Type hints/Docstrings: Present.
- `sqlite_engine.py` (426 lines): Classes: `AsyncEngine`, `ConnectionMetrics`. Type hints/Docstrings: Present.
- `schemas.py` (423 lines): Classes: None. Type hints/Docstrings: Present.
- `kuzu_setup.py` (169 lines): Classes: None. Type hints/Docstrings: Present.

**2. Row-Level Security (`agent_id` enforcement):**
- In general, `agent_id` is passed as a mandatory argument and strictly enforced in SQL/Cypher clauses.
- **VIOLATION (dao.py):** `get_all_active_agent_ids()` performs an unscoped global query (`SELECT DISTINCT agent_id FROM nodes`). Note: Docstring explicitly states "This is a system-level query... No RLS filtering is applied — the caller is trusted", but it still violates the strict protocol rule that *every* SQL query must have the `agent_id` filter.
- **VIOLATION (schemas.py):** `insert_node` has a default argument `agent_id: str = "__unset__"`. `bulk_insert_nodes` also falls back to `"__unset__"`. The protocol states `agent_id` must be mandatory.

**3. Action Methods Implementation vs. Naming:**
- Soft deletes (`purge_memory`, `invalidate_node`, `soft_delete_node`, `soft_delete`) correctly use `UPDATE ... SET deleted_at` (or `invalid_at`/`expired_at`) rather than physical `DELETE`s.
- `insert_edge` in `dao.py` correctly calls `graph.insert_edge()`. `get_neighbors` correctly calls `graph.get_neighbors()`.

**4. Async/Sync Boundaries:**
- **VIOLATION (kuzu_setup.py & run_server.py):** `kuzu_setup.initialize_schema` is a fully synchronous function that calls `kuzu.Database()` and `kuzu.Connection()` directly. However, it is invoked sequentially in the `run_server.py` lifespan (`kuzu_setup.initialize_schema("./storage/kuzu_db")`) without `run_in_executor`. This blocking disk I/O / C++ call will block the FastAPI event loop during startup.
- `vector_engine.py` and `kuzu_provider.py` correctly offload disk I/O and synchronous calls to a `ThreadPoolExecutor`.

**5. DDL Invocation:**
- `initialize_schema` from `schemas.py` is invoked at startup in `scripts/run_server.py` (`await initialize_schema(_state.sqlite_engine)`).
- `kuzu_setup.initialize_schema` is invoked at startup in `scripts/run_server.py`.

**6. Migration Scaffolding:**
- `schemas.py:initialize_schema` correctly wraps Alembic upgrades in an executor: `await loop.run_in_executor(None, command.upgrade, alembic_cfg, "head")`.
- **VIOLATION:** The migration call inside `initialize_schema` does NOT have an `asyncio.wait_for` timeout. If the migration hangs, the server startup will block indefinitely.

## Phase 3 — Core Memory Layer (`mesa_memory/`)

**Scope:** `adapter/`, `api/`, `consolidation/`, `extraction/`, `retrieval/`, `security/`, `valence/`, `observability/`, `utils.py`, `config.py`

**1. Dead Code & Split-Brain Check:**
- No dead code found. `triplet_extractor.py`, `drift.py`, `novelty.py`, and `rebel_pipeline.py` are properly wired and used by consolidation and observability layers.
- **No Split-brain:** There is no duplicate `storage` or `schema` DAO implementation inside `mesa_memory/`. The codebase strictly delegates to `mesa_storage.dao.MemoryDAO` as the single source of truth for database operations. 
- Legacy Python NetworkX spreading activation code is entirely removed; the retrieval engine offloads graph traversal directly to `KuzuGraphProvider`.

**2. Consolidation & Validation (`consolidation/`):**
- `Tier3Validator` correctly executes dual-LLM consensus logic (`asyncio.gather(..., return_exceptions=True)`) and is properly wired into the `ConsolidationLoop._process_batch` pipeline. Failures correctly propagate via `Tier3ValidationError` rather than silently defaulting to DISCARD.

**3. Hybrid Retrieval (`retrieval/`):**
- `HybridRetriever` directly queries KùzuDB via `dao.get_neighbors` and `dao.graph_provider.get_cognitive_salience`. It does NOT silently fall back to vector-only search unless graph results are empty (which is handled appropriately for cold-start cases).

**4. Valence State Hooks (`valence/`):**
- `ValenceMotor.load_state` and `ValenceMotor.save_state` are correctly invoked in the `api/server.py` lifespan (at startup line 175, and shutdown line 311). This successfully prevents EWMAD threshold amnesia upon restarts.

**5. Security & RBAC (`security/`):**
- **API Key Checking:** Handled safely in `mesa_memory/api/server.py:63` using `secrets.compare_digest(api_key, _MESA_API_KEY)`. No insecure `==` checks are present for API keys in the core server path.
- **Prompt Injection Defense:** `rbac.py` provides `detect_prompt_injection`, which is explicitly documented and implemented as an **advisory-only** logging mechanism. The actual defense relies on structural XML tagging (`<CONTENT>...</CONTENT>`) inside prompts (e.g. `VALENCE_PROMPT_A_TEMPLATE` in `validator.py`), instructing the LLM to treat the block as untrusted user data.

## Phase 4 — API Layer (`mesa_api/` & Server Entrypoints)

**Scope:** `mesa_api/router.py`, `mesa_api/schemas.py`, `mesa_memory/api/server.py` (Prod), `scripts/run_server.py` (Dev)

**1. Endpoints & Latency Architecture:**
- `POST /v3/memory/insert`: Correctly implemented as a hot-path async fire-and-forget mechanism. It synchronously inserts into `raw_logs` (<50ms target) and enqueues `process_cold_path` to `BackgroundTasks`.
- `POST /v3/memory/search`: Calls `HybridRetriever.retrieve()` synchronously. **VIOLATION**: There is no explicit `asyncio.wait_for` timeout applied to the search execution. If the backend KuzuDB or Vector engine hangs during multi-hop graph traversal, the FastAPI request will block indefinitely.

**2. Server Entrypoints Parity (Prod vs Dev):**
- **CRITICAL VIOLATION (Feature Drift):** `scripts/run_server.py` (Dev) initializes the API router with `get_consolidation_loop=lambda: None`. As a result, the `ConsolidationLoop` (Tier-3 consensus) is completely bypassed in the dev server cold-path ingestion. `MaintenanceWorker` and `REMCycleWorker` are also omitted from the dev server, meaning physical garbage collection and background optimizations will never run in dev.
- **Security Posture Drift:** `mesa_memory/api/server.py` (Prod) enforces the `X-API-Key` at the router dependency level, while the dev server enforces it via a global middleware (unless bypassed via `--no-auth`).
- **Initialization Bug:** Dev server passes `get_access_control=None` to the router, causing the router to instantiate a new `AccessControl()` object per-request without calling `await ac.initialize()`, potentially missing the SQLite policy table initialization or opening/closing DB files redundantly.

**3. RBAC & Tenant Isolation (`agent_id`):**
- `POST /v3/memory/insert`: Properly enforces tenant isolation via `await ac.check_access(agent_id, session_id, "WRITE")`.
- `POST /v3/memory/search`: Properly enforces read isolation inside `HybridRetriever.retrieve`.
- **CRITICAL VIOLATION:** `DELETE /v3/memory/purge`, `POST /v3/session/start`, `POST /v3/session/{session_id}/end`, and `GET /v3/session/{session_id}/context` endpoints **do not call `AccessControl.check_access`**. They fully trust the client-supplied `agent_id` and bypass RBAC entirely, allowing any client with the global API key to read session contexts or purge memories for ANY other agent.

**4. Pydantic V2 Schemas:**
- All schemas (`MemoryInsertRequest`, `MemorySearchRequest`, `MemoryPurgeRequest`, etc.) correctly use `model_config = ConfigDict(strict=True, frozen=True)`.
- Input validation correctly bounds payload sizes (32KB content max, 64 keys metadata max), rejects ASCII control chars and reserved sentinels (`__unset__`), and bounds identifiers to 128 characters.

## Phase 5 — Background Workers (`mesa_workers/`)

**Scope:** `ingestion_worker.py`, `maintenance.py`, `maintenance_pagerank.py`, `rem_cycle.py`

**1. Worker Architectures & Resiliency:**
- All background loops (`maintenance`, `maintenance_pagerank`, `rem_cycle`) correctly implement graceful sleep loops (e.g. `await asyncio.wait_for(self._stop_event.wait(), ...)`) and handle `asyncio.TimeoutError` and `asyncio.CancelledError` without crashing. 
- Core processing logics (e.g. `_run_cycle`, `process_cold_path`) are wrapped in `try...except Exception as exc:` blocks, ensuring that a single malformed record or database hiccup does not kill the long-running worker.

**2. Transient Retries (`ingestion_worker`):**
- Transient LLM errors are handled elegantly: `_acomplete_with_retry` is wrapped with `tenacity.retry(wait_exponential(...))` and uses an explicit circuit breaker (`llm_circuit_breaker.is_open`) to prevent overwhelming downstream models.

**3. Physical Deletes & Vacuum (`maintenance`):**
- The `MaintenanceWorker` correctly executes physical SQL deletes: `DELETE FROM nodes WHERE invalid_at < ?`.
- It executes `VACUUM;` on a dedicated raw synchronous `sqlite3` connection (with `PRAGMA busy_timeout=30000;`) bypassing the async connection pool, which is the correct architecture to prevent `database is locked` errors during vacuum.

**4. Event Loop Blocking & Heavy Computation:**
- `maintenance_pagerank.py` wraps the heavy `scipy.sparse` matrix math operations inside `await loop.run_in_executor(None, compute_damped_pagerank, ...)`, which protects the main FastAPI event loop from being blocked by synchronous CPU-bound operations.
- `rem_cycle.py` executes LLM consensus asynchronously (`acomplete`), so it does not block the loop.
