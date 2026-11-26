# Configuration reference

## Run without models

| Setting | Default and usage |
|---|---|
| `MESA_STORAGE_ROOT` | `mesa start` uses `<MESA_HOME>/data`. Set a dedicated writable directory for the direct runtime launcher. |
| `MESA_API_KEY`, `MESA_PRINCIPAL_ID` | `mesa start` and Docker provision local credentials. Supply both for direct-launch bootstrap when the registry has no active key. |
| `MESA_MODEL_ENABLED` | `false`. Enables embeddings by default when set to `true`. |
| `MESA_EMBEDDING_ENABLED`, `MESA_EXTRACTION_ENABLED` | Embedding follows `MESA_MODEL_ENABLED` when unset. Extraction defaults to `false`. |
| `MESA_EMBEDDING_MODEL`, `MESA_EMBEDDING_DIMENSION` | `local:BAAI/bge-m3`. Dimension is detected at startup when embeddings are enabled and defaults to `1024` when disabled. |
| `MESA_RERANK_ENABLED` | `false`. Set `true` to rerank search results with the configured model. |
| `MESA_LEGAL_RULES_ENABLED` | `true`. Enables Turkish legal rule processing during ingestion and search. |
| `MESA_LOG_LEVEL` | `INFO`. |

On a fresh local setup, `mesa start` provisions a MESA API key and stores
documents for BM25 search. An empty `MESA_LLM_MODEL` leaves the chat adapter
unconfigured. Embeddings and fact extraction are independent switches.

## Local model setup

Run `mesa setup` to configure embeddings and chat for `mesa start`. You can
choose `none` for either. Embedding choices are local `BAAI/bge-m3`, Ollama,
and an OpenAI-compatible API. Chat uses an OpenAI-compatible endpoint. Setup
checks each enabled model once, then saves the settings in `$MESA_HOME/.env`
(`~/.mesa/.env` by default) with owner-only permissions. For local BGE-M3,
install `mesa-memory[ml]` and cache `BAAI/bge-m3` before running setup.

For scripts, add `--non-interactive` and select modes with `--embedding` and
`--llm`. Omitted modes default to `none`. Use `--embedding-model` and
`--embedding-base-url` for an embedding API, and `--llm-model` and
`--llm-base-url` for chat. Supply API keys privately through
`MESA_EMBEDDING_API_KEY` and `MESA_LLM_API_KEY`. Local endpoints can accept
requests without a configured key. Run `mesa setup --help` for the command
options and restart `mesa start` to apply the saved configuration.

For an existing vector store, use the same home and storage root as `mesa start`.
Changing the embedding space requires an offline rebuild. Submit and drain an
operation under the current configuration, then stop the server as described in
the [rebuild runbook](v4-rebuild-runbook.md). Setup can run the offline command
after you confirm and supply the operation ID and work root. Automated runs use
`--yes-rebuild`, `--rebuild-operation-id`, and `--rebuild-work-root`. Setup
saves the new settings after a successful rebuild.

## Advanced

### Startup and precedence

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

`mesa start` and the Docker image provision an active local registry key and
credentials automatically; no bootstrap variables are needed in local mode.
`mesa start --host` controls the bind address (default `127.0.0.1`); the
credentials' client URL always uses `127.0.0.1`. The Docker image binds
`0.0.0.0` internally, while Compose publishes only to the host's loopback.
The direct runtime launcher requires `MESA_API_KEY` bootstrap or an existing active registry key at startup.
A bootstrap key requires the server-authored `MESA_PRINCIPAL_ID`; use offline
admin provisioning for ACL. `MESA_PORT` defaults to 8000; `MESA_LOG_LEVEL` to INFO
and `MESA_LOG_FORMAT` to json. Secrets must be injected privately.
Settings for retired features are ignored. The server logs a warning for each
retired key found in its startup environment.

### Providers and capacity

`MESA_CHUNKING=auto` (default) splits new document revisions at Turkish article
headings when at least three occur at line starts. The preamble stays separate;
long articles use the existing paragraph/sentence splitting with a 512-token
limit. All slices preserve the exact source text. Other documents keep the same
plain chunks; `MESA_CHUNKING=plain` selects that plan for every document. Retrying
a pending upload preserves the plan matching its already admitted chunk texts,
even after restarting or changing this setting. Active revisions are unchanged.
If an admitted prefix fits two different plans, the retry returns 409 without
appending chunks, because the stored text cannot identify the original plan.

