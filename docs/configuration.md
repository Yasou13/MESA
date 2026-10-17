# Configuration reference

Source: `mesa_memory/config.py`, `load_runtime_profile` and `MesaConfig`.
Defaults below are field declarations, not evidence of an operational model.

## Startup and precedence

`MESA_RUNTIME_PROFILE` and `MESA_STORAGE_ROOT` are required by the runtime
loader. Profiles: `api-only`, `worker-only`, `combined`, `test-isolated`. V4
requires one combined writer; V3 split roles use a separate root.
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

Model and external-provider flags default false. Enabling models defaults
validation to mode 2 unless `MESA_TIER3_MODE` explicitly selects 0/1/2; mode 2
needs distinct A/B validator identities. Extraction and validation are separate.
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

## Declared MesaConfig fields

Field-declaration reference; compare it with `MesaConfig` when configuration changes.
`required` or derived defaults are explicit. Legacy controls are present for V3;
CrossEncoder/REBEL are not the canonical V4 ranking/extraction path.

| Python field | Environment name | Declared default |
|---|---|---|
| `llm_provider` | `LLM_PROVIDER` | `'claude'` |
| `openai_api_key` | `OPENAI_API_KEY` | `None` |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | `None` |
| `mesa_llm_provider` | `MESA_LLM_PROVIDER` | `'openai_compatible'` |
| `llm_base_url` | `LLM_BASE_URL` | `None` |
| `llm_api_key` | `LLM_API_KEY` | `None` |
| `embedding_base_url` | `MESA_EMBEDDING_BASE_URL` | `None` |
| `embedding_api_key` | `MESA_EMBEDDING_API_KEY` | `None` |
| `llm_model_name` | `LLM_MODEL_NAME` | `'llama-3.1-8b-instant'` |
| `extraction_provider` | `MESA_EXTRACTION_PROVIDER` | `'ollama'` |
| `extraction_model` | `MESA_EXTRACTION_MODEL` | `'qwen3:1.7b'` |
| `extraction_thinking` | `MESA_EXTRACTION_THINKING` | `False` |
| `extraction_max_tokens` | `MESA_EXTRACTION_MAX_TOKENS` | `4096` |
| `llm_embedding_model_name` | `LLM_EMBEDDING_MODEL` | `'sentence-transformers/all-MiniLM-L6-v2'` |
| `embedding_provider` | `MESA_EMBEDDING_PROVIDER` | `'openai_compatible'` |
| `external_embedding_model` | `MESA_EXTERNAL_EMBEDDING_MODEL` | `'text-embedding-3-small'` |
| `llm_timeout_seconds` | `LLM_TIMEOUT_SECONDS` | `20.0` |
| `model_enabled` | `MESA_MODEL_ENABLED` | `False` |
| `tier3_mode` | `MESA_TIER3_MODE` | `None` |
| `tier3_llm_provider_a` | `MESA_TIER3_LLM_PROVIDER_A` | `None` |
| `tier3_llm_model_name_a` | `MESA_TIER3_LLM_MODEL_A` | `None` |
| `tier3_llm_provider_b` | `MESA_TIER3_LLM_PROVIDER_B` | `None` |
| `tier3_llm_model_name_b` | `MESA_TIER3_LLM_MODEL_B` | `None` |
| `external_provider_enabled` | `MESA_EXTERNAL_PROVIDER_ENABLED` | `False` |
| `embedding_dimension` | `MESA_EMBEDDING_DIMENSION` | `768` |
| `embedding_version` | `MESA_EMBEDDING_VERSION` | `'v1'` |
| `embedding_model_revision` | `MESA_EMBEDDING_MODEL_REVISION` | `None` |
| `embedding_normalized` | `MESA_EMBEDDING_NORMALIZED` | `True` |
| `tiebreaker_latency_threshold_ms` | `TIEBREAKER_LATENCY_THRESHOLD_MS` | `500.0` |
| `bootstrap_cosine_threshold` | `BOOTSTRAP_COSINE_THRESHOLD` | `0.75` |
| `context_window_limit` | `CONTEXT_WINDOW_LIMIT` | `8000` |
| `lancedb_memory_limit_bytes` | `LANCEDB_MEMORY_LIMIT_BYTES` | `1073741824` |
| `spacy_language_model` | `SPACY_LANGUAGE_MODEL` | `'xx_ent_wiki_sm'` |
| `ram_allocation_fraction` | `RAM_ALLOCATION_FRACTION` | `0.18` |
| `entity_similarity_threshold` | `ENTITY_SIMILARITY_THRESHOLD` | `0.8` |
| `relation_similarity_threshold` | `RELATION_SIMILARITY_THRESHOLD` | `0.7` |
| `consolidation_batch_size` | `CONSOLIDATION_BATCH_SIZE` | `20` |
| `hub_degree_threshold` | `HUB_DEGREE_THRESHOLD` | `5` |
| `consolidation_idle_timeout` | `CONSOLIDATION_IDLE_TIMEOUT` | `10` |
| `uncertain_zone_lower_bound` | `UNCERTAIN_ZONE_LOWER_BOUND` | `0.3` |
| `anchor_interval` | `ANCHOR_INTERVAL` | `3` |
| `human_review_max_size` | `HUMAN_REVIEW_MAX_SIZE` | `1000` |
| `max_batch_tokens` | `MAX_BATCH_TOKENS` | `6000` |
| `truncation_max_retries` | `TRUNCATION_MAX_RETRIES` | `2` |
| `histogram_max_size` | `HISTOGRAM_MAX_SIZE` | `10000` |
| `metrics_admission_threshold` | `METRICS_ADMISSION_THRESHOLD` | `0.8` |
| `metrics_divergence_threshold` | `METRICS_DIVERGENCE_THRESHOLD` | `0.5` |
| `recalibration_interval` | `RECALIBRATION_INTERVAL` | `50` |
| `max_embedding_history` | `MAX_EMBEDDING_HISTORY` | `500` |
| `ecod_anomaly_threshold` | `ECOD_ANOMALY_THRESHOLD` | `0.8` |
| `drift_sigmoid_weight` | `DRIFT_SIGMOID_WEIGHT` | `-10.0` |
| `drift_ewmad_alpha` | `DRIFT_EWMAD_ALPHA` | `0.2` |
| `drift_ewmad_momentum` | `DRIFT_EWMAD_MOMENTUM` | `0.8` |
| `drift_clamp_min` | `DRIFT_CLAMP_MIN` | `0.5` |
| `drift_clamp_max` | `DRIFT_CLAMP_MAX` | `0.9` |
| `rrf_k` | `MESA_RRF_K` | `60` |
| `rrf_vector_weight` | `MESA_RRF_VECTOR_WEIGHT` | `1.0` |
| `rrf_bm25_weight` | `MESA_RRF_BM25_WEIGHT` | `1.0` |
| `rrf_assertion_weight` | `MESA_RRF_ASSERTION_WEIGHT` | `1.0` |
| `rrf_graph_weight` | `MESA_RRF_GRAPH_WEIGHT` | `1.0` |
| `t_route` | `MESA_T_ROUTE` | `0.85` |
| `cold_start_min_nodes` | `COLD_START_MIN_NODES` | `10` |
| `cold_start_fitness_weight` | `COLD_START_FITNESS_WEIGHT` | `0.5` |
| `cold_start_distance_weight` | `COLD_START_DISTANCE_WEIGHT` | `0.5` |
| `ppr_alpha` | `PPR_ALPHA` | `0.15` |
| `v4_rebuild_enabled` | `MESA_V4_REBUILD_ENABLED` | `False` |
| `v4_graph_timeout_seconds` | `MESA_V4_GRAPH_TIMEOUT_SECONDS` | `15.0` |
| `crossencoder_enabled` | `MESA_CROSSENCODER_ENABLED` | `False` |
| `crossencoder_model` | `MESA_CROSSENCODER_MODEL` | `'cross-encoder/ms-marco-MiniLM-L-6-v2'` |
| `crossencoder_pool_multiplier` | `MESA_CROSSENCODER_POOL_MULTIPLIER` | `3` |
| `legal_domain_mode` | `MESA_LEGAL_DOMAIN_MODE` | `False` |
| `local_embedding_model` | `MESA_LOCAL_EMBEDDING_MODEL` | `'magibu/embeddingmagibu-200m'` |
| `rebel_enabled` | `MESA_REBEL_ENABLED` | `False` |
| `extraction_lang` | `MESA_EXTRACTION_LANG` | `'tr'` |
| `zero_cost_mode` | `MESA_ZERO_COST_MODE` | `False` |
| `ollama_url` | `MESA_OLLAMA_URL` | `'http://localhost:11434'` |
| `retry_max_attempts` | `MESA_RETRY_MAX_ATTEMPTS` | `5` |
| `retry_min_wait_sec` | `MESA_RETRY_MIN_WAIT_SEC` | `2` |
| `retry_max_wait_sec` | `MESA_RETRY_MAX_WAIT_SEC` | `60` |
| `circuit_breaker_threshold` | `MESA_CIRCUIT_BREAKER_THRESHOLD` | `10` |
| `circuit_breaker_cooldown_sec` | `MESA_CIRCUIT_BREAKER_COOLDOWN` | `60.0` |
| `storage_path` | `MESA_STORAGE_PATH` | `derived from cwd; runtime requires explicit storage root` |
| `human_review_queue_path` | `MESA_HUMAN_REVIEW_QUEUE_PATH` | `''` |
| `dead_letter_queue_path` | `MESA_DEAD_LETTER_QUEUE_PATH` | `''` |
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

Canonical V4 insertion requires the model-enabled combined processing consumer.
Model-disabled boot is topology/compatibility smoke and rejects V4 writes with
`503 canonical_processing_unavailable`; it is not a V4 insert/search demonstration.
