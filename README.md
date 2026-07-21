<div align="center">

# MESA — Memory Engine for Structured Agents

[![MESA CI](https://github.com/Yasou13/MESA/actions/workflows/ci.yml/badge.svg)](https://github.com/Yasou13/MESA/actions)
[![codecov](https://codecov.io/gh/Yasou13/MESA/graph/badge.svg)](https://codecov.io/gh/Yasou13/MESA)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-blue.svg)
![Version](https://img.shields.io/badge/Version-0.6.1-green.svg)

**Enterprise-grade cognitive memory engine for autonomous AI agents.**
Ingest → Validate → Extract → Store → Retrieve — with dual-LLM consensus designed to mitigate hallucination cascades.

</div>

---

## ⚡ Quickstart (Local Installation)

Install the core package first. Add the optional groups only when your local
development workflow needs them:

```bash
git clone https://github.com/Yasou13/MESA.git
cd MESA
python -m pip install -e .
# Optional local ML models and external-provider SDKs:
python -m pip install -e ".[ml,adapters]"
```

## 🐳 Quickstart (Docker) — 60 Seconds

Copy-paste this to get a running MESA instance with zero local dependencies:

```bash
git clone https://github.com/Yasou13/MESA.git
cd MESA
export MESA_API_KEY=local-dev-key
export MESA_PRINCIPAL_ID=local-compose-principal
docker compose config --quiet
docker compose up --build -d
```

> **Runtime profile:** Compose starts separate API and worker roles with the
> persistent named `mesa-data` volume. It deliberately sets
> `MESA_MODEL_ENABLED=false` and `MESA_EXTERNAL_PROVIDER_ENABLED=false`; this
> quickstart neither loads `.env` nor enables an LLM provider. Configure a
> reviewed non-Compose runtime profile only when model or external-provider
> access is required.

Verify it's running:

```bash
curl --fail -H "X-API-Key: $MESA_API_KEY" http://localhost:8000/health
# → {"status": "ok", ...}
```

MESA is now live at **`http://localhost:8000`** with Swagger docs at [`/docs`](http://localhost:8000/docs).

---

## 🔑 API Examples (v3)

All endpoints require the `X-API-Key` header. This must match the `MESA_API_KEY` value in your `.env` file.

### Insert a Memory

```bash
curl -X POST http://localhost:8000/v3/memory/insert \
  -H "Content-Type: application/json" \
  -H "X-API-Key: local-dev-key" \
  -d '{
    "agent_id": "analyst_1",
    "session_id": "session_001",
    "content": "Tesla Q4 2025 revenue exceeded $25B, up 12% YoY."
  }'
# → {"status": "queued", "log_id": 1, "processing_mode": "async"}
```

The insert endpoint returns **202 Accepted** in <50ms. Heavy processing (ECOD anomaly detection, triple extraction, dual-LLM consensus) happens asynchronously on the cold path.

### Check Ingestion Status

```bash
curl "http://localhost:8000/v3/memory/status/1?agent_id=analyst_1" \
  -H "X-API-Key: local-dev-key"
# → {"log_id": 1, "status": "processed"}
```

### Search Memories

```bash
curl -X POST http://localhost:8000/v3/memory/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: local-dev-key" \
  -d '{
    "agent_id": "analyst_1",
    "query": "What was Tesla Q4 revenue?",
    "limit": 5
  }'
# → {"context": "...", "retrieved_nodes": [...], "metrics": {"latency_ms": 12}}
```

### Purge Memories (Tombstoning)

```bash
curl -X DELETE http://localhost:8000/v3/memory/purge \
  -H "Content-Type: application/json" \
  -H "X-API-Key: local-dev-key" \
  -d '{
    "agent_id": "analyst_1",
    "scope": "agent"
  }'
# → {"status": "purged", "deleted_records_count": 42}
```

---

## 🐍 Python SDK

```python
from mesa_api.schemas import MemoryInsertRequest, MemorySearchRequest
from mesa_client.client import MesaClient

client = MesaClient(base_url="http://localhost:8000", api_key="local-dev-key")

# Insert
response = client.insert(MemoryInsertRequest(
    agent_id="analyst_1",
    session_id="s1",
    content="Tesla Q4 revenue: $25B, up 12% YoY.",
))
print(f"Queued: log_id={response.log_id}")

# Search
results = client.search(MemorySearchRequest(
    agent_id="analyst_1",
    query="Tesla revenue",
    limit=5,
))
print(f"Found {results.total} results")
for r in results.results:
    print(f"  {r.entity_name} (score: {r.score:.4f})")
```

---

## 🤖 Integrations: Claude Desktop (MCP)

MESA includes a built-in [Model Context Protocol](https://modelcontextprotocol.io/) server (`mesa_mcp.server`) that exposes memory insert and search as MCP tools. This lets Claude Desktop read from and write to your local MESA instance natively.

### Setup

1. **Start MESA** (Docker or local — must be running on `localhost:8000`).

2. **Add to your Claude Desktop config** (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, or `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "mesa-memory": {
      "command": "python",
      "args": ["-m", "mesa_mcp.server"],
      "cwd": "/absolute/path/to/MESA",
      "env": {
        "MESA_BASE_URL": "http://localhost:8000/v3",
        "MESA_API_KEY": "local-dev-key"
      }
    }
  }
}
```

3. **Restart Claude Desktop.** You'll see two new tools available:

| MCP Tool | Description |
|---|---|
| `record_memory` | Store a new memory (maps to `POST /v3/memory/insert`) |
| `search_memory` | Retrieve relevant memories (maps to `POST /v3/memory/search`) |

Claude can now persist facts across conversations and recall them on demand through your local MESA instance.

> [!TIP]
> Set the `agent_id` to `"claude-desktop"` for clean tenant isolation. Each conversation can use its own `session_id` for scoped retrieval.

---

## Why MESA?

Traditional agent memory is a flat buffer of text. MESA replaces that with a **multi-module pipeline** that gates every incoming record through statistical novelty checks, anomaly detection, and asymmetric dual-LLM cross-validation before committing structured knowledge triplets to a persistent graph. The result: agents that remember *accurately*, not just *recently*.

| Capability | MESA | LangChain Memory | MemGPT |
|---|---|---|---|
| **Hallucination Mitigation** | Dual-LLM Consensus + Fail-safe Discard | Prompt-based | Self-correction |
| **Validation Architecture** | 3-Tier Statistical + LLM Pipeline | None | Prompt-based |
| **Knowledge Graph** | Automated REBEL + LLM Triplet Extraction (Turkish/English) | Manual | None |
| **Zero-Cost Mode** | Native 100% local execution via Ollama (`MESA_ZERO_COST_MODE`) | External | External |
| **Tenant Isolation** | Mandatory `agent_id` RLS on every query | None | None |
| **Session Lifecycle APIs** | Native `/session/start`, `/context`, `/end` endpoints | None | Implicit |
| **Fault Tolerance** | Circuit Breaker + DLQ + Exponential Backoff | Try/Catch | Retry Decorator |
| **Local-First** | Yes (SQLite WAL, LanceDB, KùzuDB) | Cloud-dependent | Cloud-dependent |
| **Observability** | Prometheus + structured JSON logs | Basic logging | Basic logging |

---

## Features & Capabilities

MESA v0.6.1 introduces advanced cognitive memory features:
1. **Multi-Stage CrossEncoder Reranking**: Substantially improves retrieval precision using Stage 2 learned reranking (`cross-encoder/ms-marco-MiniLM-L-6-v2`).
2. **MESA Benchmark Suite**: Rigorous multi-tier evaluation pipeline with Apple-to-Apple competitor integrations (Zep, Letta, Mem0).
3. **Phase 4.1: Self-Healing Graphs**: Async Damped PageRank for hallucination quarantine.
4. **Phase 4.2: Cognitive Salience**: Spreading Activation routed through KuzuDB using `OPTIONAL MATCH`.
5. **Phase 4.3: Continuous Learning**: Blue/Green Procrustes vector alignment with persistent SQLite WAL to prevent phantom writes.
6. **Zero-Cost Mode**: 100% local, air-gapped execution orchestrating `OllamaAdapter`, local embeddings, and REBEL without any cloud dependencies (`MESA_ZERO_COST_MODE=true`).

---

## Architecture Overview

MESA is designed around a **Triple Storage Engine** architecture to maximize scalability and guarantee data integrity:
1. **SQLite:** Handles relational metadata and multi-worker Write-Ahead Log (WAL) orchestration.
2. **LanceDB:** Handles vector embeddings with Blue/Green deployment and Procrustes alignment.
3. **KuzuDB:** Handles the Knowledge Graph and Cognitive Salience routing (Spreading Activation).

```mermaid
graph TB
    subgraph "API Layer"
        T["FastAPI v3<br/>Daemon :8000"] --> INS["POST /v3/memory/insert"]
        T --> SCH["POST /v3/memory/search"]
        T --> PRG["DELETE /v3/memory/purge"]
    end

    subgraph "Ingestion Layer"
        INS --> B["Valence Motor"]
        B --> C{"Tier-1<br/>Fitness Gate"}
        C -->|DISCARD| X1["❌ Rejected"]
        C -->|PASS| D["ECOD Anomaly Detection"]
        D --> E{"Tier-2<br/>Novelty Check"}
        E -->|DISCARD| X1
        E -->|UNCERTAIN| F["Tier-3 Deferred Queue"]
    end

    subgraph "Consolidation Layer"
        F --> G["ConsolidationLoop"]
        G --> H["REBEL Extractor<br/>(Local, Zero-Cost)"]
        H --> I["Dual-LLM<br/>Cross-Validation"]
        I -->|AGREE| J["GraphWriter"]
        I -->|DISAGREE| X2["❌ Discarded<br/>(Fail-Safe)"]
    end

    subgraph "Storage Layer"
        J --> K["SQLite WAL<br/>+ FTS5"]
        J --> L["LanceDB<br/>Vector Index"]
        J --> M["KùzuDB<br/>Knowledge Graph"]
    end

    subgraph "Retrieval Layer"
        SCH --> O["MemoryDAO Search"]
        O --> P["Vector Search"]
        O --> Q["Graph Search<br/>(PPR + k-hop)"]
        O --> R["FTS5 Lexical<br/>Pre-Filter"]
        P --> S["Stage 1: RRF Fusion"]
        Q --> S
        R --> S
        S --> T["Stage 2: CrossEncoder Reranking"]
        T --> RES["Ranked Results"]
    end

    subgraph "Background Workers"
        MW["MaintenanceWorker<br/>(VACUUM, Hard-Delete)"]
        REM["rem_cycle.py<br/>(Consolidation)"]
    end

    E -->|ADMIT| K
    E -->|ADMIT| L

    style T fill:#0f3460,stroke:#16213e,color:#fff
    style J fill:#1a1a2e,stroke:#0f3460,color:#fff
    style RES fill:#1a1a2e,stroke:#e94560,color:#fff
    style X1 fill:#3d0000,stroke:#e94560,color:#fff
    style X2 fill:#3d0000,stroke:#e94560,color:#fff
    style MW fill:#3d0000,stroke:#e94560,color:#fff
```

---

## Local Development (without Docker)

### 1. Install

`pyproject.toml` is the only dependency manifest. The core package avoids heavy
ML dependencies unless explicitly requested.

```bash
git clone https://github.com/Yasou13/MESA.git
cd MESA
python3 -m venv venv && source venv/bin/activate
python -m pip install -e .
```

**Optional Heavy ML Models:** If you need the local REBEL transformer model for English-only offline triplet extraction, install the optional package:
```bash
python -m pip install -e ".[ml]"
```

**Optional LLM Adapters:** The core package avoids installing third-party LLM SDKs to keep the footprint small. If you intend to use cloud providers (OpenAI, Anthropic, Groq, LiteLLM) or Ollama instead of pure local logic, install the adapters group:
```bash
python -m pip install -e ".[adapters]"
```

### 2. Configure

```bash
export MESA_RUNTIME_PROFILE=api-only
export MESA_STORAGE_ROOT=/absolute/path/to/mesa-data
export MESA_LOAD_DOTENV=false
export MESA_MODEL_ENABLED=false
export MESA_EXTERNAL_PROVIDER_ENABLED=false
export MESA_API_KEY=local-dev-key
export MESA_PRINCIPAL_ID=local-api-principal
```

### 3. Launch

> **WARNING:** `make dev` is not a production-parity command. For the separate
> API/worker topology, use the Compose quickstart above or the operator
> runbook in [`docs/installation.md`](docs/installation.md).

```bash
uvicorn mesa_memory.api.server:app --host 0.0.0.0 --port 8000 --reload
# → http://127.0.0.1:8000/docs  (Swagger UI)
# → http://127.0.0.1:8000/health
```

---

## API Endpoints (v3)

| Method | Path | Description |
|---|---|---|
| `POST` | `/v3/memory/insert` | Atomically admit durable worker ingestion (<50ms) |
| `POST` | `/v3/memory/search` | Hybrid vector + graph + FTS5 retrieval |
| `GET` | `/v3/memory/status/{log_id}` | Query cold-path processing status |
| `DELETE` | `/v3/memory/purge` | Tombstoning only (hard-delete is background-only) |
| `POST` | `/v3/memory/session/start` | Generate a new session with tenant isolation |
| `GET` | `/v3/memory/session/{session_id}/context` | Retrieve episodic + graph context scoped to session |
| `POST` | `/v3/memory/session/{session_id}/end` | Terminate session and trigger final consolidation |
| `GET` | `/health/init` | Container orchestration readiness probe (returns 200 when workers are alive) |
| `GET` | `/health` | System status and database health check |
| `GET` | `/metrics` | Prometheus scrape endpoint |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MESA_RUNTIME_PROFILE` | *(required)* | `api-only`, `worker-only`, `combined` veya yalnız testler için `test-isolated` |
| `MESA_STORAGE_ROOT` | *(required)* | Uygulamanın sahip olduğu mutlak ve yazılabilir storage dizini |
| `MESA_LOAD_DOTENV` | `false` | `.env` yüklemeyi yalnız açıkça izin verilmiş profilde etkinleştirir |
| `MESA_MODEL_ENABLED` | `false` | Yerel model yüklemeyi etkinleştirir; Compose bunu kapatır |
| `MESA_EXTERNAL_PROVIDER_ENABLED` | `false` | Haricî LLM sağlayıcı kullanımını etkinleştirir; Compose bunu kapatır |
| `MESA_API_KEY` | *(required)* | API authentication key (sent via `X-API-Key` header) |
| `MESA_PRINCIPAL_ID` | *(required)* | API key ile ilişkilendirilen sunucu tarafı principal |
| `MESA_PRINCIPAL_TYPE` | `SERVICE` | Principal türü |
| `MESA_PRINCIPAL_STATUS` | `active` | Principal durumu |
| `LLM_API_KEY` | *(provider profile)* | Yalnız external-provider erişimi açık, gözden geçirilmiş profiller için sağlayıcı anahtarı |
| `MESA_ZERO_COST_MODE` | `false` | Yerel Ollama/embedding seçimini ister; Compose profili bunu etkinleştirmez |

---

## Running Tests

```bash
# Full test suite
pytest tests/ -q

# With coverage
pytest tests/ --cov=mesa_memory --cov=mesa_api --cov=mesa_storage --cov-report=term-missing --ignore=tests/bench

# Type checking
mypy mesa_memory mesa_storage mesa_workers mesa_api mesa_client --ignore-missing-imports --explicit-package-bases

# Formatting
black --check mesa_memory/ mesa_api/ mesa_storage/ tests/
ruff check .

# Evaluation pipeline
python -m mesa_evals.evals        # Run 30-entry synthetic benchmark
python -m mesa_evals.gatekeeper   # CI/CD gate (exit 0 = PASS)
```

---

## Known Limitations

> [!WARNING]
> **Understand these constraints before deploying to production.**

### KùzuDB Graph Scalability

MESA exclusively leverages **KùzuDB** for graph topology, enabling infinite out-of-core scaling and entirely eliminating node-related RAM exhaustion.

### LLM Provider Rate Limits

When using Groq's free tier as the LLM backend, you may hit **30 requests/minute** rate limits during consolidation batches. Mitigations:
- Reduce `consolidation_batch_size` in your `.env` or config.
- Use the `mock` provider for local development and testing.
- Deploy with a paid plan or switch to a self-hosted Ollama instance.

### CPU-Only REBEL Extraction

The REBEL model (`Babelscape/rebel-large`, 1.8 GB) runs at **~2–5 seconds per record on CPU**. For high-throughput workloads:
- Set `MESA_REBEL_DEVICE=cuda` if a GPU is available.
- Set `MESA_REBEL_ENABLED=false` to use the LLM-only fallback (zero model download, uses your configured Tier-3 provider).
- The system automatically falls back to LLM-based extraction when REBEL fails, so extraction never blocks the pipeline.

### Current Status

As of v0.6.1, Hot Path (API ingestion/search) and Cold Path (consolidation workers) concurrency are fully isolated via atomic Saga dual-writes, executor-offloaded embeddings, and strict input sanitization (including hard 1MB payload limits to prevent memory exhaustion DoS attacks). Furthermore, the system now supports safe multi-worker asynchronous writes via a persistent SQLite WAL queue, and automated background WAL checkpointing, preventing phantom writes and disk bloat during continuous ingestion.

---

## Project Structure

```
MESA/
├── mesa_api/             # Headless FastAPI v3 REST server + Pydantic schemas
├── mesa_client/          # Python SDK (sync/async) + LangChain adapter
├── mesa_evals/           # Golden Dataset, evaluation runner, CI/CD gatekeeper
├── mesa-benchmark/       # Comprehensive evaluation suite for competitor benchmarking
├── mesa_memory/
│   ├── adapter/          # LLM provider adapters (Claude, Ollama, Mock)
│   ├── api/              # FastAPI server entrypoint + auth middleware
│   ├── consolidation/    # Batch orchestration + graph writing
│   ├── extraction/       # REBEL triplet extraction pipeline
│   ├── observability/    # Prometheus metrics + structured logging
│   ├── retrieval/        # Hybrid vector + graph retrieval
│   ├── schema/           # Pydantic CMB schema
│   ├── security/         # RBAC access control + input sanitisation
│   └── valence/          # ECOD anomaly detection + novelty scoring
├── mesa_mcp/             # Model Context Protocol server (Claude Desktop)
├── mesa_storage/         # Triple Storage Engine
│   ├── dao.py            # Orchestration & WAL queueing
│   ├── kuzu_provider.py  # Graph Storage
│   └── vector_engine.py  # Vector Storage
├── mesa_workers/         # Cold-path ingestion worker, MaintenanceWorker, rem_cycle.py
├── tests/                # pytest suite + benchmarks
├── examples/             # Tutorial scripts (hello_mesa.py, legal_assistant.py)
├── Dockerfile            # Production container
├── docker-compose.yml    # API + worker Compose deployment
├── pyproject.toml        # Package metadata + dependency ranges
├── uv.lock               # Reproducible resolved dependency graph
└── SECURITY.md            # Security disclosure policy
```

---

## Contributing

We welcome contributions! Please follow the **Fork → Feature Branch → Pytest → Pull Request** workflow. Ensure all tests pass and code is formatted with `black` and `ruff` before submitting.

## License

This project is licensed under the [MIT License](LICENSE) — Copyright © 2026 MESA Core Team.
