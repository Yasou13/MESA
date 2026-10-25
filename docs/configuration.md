# Configuration reference

Source: `mesa_memory/config.py`, `load_runtime_profile` and `MesaConfig`.
Defaults below are field declarations, not evidence of an operational model.

## Startup and precedence

`MESA_RUNTIME_PROFILE` and `MESA_STORAGE_ROOT` are required by the runtime
loader. Profiles: `combined`, `test-isolated`. V4 requires one combined writer.
Storage aliases are tried in order ROOT, PATH, DIR, DB_PATH; a `.db` filename
selects its parent. Reject filesystem/home/repository root. Test-isolated
requires an absolute child of `MESA_RUNTIME_LAB_ROOT` and forbids models,
external providers and dotenv.

No implicit `.env` search. `MESA_LOAD_DOTENV=true` requires explicit
`MESA_DOTENV_PATH`; startup loads and reparses the explicit environment. The explicit dotenv file wins over existing environment values (`override=True`);
this can change profile/storage/provider flags, so review the file before enabling it. Pydantic constructor fields override
environment in direct programmatic configuration. Unaliased fields use their
uppercase field name; comments mentioning MESA_* do not create aliases.

`MESA_API_KEY` bootstrap or an existing active registry key is needed at startup.
A bootstrap key requires the server-authored `MESA_PRINCIPAL_ID`; use offline
admin provisioning for ACL. `MESA_PORT` defaults to 8000; `MESA_LOG_LEVEL` to INFO
and `MESA_LOG_FORMAT` to json. Secrets must be injected privately.

## Providers and capacity

Model and external-provider flags default false. Validation defaults to
mode 0; mode 1 or 2 must be set explicitly via `MESA_TIER3_MODE`. Mode 2
needs distinct A/B validator identities. Extraction and validation are separate.
`MESA_EMBEDDING_ENABLED` and `MESA_EXTRACTION_ENABLED` each default to
`MESA_MODEL_ENABLED`. A combined runtime accepts writes with both off and finds
them through BM25. Disabled VECTOR lanes end `SKIPPED`; enabling embedding later
backfills them at startup using the same embedding identity. Backfill failures
leave committed records searchable.
Local identity defaults to Magibu 768-d normalized; external text-embedding-3-small
requires setting dimension to 1536. Do not enable external flag and keep an
unrelated dimension. Nemotron-3 profile requires 2048 and `nemotron-qpass-v1`,
with query/passage input types. Provider/model/revision/dimension/normalization
define a fenced embedding space; switch through reindex/rebuild, not live fallback.

Install optional ML/provider SDK extras for the selected backend. With models
disabled semantic generation is intentionally unavailable. `MESA_ZERO_COST_MODE`
selects local Ollama configuration but preserves validator settings and does
not prove an air gap. RAM budget precedence is explicit MAX_RAM_MB/MAX_MEMORY_BYTES,
cgroups, psutil, then 1 GiB safe fallback; this is not an RSS guarantee.
Queue per-tenant bounds must fit within global bounds. `.env.example` deliberately
uses smaller sample global and per-tenant bounds than code defaults.

## Minimal V4 provider setup