`MESA_LEGAL_RULES_ENABLED=true` enables Turkish legal rule processing. In auto
chunking mode, ordinary article chunks inherit the document's law number from
`metadata.law_number` or a `Kanun Numarası`, `Kanunun Numarası` or `Kanun No.`
header in the first 3,000 characters. Continuation pieces keep that article's
identity; preambles, GEÇİCİ/EK articles and plain plans receive none. Setting the
switch to false disables automatic identity assignment for new chunks. It also
disables legal processing during search, including citation resolution, the
exact-match slot, decision künye promotion, and legal ranking factors.
Ordinary lexical and vector retrieval still run.
Search ignores stored legal identities and item snapshots while the switch is
off and omits their legal result annotations; it does not erase them. Stored
identities are immutable on resume. Any 3–5 digit law number is accepted by the
typed identity API; known numbers retain their existing statute code (6098 →
TBK). Unknown law names and inconsistent number/code pairs are rejected.
Numbered queries such as `7201 sayılı Kanunun 21. maddesi` use the existing
typed legal candidate lane and ranking factors. Document fallback assertions
keep their document subject, `madde` relation and chunk object; automatic
article identity changes only the typed columns.

Newly admitted chunks also keep versioned citation, decision-reference, date,
amount, duration and outcome items. Search returns them in
`retrieval_provenance.legal_items` as `{version, items}`. Item `start`/`end`
offsets refer to the original chunk, including when a result quotes a shorter
extracted fact. Items remain separate from SPO facts and do not enter fusion
scores. Decision references can use the bounded exact-match slot below.
Disabling the switch prevents new item snapshots and hides existing snapshots
from search results. Existing snapshots remain stored. Purging a document
erases these source quotes too.

An exact law-and-article query puts at most three matching chunks first, in
document order. Other results retain their fused order (or reranked order when
reranking is enabled). The match must exist in the authorized search scope.
A query's docket and decision numbers can also match a decision's own künye:
a stored `decision_ref` starting within the first 300 characters of chunk zero.
Its first chunk shares the same three-chunk limit with article matches, in
document order. Later quotations do not teach a decision's identity. The map
uses the same tenant, dataset, agent and time scope cache as law names; an
unmatched reference leaves result order unchanged.
The scope cache learns law names from titles and first-chunk law headers of
documents with trusted article identities; it never learns them from another
tenant, dataset, agent or time scope. Names accept case and Turkish ı/i variants,
possessive suffixes, and `K.` for `Kanunu`. The longest exact name from that scope
takes precedence over an overlapping ontology alias. Ambiguous store names
shared by different law numbers are ignored. No static law-name table is added.

`MESA_RERANK_ENABLED=false` keeps the existing fused ranking and does not import
ML packages. Enabling it requires `pip install "mesa-memory[ml]"`; startup checks
that the package is installed, and the model loads lazily on the first search.
`MESA_RERANK_MODEL` defaults to `BAAI/bge-reranker-v2-m3` (Apache-2.0), with
`MESA_RERANK_DEPTH=50` (range 1–50). The scorer uses authorized full chunk texts
after releasing the search connection, in a worker thread, then returns the
requested number of results. Tied scores retain fused order. Results keep their
fusion fields, including `rrf_score` and `final_score`, and add `rerank_score`.
The first search includes model-load latency; subsequent searches reuse it.

Model and external-provider flags default false. Validation is always
deterministic (Mode 0; zero LLM validator dependencies).
`MESA_EMBEDDING_ENABLED` defaults to `MESA_MODEL_ENABLED`; `MESA_EXTRACTION_ENABLED`
defaults to `false` (D46). A combined runtime accepts writes with both off and finds
them through BM25. Disabled VECTOR lanes end `SKIPPED`; enabling embedding later
backfills them at startup using the same embedding identity. Backfill failures
leave committed records searchable.

The former graph projection store was removed in v0.9.5. Any `kuzu_db` files
left on disk in older stores are ignored by the runtime and can be deleted by
hand after verifying backups. Retired graph settings (`MESA_GRAPH_ENABLED`,
`MESA_RRF_GRAPH_WEIGHT`, `MESA_V4_GRAPH_TIMEOUT_SECONDS`) log a startup warning
and are ignored.

Local identity defaults to `BAAI/bge-m3` 1024-d normalized (upgraded from
`local:magibu/embeddingmagibu-200m` 768-d; existing stores created before v0.9.3 must keep
`MESA_EMBEDDING_MODEL=local:magibu/embeddingmagibu-200m` and `MESA_EMBEDDING_DIMENSION=768`
unless rebuilt); external text-embedding-3-small
requires setting dimension to 1536. Do not enable external flag and keep an
unrelated dimension. Nemotron-3 profile requires 2048 and `nemotron-qpass-v1`,
with query/passage input types. Provider/model/revision/dimension/normalization
define a fenced embedding space; switch through reindex/rebuild, not live fallback.

