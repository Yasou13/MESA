# Changelog

All notable changes to the MESA project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-05-15

### Added

- **FastAPI REST Layer** (`mesa_memory/api/server.py`): Production-ready HTTP server exposing `/ingest`, `/query`, `/health`, and `/metrics` endpoints with Pydantic request/response schemas and proper lifecycle management.
- **Prometheus Observability**: Module-level `Counter`, `Histogram`, and `Gauge` metrics for valence tier hits, admission rates, consolidation batch duration, and cross-validation divergence. Exposed via `GET /metrics` for Prometheus scraping.
- **CMB Fitness Scoring** (`mesa_memory/valence/fitness.py`): Composite scoring function (0.0–1.0) based on content word density, token cost efficiency, and novelty score. Integrated into the `/ingest` pipeline.
- **Multi-Hop Graph Traversal** (`mesa_memory/retrieval/graph_traversal.py`): `find_path()` function using `networkx.shortest_path` with configurable `max_hops` and graceful `NetworkXNoPath` handling.

- **Persistent ValenceMotor State**: `save_state()` and `load_state()` methods using `aiosqlite` to persist `_ewmad_threshold` and `memory_count` across process restarts via a `valence_state` key-value table.
- **StorageFacade Integration**: `initialize_all()` now accepts an optional `valence_motor` parameter to automatically restore cognitive state on startup.
- **Docker Containerisation**: `Dockerfile` (python:3.10-slim, multi-layer caching, built-in healthcheck) and `docker-compose.yml` (persistent volume mapping, env passthrough, `unless-stopped` restart policy).
- **Dependency Separation**: Split `requirements.txt` into `requirements-core.txt` (lightweight API mode, ~200 MB) and `requirements-ml.txt` (full ML mode with PyTorch/REBEL, ~3 GB).
- **Tutorial Script** (`examples/hello_mesa.py`): 3-scenario tutorial demonstrating single ingest, concurrent multi-agent ingestion, and multi-hop graph traversal.
- **Documentation**: Enterprise-grade `README.md` with Mermaid architecture diagram, `docs/installation.md`, `docs/api-reference.md`, and `benchmarks/v0.2.0_results.md`.

### Changed

- **DeterministicMockAdapter** (`mesa_memory/adapter/mock.py`): Extracted from inline demo script into a standalone module. Now handles Tier-3 validation prompts (`decision`/`justification`), batch extraction prompts (`triplets`/`record_index`), and context-query prompts. Deterministic SHA-256 embeddings (384-dim, unit-normalised).
- **AdapterFactory** (`mesa_memory/adapter/factory.py`): Expanded to route `claude`, `ollama`, and `mock` providers alongside `openai_compatible`. Accepts `Optional[str]` provider parameter with config fallback.
- **REBEL Pipeline** (`mesa_memory/extraction/rebel_pipeline.py`): Added hardware detection with CPU latency warning, `_rebel_failures` tracking list, and improved exception handling that logs `"REBEL failed, triggering LLM fallback"` instead of generic errors.
- **ObservabilityLayer**: Prometheus metrics moved from instance-level (`__init__`) to module-level singletons to prevent `ValueError: Duplicated timeseries in CollectorRegistry` when `pytest` creates multiple instances.

### Fixed

- **mypy strict compliance**: Resolved type errors — incompatible dict types in `DeterministicMockAdapter`, missing `_rebel_failures` annotation, and implicit `Optional` in `AdapterFactory.get_adapter`.
- **RBAC grant semantics**: Tutorial and demo scripts now grant only `WRITE` (which implicitly includes `READ` per `check_access` logic) instead of overwriting `WRITE` with `READ` due to the primary key constraint.
- **Prometheus duplicate registry**: Module-level metric singletons prevent re-registration crashes across test fixtures and multi-instance scenarios.

---

## [0.1.0] - 2026-05-11

### Added

- **9-Module Architecture**: Complete cognitive memory pipeline from ingestion through retrieval.
- **Cognitive Memory Block (CMB) Schema** (`mesa_memory/schema/cmb.py`): Pydantic model with UUID7 IDs, embedding vectors, fitness scores, affective state, and resource cost tracking.
- **Valence Motor** (`mesa_memory/valence/core.py`): 3-tier admission gate with bootstrap cosine threshold, EWMAD drift calibration, and configurable recalibration intervals.
- **ECOD Anomaly Detection** (`mesa_memory/valence/novelty.py`): Embedding-space novelty scoring using cosine similarity distributions.
- **Drift Recalibration** (`mesa_memory/valence/drift.py`): Exponentially weighted threshold adaptation with sigmoid blending and configurable clamping.
- **REBEL Extraction Pipeline** (`mesa_memory/extraction/rebel_pipeline.py`): Thread-safe singleton holder for the Babelscape/rebel-large model with `<triplet>/<subj>/<obj>` token parsing.
- **Consolidation Loop** (`mesa_memory/consolidation/loop.py`): Batch orchestration with dual-LLM cross-validation, truncated JSON recovery, and salience-based record ordering.
- **Batch Orchestrator** (`mesa_memory/consolidation/batch_orchestrator.py`): Token-aware batch splitting with configurable `max_batch_tokens`.
- **Tier-3 Validator** (`mesa_memory/consolidation/tier3_validator.py`): Asymmetric dual-LLM consensus with composite similarity scoring.
- **Graph Writer** (`mesa_memory/consolidation/graph_writer.py`): Atomic triplet commit with MVCC node versioning and hub-degree threshold checks.
- **Storage Facade** (`mesa_memory/storage/__init__.py`): Unified interface to SQLite raw log, LanceDB vector index, and NetworkX knowledge graph with atomic cross-layer consistency and orphan reconciliation.
- **Raw Log Storage** (`mesa_memory/storage/raw_log.py`): Async SQLite with WAL mode, soft-delete, and tier-3 deferred record fetching.
- **Vector Storage** (`mesa_memory/storage/vector_index.py`): LanceDB integration with dynamic RAM budgeting, memory limit enforcement, and kNN search.
- **NetworkX Graph Provider** (`mesa_memory/storage/graph/networkx_provider.py`): In-memory graph with SQLite persistence, MVCC node versioning, BFS subgraph extraction, and Personalized PageRank.
- **BaseGraphProvider ABC** (`mesa_memory/storage/graph/base.py`): Provider-agnostic async contract for all graph backends.
- **Hybrid Retriever** (`mesa_memory/retrieval/hybrid.py`): RRF-fused vector similarity + Personalized PageRank retrieval with cold-start fallback and token-limited context formatting.
- **Query Analyser** (`mesa_memory/retrieval/core.py`): spaCy-based entity extraction and query normalisation.
- **RBAC Access Control** (`mesa_memory/security/rbac.py`): SQLite-backed permission enforcement with advisory prompt injection detection.
- **Observability Layer** (`mesa_memory/observability/metrics.py`): Structured JSON logging with counters, gauges, and histograms.
- **Configuration System** (`mesa_memory/config.py`): Pydantic-settings with hierarchical RAM detection (psutil → env var → cgroup → safe-mode fallback).
- **LLM Adapters**: `ClaudeAdapter`, `OllamaAdapter`, and `OpenAICompatibleAdapter` with local embedding fallback via `sentence-transformers/all-MiniLM-L6-v2`.
- **Test Suite**: 159+ tests covering unit, integration, P0 hotfixes, and performance benchmarks.
- **CI Pipeline**: GitHub Actions workflow with Black, Ruff, mypy, pytest + coverage, and Codecov upload.

[0.2.0]: https://github.com/Yasou13/MESA/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Yasou13/MESA/releases/tag/v0.1.0