Start with the disposable root, key and grants from the
[quickstart](getting-started.md#provision-before-startup), with the server stopped.
For an external OpenAI-compatible extraction + embedding setup, install
`uv sync --locked --python 3.11 --extra adapters` (no local ML extra is needed).
Select models supported by your endpoint. The following is an example pairing,
not evidence of a real-provider run:

```bash
mkdir -p "$HOME/mesa-doc-demos"
export MESA_STORAGE_ROOT="$(mktemp -d "$HOME/mesa-doc-demos/v4-XXXXXX")"
export MESA_PRINCIPAL_ID=docs-principal
export MESA_API_KEY="$(.venv/bin/mesa-v4-admin issue-key --principal docs-principal)"
.venv/bin/mesa-v4-admin grant-role --principal docs-principal --tenant docs-tenant --role OWNER
.venv/bin/mesa-v4-admin grant-agent --principal docs-principal --agent docs-agent --permission SESSION_CREATE
export MESA_RUNTIME_PROFILE=combined
export MESA_LOAD_DOTENV=false
export MESA_MODEL_ENABLED=true
export MESA_EXTERNAL_PROVIDER_ENABLED=true
export MESA_TIER3_MODE=0
export MESA_LLM_MODEL=openai:gpt-4o-mini
export MESA_LLM_API_KEY="${OPENAI_API_KEY:?Supply the provider key privately first}"
export MESA_EMBEDDING_MODEL=openai:text-embedding-3-small
export MESA_EMBEDDING_BASE_URL=https://api.openai.com/v1
export MESA_EMBEDDING_API_KEY="$MESA_LLM_API_KEY"
export MESA_EMBEDDING_DIMENSION=1536
export MESA_EMBEDDING_NORMALIZED=true
export MESA_EMBEDDING_VERSION=v1
```

A root without active vector artifacts adopts a changed embedding identity at
startup and backfills skipped VECTOR lanes. A root with active vectors requires
an explicit rebuild before changing identity. Keep `MESA_API_KEY` (MESA access) separate
from `MESA_LLM_API_KEY` / `MESA_EMBEDDING_API_KEY` (provider access). Modes 1/2 need
the validator A or A/B settings from the table below. Extraction is configured
separately and mode 0 still calls it.

`AdapterFactory` parses `provider:model` (`ollama`, `openai`, `openai_compatible`,
`anthropic`, `mock`). Embeddings support local sentence-transformers (`ml` extra) or
OpenAI-compatible endpoints (`adapters` extra); Claude/Ollama extraction does not
automatically configure embeddings. Native Ollama extraction requires a running
endpoint and an already available model; MESA does not provision it.

Select embeddings with `MESA_EMBEDDING_MODEL=provider:model`: `local:...`,
`ollama:qwen3-embedding:4b`, `openai:text-embedding-3-small`, or
`openai_compatible:nvidia/nemotron-3-embed-1b`. The second colon stays in the
model name. `MESA_EMBEDDING_BASE_URL` overrides the endpoint; Ollama otherwise
uses `<MESA_OLLAMA_URL>/v1` and needs no key for a local server. Without
`MESA_EXTERNAL_PROVIDER_ENABLED=true`, Ollama must use a loopback URL; OpenAI
and OpenAI-compatible providers require the flag. Endpoint/key settings retain
the `MESA_LLM_BASE_URL` / `MESA_LLM_API_KEY` fallbacks for those providers.

With embedding enabled and `MESA_EMBEDDING_DIMENSION` unset, the server and
rebuild runner embed one probe at startup and use its length for storage and
admission. Probe failure stops startup and names the model and URL. Setting the
dimension explicitly skips the probe; embedding disabled with no dimension
keeps 768 and makes no embedding requests. The removed embedding provider,
external-model and local-model variables stop runtime startup with the
replacement setting. Nemotron still requires `MESA_EMBEDDING_VERSION=nemotron-qpass-v1`
and 2048 dimensions (explicit or detected).

The built-in `mock` adapter returns no extracted facts; it is not a source-backed
V4 demo. Contract tests inject deterministic fact and embedding providers
explicitly. Models-disabled startup, deterministic V4 tests and real-provider
V4 runs are different verification levels. V3 raw-memory processing requires
embeddings too; models-disabled boot does not demonstrate insert/search success.

## Environment file and first startup

The [template](../.env.example) uses a standalone `combined` owner with models
disabled. Copy it only if needed, replace the storage path with an absolute,
writable dedicated directory, and supply issued credentials privately. To load
it explicitly, set `MESA_LOAD_DOTENV=true` and `MESA_DOTENV_PATH` to its absolute
path **in the launching shell**; setting them only inside the file cannot enable
loading. Do not load dotenv in `test-isolated`. The quickstart's exported settings
work without creating any `.env` file.

Fresh combined startup creates/migrates SQLite and initializes LanceDB/Kùzu;
there is no manual fresh-database migration step. Keep old storage migrations
separate: use the [upgrade procedure](RUNBOOK.md#upgrade-v080), never delete an
existing database to resolve a startup error. Startup failures occur in the server terminal before
readiness; check the named variable, writable path or missing selected extra
there. Do not paste secrets or entire environment files into bug reports.

## Declared MesaConfig fields

Field-declaration reference; compare it with `MesaConfig` when configuration changes.
`required` or derived defaults are explicit. Legacy controls are present for V3;

| Python field | Environment name | Declared default |
|---|---|---|
| `llm_provider` | `LLM_PROVIDER` | `'claude'` |
| `openai_api_key` | `OPENAI_API_KEY` | `None` |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | `None` |
| `llm_model` | `MESA_LLM_MODEL` | `''` |
| `llm_base_url` | `MESA_LLM_BASE_URL` | `None` |
| `llm_api_key` | `MESA_LLM_API_KEY` | `None` |
| `llm_timeout_seconds` | `MESA_LLM_TIMEOUT_SECONDS` | `60.0` |
| `embedding_base_url` | `MESA_EMBEDDING_BASE_URL` | `None` |
| `embedding_api_key` | `MESA_EMBEDDING_API_KEY` | `None` |
| `extraction_thinking` | `MESA_EXTRACTION_THINKING` | `False` |
| `extraction_max_tokens` | `MESA_EXTRACTION_MAX_TOKENS` | `4096` |
| `llm_embedding_model_name` | `LLM_EMBEDDING_MODEL` | `'sentence-transformers/all-MiniLM-L6-v2'` |
| `embedding_model` | `MESA_EMBEDDING_MODEL` | `'local:magibu/embeddingmagibu-200m'` |
| `model_enabled` | `MESA_MODEL_ENABLED` | `False` |
| `tier3_mode` | `MESA_TIER3_MODE` | `None` |
| `tier3_llm_provider_a` | `MESA_TIER3_LLM_PROVIDER_A` | `None` |
| `tier3_llm_model_name_a` | `MESA_TIER3_LLM_MODEL_A` | `None` |
| `tier3_llm_provider_b` | `MESA_TIER3_LLM_PROVIDER_B` | `None` |
| `tier3_llm_model_name_b` | `MESA_TIER3_LLM_MODEL_B` | `None` |
| `external_provider_enabled` | `MESA_EXTERNAL_PROVIDER_ENABLED` | `False` |
| `embedding_dimension` | `MESA_EMBEDDING_DIMENSION` | detected at startup when enabled and unset; otherwise `768` |
| `embedding_version` | `MESA_EMBEDDING_VERSION` | `'v1'` |
| `embedding_model_revision` | `MESA_EMBEDDING_MODEL_REVISION` | `None` |
| `embedding_normalized` | `MESA_EMBEDDING_NORMALIZED` | `True` |
| `context_window_limit` | `CONTEXT_WINDOW_LIMIT` | `8000` |
| `lancedb_memory_limit_bytes` | `LANCEDB_MEMORY_LIMIT_BYTES` | `1073741824` |
| `ram_allocation_fraction` | `RAM_ALLOCATION_FRACTION` | `0.18` |
| `consolidation_batch_size` | `CONSOLIDATION_BATCH_SIZE` | `20` |
| `consolidation_idle_timeout` | `CONSOLIDATION_IDLE_TIMEOUT` | `10` |
| `histogram_max_size` | `HISTOGRAM_MAX_SIZE` | `10000` |
| `metrics_admission_threshold` | `METRICS_ADMISSION_THRESHOLD` | `0.8` |
| `metrics_divergence_threshold` | `METRICS_DIVERGENCE_THRESHOLD` | `0.5` |
| `rrf_k` | `MESA_RRF_K` | `60` |
| `rrf_vector_weight` | `MESA_RRF_VECTOR_WEIGHT` | `1.0` |
| `rrf_bm25_weight` | `MESA_RRF_BM25_WEIGHT` | `1.0` |
| `rrf_assertion_weight` | `MESA_RRF_ASSERTION_WEIGHT` | `1.0` |
| `rrf_graph_weight` | `MESA_RRF_GRAPH_WEIGHT` | `1.0` |
| `t_route` | `MESA_T_ROUTE` | `0.85` |
| `v4_rebuild_enabled` | `MESA_V4_REBUILD_ENABLED` | `False` |
| `v4_graph_timeout_seconds` | `MESA_V4_GRAPH_TIMEOUT_SECONDS` | `15.0` |
| `crossencoder_pool_multiplier` | `MESA_CROSSENCODER_POOL_MULTIPLIER` | `3` |
| `legal_domain_mode` | `MESA_LEGAL_DOMAIN_MODE` | `False` |
| `extraction_lang` | `MESA_EXTRACTION_LANG` | `'tr'` |
| `zero_cost_mode` | `MESA_ZERO_COST_MODE` | `False` |
| `ollama_url` | `MESA_OLLAMA_URL` | `'http://localhost:11434'` |
| `ollama_timeout_seconds` | `MESA_OLLAMA_TIMEOUT_SECONDS` | `300.0` |
| `ollama_num_ctx` | `MESA_OLLAMA_NUM_CTX` | `8192` |
| `retry_max_attempts` | `MESA_RETRY_MAX_ATTEMPTS` | `5` |
| `retry_min_wait_sec` | `MESA_RETRY_MIN_WAIT_SEC` | `2` |
| `retry_max_wait_sec` | `MESA_RETRY_MAX_WAIT_SEC` | `60` |
| `circuit_breaker_threshold` | `MESA_CIRCUIT_BREAKER_THRESHOLD` | `10` |
| `circuit_breaker_cooldown_sec` | `MESA_CIRCUIT_BREAKER_COOLDOWN` | `60.0` |
| `storage_path` | `MESA_STORAGE_PATH` | `derived from cwd; runtime requires explicit storage root` |
| `human_review_queue_path` | `MESA_HUMAN_REVIEW_QUEUE_PATH` | `'' (server puts this file under MESA_STORAGE_ROOT; variable applies only outside the server)` |
| `dead_letter_queue_path` | `MESA_DEAD_LETTER_QUEUE_PATH` | `'' (server puts this file under MESA_STORAGE_ROOT; variable applies only outside the server)` |
| `queue_max_pending_records` | `MESA_QUEUE_MAX_PENDING_RECORDS` | `50000` |
| `queue_max_pending_bytes` | `MESA_QUEUE_MAX_PENDING_BYTES` | `2147483648` |
| `queue_max_pending_records_per_tenant` | `MESA_QUEUE_MAX_PENDING_RECORDS_PER_TENANT` | `50000` |
| `queue_max_pending_bytes_per_tenant` | `MESA_QUEUE_MAX_PENDING_BYTES_PER_TENANT` | `2147483648` |
| `queue_max_in_flight_records` | `MESA_QUEUE_MAX_IN_FLIGHT_RECORDS` | `32` |
| `queue_max_in_flight_records_per_tenant` | `MESA_QUEUE_MAX_IN_FLIGHT_RECORDS_PER_TENANT` | `8` |
| `queue_max_retry_pending_records` | `MESA_QUEUE_MAX_RETRY_PENDING_RECORDS` | `10000` |
| `queue_max_retry_pending_records_per_tenant` | `MESA_QUEUE_MAX_RETRY_PENDING_RECORDS_PER_TENANT` | `10000` |
| `queue_max_single_record_bytes` | `MESA_QUEUE_MAX_SINGLE_RECORD_BYTES` | `8388608` |
| `queue_retry_after_seconds` | `MESA_QUEUE_RETRY_AFTER_SECONDS` | `5` |
| `readiness_projection_backlog_max` | `MESA_READINESS_PROJECTION_BACKLOG_MAX` | `1000` |
| `readiness_projection_dead_letter_max` | `MESA_READINESS_PROJECTION_DEAD_LETTER_MAX` | `0` |
| `readiness_projection_stuck_max` | `MESA_READINESS_PROJECTION_STUCK_MAX` | `0` |
| `readiness_cleanup_backlog_max` | `MESA_READINESS_CLEANUP_BACKLOG_MAX` | `1000` |
| `readiness_cleanup_blocked_max` | `MESA_READINESS_CLEANUP_BLOCKED_MAX` | `0` |
| `readiness_orphan_registry_max` | `MESA_READINESS_ORPHAN_REGISTRY_MAX` | `0` |

## MCP and operator variables

`mesa_mcp/configuration.py` owns separate `MCPSettings`; see [MCP](../README_MCP.md).
V3 stdio defaults to `MESA_USE_V4=false`; direct V4 requires explicit tenant,
workspace and dataset settings plus workspace root and binding authorization.
MCP general context defaults to 2500 tokens/max8000; Core HTTP context defaults
to 2048; Codex binding profile caps are separate (8 memories/2500 tokens).
`MESA_GATEWAY_ENCRYPTION_KEY` and `MESA_GATEWAY_CONTROL_DB` are operator inputs.

Rebuild uses reviewed trusted/storage/work roots and full-root offline scope;
see [runbook](v4-rebuild-runbook.md). Defaults and example pairings do not certify
provider availability or quality.

Canonical V4 insertion requires the combined processing consumer. A combined
runtime without models stores documents and finds them with BM25; vector search
starts when embedding is enabled (`degraded_sources` shows `vector` until then);
503 `canonical_processing_unavailable` happens only on a non-combined profile.