Local sentence-transformer embeddings and reranking require the `ml` extra. Cache the selected model before startup. RAM budget precedence is explicit MAX_RAM_MB/MAX_MEMORY_BYTES,
cgroups, psutil, then 1 GiB safe fallback; this is not an RSS guarantee.
Queue per-tenant bounds must fit within global bounds. `.env.example` deliberately
uses smaller sample global and per-tenant bounds than code defaults.

### Chat models

For a direct runtime or a manually edited dotenv file, use these settings.
`mesa setup` writes the equivalent values for `mesa start`.

| Setting | Usage |
|---|---|
| `MESA_LLM_MODEL` | Model name served by the endpoint, such as `qwen3:8b`. Leave empty to disable built-in answering. |
| `MESA_LLM_BASE_URL` | OpenAI-compatible API base URL, such as `http://127.0.0.1:11434/v1` for Ollama. An unset URL uses the OpenAI endpoint. |
| `MESA_LLM_API_KEY` | Provider key. Required for remote chat endpoints. A local endpoint can accept the adapter's placeholder key. |
| `MESA_LLM_TIMEOUT_SECONDS` | Timeout per provider request. Defaults to 60 seconds. |
| `MESA_EXTERNAL_PROVIDER_ENABLED` | Defaults to `false`. Set `true` to allow remote providers. Setup enables it when a selected endpoint is remote. |

The same chat adapter serves grounded answers, summaries, optional query
planning and optional fact extraction. Set `MESA_EXTRACTION_ENABLED=true`
separately when you want extraction. Authorized passages and questions go to
the selected chat server for answering. Summary calls send the selected
revision's text to that server.

A local endpoint uses HTTP or HTTPS with a host of `localhost`, `127.0.0.1`,
`::1`, or `host.docker.internal`. Local Ollama, vLLM and LM Studio can use
the OpenAI-compatible adapter without enabling remote provider access.
With remote access enabled, a hosted service such as OpenAI or OpenRouter
can use the same adapter. Keep provider API keys separate from `MESA_API_KEY`,
which authorizes access to MESA.

Legacy chat model values with a provider prefix, such as `ollama:qwen3:8b`,
emit a one-time warning. For a legacy Ollama spec without a chat base URL,
MESA uses the local Ollama endpoint. `MESA_LLM_PROVIDER` and
`LLM_EMBEDDING_MODEL` emit startup warnings and are ignored.
`MESA_ZERO_COST_MODE=true` selects the `qwen3:8b` Ollama preset and uses the
Ollama URL if the chat base URL is unset. Setup disables the preset so the
selected model takes effect.

### Embedding providers

Set `MESA_EMBEDDING_ENABLED=true` for semantic search. The
`MESA_EMBEDDING_MODEL` format is `provider:model`. Supported examples are
`local:BAAI/bge-m3`, `ollama:bge-m3`, `openai:text-embedding-3-small`, and
`openai_compatible:nvidia/nemotron-3-embed-1b`.

Set `MESA_EMBEDDING_BASE_URL` and `MESA_EMBEDDING_API_KEY` for an API
provider. Ollama defaults to `<MESA_OLLAMA_URL>/v1`. The
`openai_compatible` and `openai` backends can use the chat base URL and key
as fallbacks. Use explicit embedding credentials when the services differ.
A remote endpoint requires remote provider access. For local embedding APIs,
use the `ollama` or `openai_compatible` provider name. The core install
includes the OpenAI transport. Use `pip install "mesa-memory[ml]"` for
local sentence-transformers.

When embeddings are enabled and `MESA_EMBEDDING_DIMENSION` is unset,
startup and the rebuild runner probe the model for its dimension. Setup
saves the detected dimension. An explicit dimension skips the startup
probe. With embeddings off in a fresh store, the default is 1024.
Nemotron uses dimension 2048 and
`MESA_EMBEDDING_VERSION=nemotron-qpass-v1`. See the
[rebuild procedure](v4-rebuild-runbook.md) before changing a populated
store's embedding identity.

### Environment file and first startup

