<div align="center">

# MESA — Memory Engine for Structured Agents

[![MESA CI](https://github.com/Yasou13/MESA/actions/workflows/python-app.yml/badge.svg)](https://github.com/Yasou13/MESA/actions)
[![codecov](https://codecov.io/gh/Yasou13/MESA/graph/badge.svg)](https://codecov.io/gh/Yasou13/MESA)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-blue.svg)
![Version](https://img.shields.io/badge/Version-0.3.0-green.svg)

**Enterprise-grade cognitive memory engine for autonomous AI agents.**
Ingest → Validate → Extract → Store → Retrieve — with dual-LLM consensus designed to mitigate hallucination cascades.

</div>

---

## Why MESA?

Traditional agent memory is a flat buffer of text. MESA replaces that with a **multi-module pipeline** that gates every incoming record through statistical novelty checks, anomaly detection, and asymmetric dual-LLM cross-validation before committing structured knowledge triplets to a persistent graph. The result: agents that remember *accurately*, not just *recently*.

| Capability | MESA | LangChain Memory | MemGPT |
|---|---|---|---|
| **Hallucination Mitigation** | Dual-LLM Consensus + Fail-safe Discard | Prompt-based | Self-correction |
| **Validation Architecture** | 3-Tier Statistical + LLM Pipeline | None | Prompt-based |
| **Knowledge Graph** | Automated REBEL + LLM Triplet Extraction | Manual | None |
| **Tenant Isolation** | Mandatory `agent_id` RLS on every query | None | None |
| **Local-First** | Yes (SQLite WAL, LanceDB, NetworkX) | Cloud-dependent | Cloud-dependent |
| **Observability** | Prometheus + structured JSON logs | Basic logging | Basic logging |

---

## Architecture Overview

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
        J --> M["NetworkX<br/>Knowledge Graph"]
    end

    subgraph "Retrieval Layer"
        SCH --> O["MemoryDAO Search"]
        O --> P["Vector Search"]
        O --> Q["Graph Search<br/>(PPR + k-hop)"]
        O --> R["FTS5 Lexical<br/>Pre-Filter"]
        P --> S["RRF Fusion"]
        Q --> S
        R --> S
        S --> RES["Ranked Results"]
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

## 5-Minute Quickstart

### 1. Install

```bash
git clone https://github.com/Yasou13/MESA.git
cd MESA
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-core.txt
```

> **Core dependencies installed:** `aiosqlite`, `fastapi`, `lancedb`, `httpx`, `pydantic`, `uvicorn`, `networkx`, `pyarrow`, and all supporting packages. See `requirements-core.txt` for the full manifest or `pyproject.toml` for version ranges.

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your provider credentials, or use mock mode:
# MESA_LLM_PROVIDER=mock
# MESA_API_KEY=your-secret-key
```

### 3. Launch the API Server (Daemon Mode)

MESA v0.3.0 runs as a **headless FastAPI daemon**. All interaction flows through the REST API:

```bash
uvicorn mesa_api.router:app --host 0.0.0.0 --port 8000 --reload
# → http://127.0.0.1:8000/docs  (Swagger UI)
# → http://127.0.0.1:8000/health
```

### 4. Insert & Search via cURL

```bash
# Insert a memory
curl -X POST http://localhost:8000/v3/memory/insert \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-key" \
  -d '{
    "agent_id": "analyst_1",
    "session_id": "session_001",
    "content": "Tesla Q4 2025 revenue exceeded $25B, up 12% YoY."
  }'

# Search memories
curl -X POST http://localhost:8000/v3/memory/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-key" \
  -d '{
    "agent_id": "analyst_1",
    "query": "What was Tesla Q4 revenue?",
    "limit": 5
  }'
```

### 5. Use the Python SDK

```python
from mesa_client.client import MesaClient

client = MesaClient(base_url="http://localhost:8000", api_key="your-secret-key")

# Insert
client.insert(agent_id="analyst_1", session_id="s1", content="Tesla Q4 revenue: $25B")

# Search
results = client.search(agent_id="analyst_1", query="Tesla revenue")
for r in results:
    print(r.content, r.score)
```

### 6. External Integration (MCP & LangChain)

MESA provides deep integration with modern agent stacks:

- **Model Context Protocol (MCP):** Connect to MESA using Claude Desktop or any MCP-compatible agent via the `mesa_mcp` package. This exposes memory retrieval as a standard context provider.
- **LangChain:** Use the `MesaLangchainRetriever` found in the `mesa_client` package to embed MESA's asynchronous dual-engine memory straight into your LangChain pipelines.

### 7. Docker Deployment

```bash
docker compose up --build -d
# API available at http://localhost:8000
# Storage persisted to ./storage/
```

---

## API Endpoints (v3)

| Method | Path | Description |
|---|---|---|
| `POST` | `/v3/memory/insert` | Queue memory ingestion (fire-and-forget, <150ms) |
| `POST` | `/v3/memory/search` | Hybrid vector + graph + FTS5 retrieval |
| `DELETE` | `/v3/memory/purge` | Soft-delete only (hard-delete is background-only) |
| `GET` | `/health` | System status and readiness check |
| `GET` | `/metrics` | Prometheus scrape endpoint |

---

## Running Tests

```bash
# Full test suite (409 tests)
pytest tests/ -q

# With coverage
pytest tests/ --cov=mesa_memory --cov=mesa_api --cov=mesa_storage --cov-report=term-missing --ignore=tests/bench

# Type checking
mypy mesa_memory/ mesa_api/ mesa_storage/ --ignore-missing-imports --explicit-package-bases

# Formatting
black --check mesa_memory/ mesa_api/ mesa_storage/ tests/
ruff check mesa_memory/ mesa_api/ mesa_storage/ tests/

# Evaluation pipeline
python -m mesa_evals.evals        # Run 30-entry synthetic benchmark
python -m mesa_evals.gatekeeper   # CI/CD gate (exit 0 = PASS)
```

---

## Known Limitations

> [!WARNING]
> **Understand these constraints before deploying to production.**

### NetworkX Graph Scalability

The default graph provider uses **in-memory NetworkX** backed by SQLite persistence. This works well for graphs up to ~100K nodes. For larger knowledge bases, a dedicated graph database backend is recommended.

### LLM Provider Rate Limits

When using Groq's free tier as the LLM backend, you may hit **30 requests/minute** rate limits during consolidation batches. Mitigations:
- Reduce `consolidation_batch_size` in your `.env` or config.
- Use the `mock` provider for local development and testing.
- Deploy with a paid plan or switch to a self-hosted Ollama instance.

### CPU-Only REBEL Extraction

The REBEL model (`Babelscape/rebel-large`, 1.8 GB) runs at **~2–5 seconds per record on CPU**. For high-throughput workloads:
- Set `MESA_REBEL_DEVICE=cuda` if a GPU is available.
- The system automatically falls back to LLM-based extraction when REBEL fails, so extraction never blocks the pipeline.

---

## Project Structure

```
MESA/
├── mesa_api/             # Headless FastAPI v3 REST server + Pydantic schemas
├── mesa_client/          # Python SDK (sync/async) + LangChain adapter
├── mesa_evals/           # Golden Dataset, evaluation runner, CI/CD gatekeeper
├── mesa_memory/
│   ├── adapter/          # LLM provider adapters (Claude, Ollama, Mock)
│   ├── consolidation/    # Batch orchestration + graph writing
│   ├── extraction/       # REBEL triplet extraction pipeline
│   ├── observability/    # Prometheus metrics + structured logging
│   ├── retrieval/        # Hybrid vector + graph retrieval
│   ├── schema/           # Pydantic CMB schema
│   ├── security/         # RBAC access control + input sanitisation
├── mesa_mcp/             # Model Context Protocol external integration
├── mesa_storage/         # MemoryDAO, AsyncEngine (SQLite WAL), LanceDB
├── mesa_workers/         # MaintenanceWorker, rem_cycle.py
├── tests/                # pytest suite (409 tests + benchmarks)
├── examples/             # Tutorial scripts (hello_mesa.py, legal_assistant.py)
├── Dockerfile            # Production container
├── docker-compose.yml    # Single-command deployment
├── pyproject.toml        # Package metadata + dependency ranges
├── requirements-core.txt # Lightweight API dependencies (~200 MB)
└── requirements-ml.txt   # Full ML dependencies (PyTorch/REBEL, ~3 GB)
```

---

## Contributing

We welcome contributions! Please follow the **Fork → Feature Branch → Pytest → Pull Request** workflow. Ensure all tests pass and code is formatted with `black` and `ruff` before submitting.

## License

This project is licensed under the [MIT License](LICENSE) — Copyright © 2026 MESA Core Team.
