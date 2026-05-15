# Installation Guide

## Deployment Modes

MESA supports two deployment profiles. Choose based on your hardware and use case.

### Lightweight API Mode (~200 MB)

**Best for:** API servers, CI pipelines, quick prototyping, and environments without GPU access.

Uses pre-trained LLM providers (Groq, Claude, Ollama) for extraction and skips the local REBEL model entirely. All triplet extraction is delegated to remote LLM calls.

```bash
# Clone and set up
git clone https://github.com/Yasou13/MESA.git
cd MESA
python3 -m venv venv && source venv/bin/activate

# Install lightweight dependencies only
pip install -r requirements-core.txt
```

**What's included:**
- SQLite (aiosqlite) for raw log persistence
- LanceDB for vector similarity search
- NetworkX for in-memory knowledge graph
- FastAPI + Uvicorn for REST API
- Prometheus client for observability
- LLM provider SDKs (Anthropic, OpenAI, Ollama, Groq)

**What's NOT included:**
- PyTorch / Transformers (no local model inference)
- REBEL extraction model (1.8 GB download skipped)
- spaCy language models

---

### Full ML Mode (~3 GB)

**Best for:** Production deployments with GPU access, offline environments, and maximum extraction throughput.

Includes the local REBEL seq2seq model for zero-cost triplet extraction, with LLM fallback for records REBEL cannot process.

```bash
# Clone and set up
git clone https://github.com/Yasou13/MESA.git
cd MESA
python3 -m venv venv && source venv/bin/activate

# Install full ML dependencies
pip install -r requirements-ml.txt

# Download the spaCy multilingual NER model
python -m spacy download xx_ent_wiki_sm
```

**Additional dependencies:**
- PyTorch (CPU or CUDA)
- Transformers (Hugging Face)
- Babelscape/rebel-large model (auto-downloaded on first use)
- spaCy with `xx_ent_wiki_sm` model
- scikit-learn, scipy, pyod (anomaly detection)

> [!TIP]
> If running on a machine with an NVIDIA GPU, install the CUDA-enabled PyTorch variant for 10–50× faster REBEL extraction:
> ```bash
> pip install torch --index-url https://download.pytorch.org/whl/cu121
> ```

---

## Environment Variables

MESA uses `python-dotenv` to load configuration from a `.env` file. Copy the example to get started:

```bash
cp .env.example .env
```

### Required Variables

| Variable | Description | Example |
|---|---|---|
| `MESA_LLM_PROVIDER` | Active LLM provider | `openai_compatible`, `claude`, `ollama`, `mock` |
| `LLM_API_KEY` | API key for the selected provider | `gsk_abc123...` |
| `LLM_BASE_URL` | Base URL for OpenAI-compatible endpoints | `https://api.groq.com/openai/v1` |
| `LLM_MODEL_NAME` | Model identifier | `llama-3.1-8b-instant` |

### Optional Variables

| Variable | Description | Default |
|---|---|---|
| `MESA_MAX_RAM_MB` | Override automatic RAM detection (MB) | Auto-detected |
| `MESA_REBEL_DEVICE` | Force REBEL model device | `cpu` (auto-detects CUDA) |
| `MESA_MAX_BATCH_TOKENS` | Max tokens per consolidation batch | `6000` |
| `MESA_ECOD_ANOMALY_THRESHOLD` | Novelty detection sensitivity (0–1) | `0.80` |

### Provider-Specific Configuration

#### Groq (Recommended for Free Tier)

```env
MESA_LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=gsk_your_groq_key_here
LLM_MODEL_NAME=llama-3.1-8b-instant
```

#### Claude (Anthropic)

```env
MESA_LLM_PROVIDER=claude
LLM_API_KEY=sk-ant-your_anthropic_key_here
```

#### Ollama (Local, Self-Hosted)

```env
MESA_LLM_PROVIDER=ollama
LLM_MODEL_NAME=mistral
```

> [!NOTE]
> Ensure Ollama is running locally (`ollama serve`) before starting the MESA API.

#### Mock (Development / Testing)

```env
MESA_LLM_PROVIDER=mock
```

No API key required. Uses deterministic SHA-256 embeddings and simplified triplet extraction. Ideal for CI and local development.

---

## Docker Deployment

### Build and Run

```bash
docker compose up --build -d
```

### Verify

```bash
curl http://localhost:8000/health
# → {"status": "HEALTHY", "counters": {}, "gauges": {}}
```

### Persistent Storage

The `docker-compose.yml` maps `./storage:/app/storage` to persist all databases across container restarts:

| File | Engine | Purpose |
|---|---|---|
| `raw_log.db` | SQLite (WAL) | Append-only CMB journal |
| `vector_index.lance/` | LanceDB | Embedding similarity index |
| `knowledge_graph.db` | SQLite | Graph node/edge persistence |
| `kg_history.rocks/` | RocksDB | MVCC history archive |

---

## Verifying Installation

```bash
# Run the full test suite (159+ tests)
pytest tests/ -q

# Type checking
mypy mesa_memory/ --ignore-missing-imports --explicit-package-bases

# Code quality
black --check mesa_memory/ tests/
ruff check mesa_memory/ tests/

# Run the tutorial script
python examples/hello_mesa.py
```

A successful installation produces:

```
159 passed in ~37s
```