The [template](../.env.example) uses a standalone `combined` owner with models
disabled. Copy it only if needed, replace the storage path with an absolute,
writable dedicated directory, and supply issued credentials privately. To load
it explicitly, set `MESA_LOAD_DOTENV=true` and `MESA_DOTENV_PATH` to its absolute
path **in the launching shell**; setting them only inside the file cannot enable
loading. The `test-isolated` profile forbids dotenv. `mesa start` automatically uses the file created by `mesa setup`.

Fresh combined startup creates or migrates SQLite and initializes LanceDB. For
older stores, follow the
[upgrade procedure](RUNBOOK.md#upgrade-v080). Startup failures occur in the server terminal before
readiness; check the named variable, writable path or missing selected extra
there. Do not paste secrets or entire environment files into bug reports.

### Declared MesaConfig fields

See `mesa_memory/config.py` and `MesaConfig` for field declarations.

| Python field | Environment name | Declared default |
|---|---|---|
| `llm_model` | `MESA_LLM_MODEL` | `''` |
| `llm_base_url` | `MESA_LLM_BASE_URL` | `None` |
| `llm_api_key` | `MESA_LLM_API_KEY` | `None` |
| `llm_timeout_seconds` | `MESA_LLM_TIMEOUT_SECONDS` | `60.0` |
| `embedding_base_url` | `MESA_EMBEDDING_BASE_URL` | `None` |
| `embedding_api_key` | `MESA_EMBEDDING_API_KEY` | `None` |
| `extraction_thinking` | `MESA_EXTRACTION_THINKING` | `False` |
| `extraction_max_tokens` | `MESA_EXTRACTION_MAX_TOKENS` | `4096` |
| `external_provider_enabled` | `MESA_EXTERNAL_PROVIDER_ENABLED` | `False` |
| `embedding_version` | `MESA_EMBEDDING_VERSION` | `'v1'` |
| `embedding_model_revision` | `MESA_EMBEDDING_MODEL_REVISION` | `None` |
| `embedding_normalized` | `MESA_EMBEDDING_NORMALIZED` | `True` |
| `context_window_limit` | `CONTEXT_WINDOW_LIMIT` | `8000` |
| `lancedb_memory_limit_bytes` | `LANCEDB_MEMORY_LIMIT_BYTES` | `1073741824` |
| `ram_allocation_fraction` | `RAM_ALLOCATION_FRACTION` | `0.18` |
| `rrf_k` | `MESA_RRF_K` | `60` |
| `rrf_vector_weight` | `MESA_RRF_VECTOR_WEIGHT` | `8.0` |
| `rrf_bm25_weight` | `MESA_RRF_BM25_WEIGHT` | `1.0` |
| `rrf_assertion_weight` | `MESA_RRF_ASSERTION_WEIGHT` | `0.0` |
| `search_scope_cache` | `MESA_SEARCH_SCOPE_CACHE` | `True` |
| `chunking` | `MESA_CHUNKING` | `'auto'` |
| `rerank_model` | `MESA_RERANK_MODEL` | `'BAAI/bge-reranker-v2-m3'` |
| `rerank_depth` | `MESA_RERANK_DEPTH` | `50` |
| `v4_rebuild_enabled` | `MESA_V4_REBUILD_ENABLED` | `False` |
| `crossencoder_pool_multiplier` | `MESA_CROSSENCODER_POOL_MULTIPLIER` | `3` |
| `extraction_lang` | `MESA_EXTRACTION_LANG` | `'tr'` |
| `zero_cost_mode` | `MESA_ZERO_COST_MODE` | `False` |
| `ollama_url` | `MESA_OLLAMA_URL` | `'http://localhost:11434'` |
| `storage_path` | `MESA_STORAGE_PATH` | `derived from cwd; runtime requires explicit storage root` |
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

### MCP and operator variables

`mesa_mcp/configuration.py` owns separate `MCPSettings`; see [MCP](../README_MCP.md).
The direct stdio client uses the MESA API key and configured tenant, workspace and dataset. `mesa mcp config <client>` emits those settings for a local server. The optional gateway has its own binding and policy controls. The Core HTTP context default is 2048 tokens.
`MESA_GATEWAY_ENCRYPTION_KEY` and `MESA_GATEWAY_CONTROL_DB` are operator inputs.

Rebuild uses reviewed trusted/storage/work roots and full-root offline scope;
see [runbook](v4-rebuild-runbook.md). Defaults and example pairings do not certify
provider availability or quality.

Canonical V4 insertion requires the combined processing consumer. A combined
runtime without models stores documents and finds them with BM25; vector search
starts when embedding is enabled (`degraded_sources` shows `vector` until then);
503 `canonical_processing_unavailable` happens only on a non-combined profile.
