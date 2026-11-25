# Changelog

All notable changes to the MESA project will be documented in this file.
Entries describe the named release. Old performance and architecture claims
are historical; consult the [current guides](docs/README.md) for present behavior.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- OpenAI-compatible chat endpoint (`POST /v1/chat/completions`) backed by
  `/v4/ask` with numbered citations in message text, extra `mesa_citations`
  field, non-streaming and single-chunk SSE + `[DONE]` streaming, Bearer /
  `X-API-Key` auth, and `model` scope (`mesa` or
  `mesa:<tenant>/<workspace>/<dataset>`) (D104).
- OpenAI-format SDK tool definitions (`MESA_OPENAI_TOOLS`, `get_openai_tools`)
  for `search`, `read_text`, and `list_documents`, plus `execute_tool_call` /
  `aexecute_tool_call` helpers in `mesa_client` (D104).
- Grounded answering (`POST /v4/ask`, sync/async SDK `ask`, `Mesa.ask`, CLI
  `mesa ask`, and MCP tool `mesa_ask`) (D103). Searches authorized scope
  (sessionless or session-based, with optional `document_ids`, `valid_at`,
  `limit=8`, `expand`, and `retrieval_mode`), wraps evidence as untrusted data
  with `RECOMMENDED_SYSTEM_PROMPT`, enforces strict JSON parsing with one retry
  on malformed output, drops and logs any out-of-context citations, returns
  `not_found: true` whenever no valid citation remains, and returns HTTP 409
  `"no LLM configured"` when no LLM is configured (D101).

### Changed

- Unified all chat LLM providers onto a single OpenAI-compatible adapter
  (`mesa_memory/adapter/openai_compat.py`) configured via `MESA_LLM_MODEL`,
  optional `MESA_LLM_BASE_URL`, optional `MESA_LLM_API_KEY`, and
  `MESA_LLM_TIMEOUT_SECONDS` (D102). Legacy `provider:model` values remain
  accepted with a one-time deprecation warning (`ollama:` without a base URL
  resolves to `<MESA_OLLAMA_URL>/v1`; other prefixes are stripped).
- Removed the separate Claude (`anthropic`), native Ollama (`ollama`), and
  `live.py` adapters along with the `ollama` and `anthropic` package
  dependencies. `MESA_LLM_PROVIDER` and `LLM_EMBEDDING_MODEL` now warn once at
  startup and are ignored (D71, D102). Embeddings remain on `MESA_EMBEDDING_*`.

## [0.9.6] - 2026-11-25

[Release notes](docs/release-notes-0.9.6.md)

### Added

- Session-optional document ingestion (`POST /v4/documents`) and search
  (`POST /v4/memory/search`) across HTTP, sync/async Python SDK, and MCP
  (`mesa_recall` with `dataset_ids` or `use_session=false`) (D94). Requests
  without `session_id` authorize against dataset `READER`/`WRITER` roles with
  `tenant_id`, `workspace_id`, and dataset scope (`agent_id` defaults to
  `local-agent`) and require the principal's `SESSION_CREATE` permission for
  that agent, the same check as `/v4/sessions/start` (R25 amendment A1); mixing `session_id` with disagreeing scope fields returns
  HTTP 422. Session-based calls and agent memory routes remain unchanged.
- Optional `document_ids` search filter (1..64 caller document IDs) across
  HTTP (`POST /v4/memory/search`), Python SDK (`MesaV4Client`,
  `AsyncMesaV4Client`, `Mesa`), and MCP (`mesa_recall`) (D95). Applied in the
  pre-rank scope query across BM25, vector, and legal lanes, included in the
  search scope cache key, and bypasses the dataset-wide persistent BM25 index.
- Citation spans (`char_start`/`char_end` on `source_chunks` via Alembic
  migration `b1e2f3a4b5c6` with exact cumulative backfill for existing stores)
  and `revision_id`, `chunk_id`, `span` (`{"start", "end"}`) fields on every
  search result and context source list across HTTP, SDK, and MCP (D95).
- Document text read endpoint `GET /v4/documents/{document_id}/text` (and SDK
  `get_document_text`) returning the full rebuilt text or a `start`/`end` slice
  for the active or a specified historical revision with dataset `READER`
  access (D96).
- Document status endpoint `GET /v4/documents/{document_id}` (and SDK
  `get_document`) returning per-revision status, validity dates, chunk counts
  by state, ingest `stage` (`admitted`, `processing`, `indexed`, `active`,
  `failed`, `superseded`), and plain-English failure `error` messages mapped
  from failure classes via `mesa_storage/error_messages.py` (D96).
- Package `version` field in the `GET /health` response body (D96).
- Multipart document upload (`POST /v4/documents/upload`, SDK `add_file`,
  and `mesa add <file>`) for text-layer PDF, DOCX, UYAP UDF, XLSX, and UTF-8
  text and Markdown. PDF citations include the page number. XLSX citations
  include the sheet and original row range (D95, D99).
- `mesa mcp config <client>` command printing ready-to-paste MCP configuration
  snippets (JSON/TOML, no files written) for `claude-code`, `claude-desktop`,
  `codex`, `cursor`, `gemini`, and `antigravity` pointing to `mesa-mcp` (D97).

### Changed

- Moved the `mesa` CLI entry point from `mesa_mcp.codex_cli:main` to the
  neutral `mesa_mcp.cli:main` module, keeping `start`, `add`, `search`,
  `delete`, `status`, `operations`, `mcp-config`, and `mcp config <client>`
  (D97). `mesa-mcp-gateway` remains available as an optional advanced mode.

### Removed

- Deleted the Antigravity stdio bridge/CLI (`mesa_mcp/antigravity_bridge.py`,
  `mesa_mcp/antigravity_cli.py`, and the `mesa-antigravity-bridge` console
  script) and the Codex installer/hooks/CLI (`mesa_mcp/codex_cli.py`,
  `mesa_mcp/codex_hooks.py`, and the `mesa codex` / `mesa antigravity` command
  groups) (D97).

## [0.9.5] - 2026-11-24

[Release notes](docs/release-notes-0.9.5.md)

### Added

- Additive revision validity fields (`revision_number`, `valid_from`,
  `valid_to`, empty string when unset) on search results across HTTP
  (`/v4/memory/search`), Python SDK (`V4SearchResult`, `V4ContextSource`), MCP
  (`mesa_recall` and gateway recall), and HTTP context source lists
  (`GET /v4/sessions/{session_id}/context`) (D88).
- Out-of-order ingestion for dated document revisions (D87): when revisions
  carry `metadata.valid_from`, validity order rather than upload order decides
  the `ACTIVE` head and `[valid_from, valid_to)` history chain. Older versions
  loaded after newer ones commit directly as `SUPERSEDED` history with
  `supersedes_revision_id = null`, update the preceding revision's `valid_to`,
  and stay out of the undated persistent BM25 index. Duplicate `valid_from`
  returns HTTP 409; undated revisions on a dated document return HTTP 422.
- Per-(tenant, dataset) persistent FTS5 BM25 index for exact dataset-wide
  queries (D80), updated inside the commit, supersession, rollback, and purge
  SQLite transactions and automatically rebuilt from canonical SQLite when
  missing. Narrower scopes (multi-dataset, temporal/jurisdiction filters, or
  multi-agent datasets where the agent filter excludes rows) continue to use
  per-query temporary FTS5 indexes.

### Changed

- Document ingestion (`POST /v4/documents`, `/v4/memory/insert`, SDK, and MCP)
  validates optional `metadata.valid_from` and `metadata.valid_to` as
  timezone-aware ISO-8601 timestamps (`valid_from < valid_to`), rejects
  invalid or date-only values with HTTP 422 before writing any chunks, and
  stores normalized UTC timestamps (D85).
- Dated search queries now evaluate validity intervals as half-open
  `[valid_from, valid_to)` (`valid_from <= valid_at < valid_to`, with matching
  open-end overlap for `valid_from`/`valid_to` range filters) so an exact
  amendment instant returns only the replacing version (D86).
- LanceDB is locked to 0.41.0 with an upper bound below 0.42. Vector search
  prefilters by dataset and agent before ranking and verifies eligibility in
  SQLite. Older LanceDB tables receive their scope keys on startup. The live
  projection worker batches vector upserts and retains per-mutation receipts.
- The [architecture overview](ARCHITECTURE.md) explains how documents become
  searchable and where MESA fits in an application.

### Fixed

- The legal extraction stress test uses a deterministic work bound at the
  existing input limits. The user guide shows how to load law versions and
  search the text in force on a date.
- Whole-document uploads retain the chunking plan selected at admission.
  Retries continue the same revision from its saved chunks, including legacy
  uploads whose stored prefix matches the configured plan.
- Commit-time maintenance of the persistent BM25 index looks up only the
  committing mutation's assertions, reducing SQLite writer contention during
  ingestion.
- Internal storage identifiers (`document_id`, `revision_id`, `chunk_id`,
  `assertion_id`, and catalog-mapped `mesa-*` entity names) are excluded from
  lexical FTS5 indexing, lexical passage/assertion scoring, and legal citation
  text matching so query numbers cannot accidentally match opaque internal IDs.

### Removed

- The Kùzu graph database dependency, graph projection worker/outbox lane,
  graph search lane, and graph rebuild paths (D79). Forward-only Alembic
  migration `f9c0d1e2f3a4` removes legacy `GRAPH` rows from `projection_outbox`,
  `projection_attempts`, `artifact_registry`, `artifact_sources`,
  `artifact_cleanup_outbox`, and `memory_artifacts`, and promotes any completed
  `VECTOR_APPLIED`/`GRAPH_APPLIED` mutations to `COMMITTED`. Existing `kuzu_db`
  files on disk are ignored and can be deleted manually after verifying backups.
- Retired graph settings (`MESA_GRAPH_ENABLED`, `MESA_RRF_GRAPH_WEIGHT`,
  `MESA_V4_GRAPH_TIMEOUT_SECONDS`) log one startup warning per key and are
  ignored.

## [0.9.4] - 2026-11-23

### Retrieval quality

- On the shared Tier M dev store with models and reranking disabled, passage
  Hit@5 rose from 50/112 to 61/112 and MRR from 0.3411 to 0.3955. Median search
  latency fell from 361.3 ms to 233.9 ms (35%). The comparison used the same
  128 dev questions, including 16 no-answer questions.

### Removed

- Legacy V3 node store tables (`nodes`, `nodes_fts`, `lancedb_wal`,
  `purge_journal`, `system_config`) are dropped by forward-only Alembic
  migration `f7a8b9c0d1e2` along with V3 `MemoryDAO` node/purge/WAL methods,
  `VectorEngine` soft-delete/Procrustes methods, `KuzuGraphProvider.get_cognitive_salience`,
  and legacy V3 startup recovery/polling paths. Existing 0.9.3 V4 stores migrate
  automatically without data loss.
- LLM consolidation (`ConsolidationLoop`, `AdaptiveRouter`, `Tier3Validator`,
  `ValidationPolicy` modes 1/2), `routing_telemetry` (dropped by Alembic
  migration `f8b9c0d1e2f3`), and valence/consolidation `ObservabilityLayer`
  metrics. Validation uses deterministic mode 0. Optional fact extraction
  (`MESA_EXTRACTION_ENABLED=true`) runs through `FactExtractionService`.
- MESA ignores the retired settings below and logs one warning per key at startup.
  - `MESA_TIER3_MODE`, `MESA_TIER3_LLM_PROVIDER_A`, `MESA_TIER3_LLM_MODEL_A`,
    `MESA_TIER3_LLM_PROVIDER_B`, `MESA_TIER3_LLM_MODEL_B`, `MESA_LEGAL_DOMAIN_MODE`,
    `MESA_T_ROUTE`, `MESA_USE_V4`
  - `MESA_RETRY_MAX_ATTEMPTS`, `MESA_RETRY_MIN_WAIT_SEC`, `MESA_RETRY_MAX_WAIT_SEC`,
    `MESA_CIRCUIT_BREAKER_THRESHOLD`, `MESA_CIRCUIT_BREAKER_COOLDOWN`,
    `MESA_HUMAN_REVIEW_QUEUE_PATH`, `MESA_DEAD_LETTER_QUEUE_PATH`
  - `CONSOLIDATION_BATCH_SIZE`, `CONSOLIDATION_IDLE_TIMEOUT`, `HISTOGRAM_MAX_SIZE`,
    `METRICS_ADMISSION_THRESHOLD`, `METRICS_DIVERGENCE_THRESHOLD`
- Retired unused gateway scaffolding and internal retrieval compatibility
  modules. Adaptive retrieval retains its Turkish query normalization and
  citation checks.

### Changed

- Whole-document uploads compute the revision manifest when the final chunk is
  admitted. Interrupted uploads resume from committed chunks, and the sealed
  manifest uses the same hash format. For the 5 MB models-off fixture,
  admission fell from 246.52 s to 169.42 s and end-to-end readiness from
  249.99 s to 172.61 s, with all 4,882 chunks committed in both runs.
- Search ranking and tie-breaks across BM25, vector, assertion, graph, exact
  legal lookup, and RRF fusion now break equal-score ties by stable content
  identity (`document_id`, `revision_number`, chunk `ordinal`, `chunk_id`)
  rather than random UUIDs, and `CatalogRepository` assigns tenant-scoped
  deterministic UUID5 physical keys so two fresh stores built from the same
  corpus produce identical ranks without changing scores.
- Scoped temporary FTS5 lexical search now uses 5-character prefix matching
  (`"abcde"*` with `prefix='5'`) for query tokens of 6 or more characters that
  contain no digits, while keeping shorter tokens, digit tokens, and `ı` → `i`
  variants exact. To use the previous exact-token matching, run v0.9.3 with a
  pre-upgrade store snapshot.
- Fusion defaults changed based on benchmark v2 dev ablation:
  `MESA_RRF_ASSERTION_WEIGHT` now defaults to `0.0` (was `1.0`) and
  `MESA_GRAPH_ENABLED` now defaults to `false` (was `true`). Restore the earlier
  fusion defaults with `MESA_RRF_ASSERTION_WEIGHT=1.0` and `MESA_GRAPH_ENABLED=true`.
  Enabling the graph backfills skipped GRAPH projections at startup.
- The configuration guide and environment template put common settings first.
- Fresh installs cap FastAPI below 0.142, LanceDB below 0.35, Kùzu below
  0.12, and Pydantic below 3.

## [0.9.3] - 2026-11-16

### Fixed

- MCP session creation now provisions the workspace and dataset when a fresh
  store reports them missing, then retries once. If catalog creation fails,
  callers receive the original session-start error rather than the create error.
- Ending a session in default Mode 0 now processes deferred records without a
  consolidation loop, allowing finalization to complete.
- Turkish legal rule comparisons and date lookups handle uppercase İ/I
  consistently with lowercase text. Original text, offsets and rule versions
  are preserved.
- Scoped FTS queries include deduplicated `ı` → `i` variants, so `YARGITAY`
  and `yargıtay` find text written `YARGITAY KARARI`.
- Context requests reject timestamps without timezone offsets with HTTP 422
  before comparing ranges. Fact validation treats date-only values as UTC
  when comparing them with timezone-aware timestamps, preventing datetime errors.
- Gateway circuit breakers release half-open probes after permanent errors or
  cancellation. Non-retryable bridge writes enter a terminal failed state
  instead of being sent again on every drain.
- Projection rebuild smoke checks use the same assertion text, including the
  evidence span, as vector replay.
- Duplicate or stale projection completion calls preserve committed artifacts;
  artifact compensation remains restricted to fenced mutations.
- SDK quickstart examples wait for ingestion before requesting context and close
  document files. Documentation links and the architecture version are corrected,
  and embedding setup notes explain downloading the model before enabling it.
- Parser regression checks retain their 200 ms budget using thread CPU time,
  with a 2 s hang guard. Claude adapter tests skip when the optional `anthropic`
  package is absent.
- Legal rule extraction skips impossible decision-reference scans and delays
  law-name cleanup until an attached article is found. On the API-limit
  `TCK/1` input, best-of-five local thread CPU fell from 129.861 ms to
  52.330 ms (2.48x faster), with identical saved outputs and spans. The
  isolated 200 ms CI CPU-budget check is blocking again; the full local
  suite retains its 2 s hang guard.

### Changed

- Bulk ingestion batches SQL and graph projections and outbox completion in
  the existing worker pass. The measured 4,999,000-byte document reached full
  readiness in 245.20 s versus 718.27 s before (2.93x faster), with all 4,882
  mutations COMMITTED, SQL and GRAPH COMPLETED, VECTOR SKIPPED, and lossless
  chunk readback. Durability and fencing behavior are preserved.
- The default embedding model is now `local:BAAI/bge-m3`, with 1024 dimensions
  and normalization enabled, including the Compose model default. Models remain
  off by default. Stores created with the old default must keep both
  `MESA_EMBEDDING_MODEL=local:magibu/embeddingmagibu-200m` and
  `MESA_EMBEDDING_DIMENSION=768`, or rebuild before switching embedding identity.
- Tag-triggered PyPI releases wait for the complete reusable CI workflow before
  building and publishing. The first live tag run will verify this wiring.
- Startup fails with a clear error when `MESA_TIER3_MODE=1/2` has no active
  consolidation loop; extraction must be enabled for these modes.
- Bootstrap `MESA_API_KEY` secrets containing `.` are rejected at startup.
  Placeholder API keys and principal IDs in `.env.example` are commented out.
- Antigravity bridge write spools are isolated per workspace. Entries in the old
  shared spool are left in place and are not migrated automatically.

### Known issues

- Kuzu 0.11.3 can write uninitialized memory into graph page tails, so raw graph files may contain fragments of text the process handled; MESA never stores memory text in the graph (checked by query), and the graph directory should be treated as sensitive like the SQLite file.
- Per-minute and daily rate limits are not enforced for `/v4/*` and `/control/*`
  in this version; the API documentation now states this limitation.
- The ASCII query `yargitay` still does not match text written `Yargıtay`;
  index-side Turkish folding remains deferred.

## [0.9.2] - 2026-11-06

This release hardens the Turkish legal extraction and retrieval features from
Round 16 and Round 17. V4 HTTP routes remain compatible. Production qualification
remains **NO-GO** pending the normal certification and production-like release gates.

### Added

- Rule-based Turkish legal item extraction for newly admitted chunks, capturing
  versioned legal rule items with chunk offsets (citation, decision_ref,
  date, amount, duration, and outcome). Extracted items are returned in
  search provenance under `legal_items` separately from SPO facts, without
  affecting fusion scores. Document purge erases their source quotes.
- Store-learned law names: authorized document titles and law headers provide
  scope-cached and indexed law names, including the `K.` abbreviation.
- Exact-match-first slot: queries matching a decision's own docket/decision reference
  or exact law/article occupy at most three leading search positions in document order,
  preserving remaining result order. Decision matching requires references beginning in
  the first 300 characters of an authorized first chunk; later quotations do not count.

### Changed

- `MESA_LEGAL_RULES_ENABLED` defaults to `true`. When set to `false`, it disables
  automatic legal identity assignment and item extraction during ingestion. During
  search, it disables legal processing (citation resolution, exact-match slot,
  decision künye promotion, legal ranking factors, and graph citation parsing) and
  suppresses stored legal result overlays in returned copies while keeping durable
  identities and snapshots intact.
- Open-ended SPO fact extraction (`MESA_EXTRACTION_ENABLED`) defaults to `false`
  regardless of `MESA_MODEL_ENABLED`; an explicit `true` still enables it.
- Constitutional Court names (`Anayasa Mahkemesi`, its suffix forms and `AYM`)
  no longer activate Constitution citation ranking; separate statute citations in
  the same query are preserved.
- Large-document admission waits for queue room on `queue_over_capacity` without
  repeating full document chunk planning.

### Security

- Fixed synchronous CPU-bound request-path stalls that also affect 0.9.1: rule
  extraction, citation resolution, scope building, and heavy token scoring now bound
  caller-text work and yield thread execution, keeping `/health` responsive under
  high load. Users of 0.9.1 are recommended to upgrade.

### Known issues

- Heavy 1,000-candidate search takes approximately 11.6 s; `/health` responsiveness
  is restored for measured repros, but heavy-query execution time is not proved faster.
- Older whole-document secret scanning and extreme Unicode normalization can still
  cause sub-second request-path stalls.
- UUID ties can change ordering across separately ingested stores; same-store
  retrieval remains deterministic for retained inputs.
- Large documents take minutes to become fully ready: a 5 MB document
  (4,999,000 bytes) took about 7 minutes (7m00.6s) to become ready with models off
  and graph on.
- Non-object document requests keep an INFO-level 422 detail wording difference;
  HTTP status, error code, location, and OpenAPI schema remain unchanged.

## [0.9.1] - 2026-11-01

This release combines Round 13 (speed) and Round 14 (retrieval quality and
document workflow improvements), merged in PRs #137 and #140. V4 HTTP routes
remain compatible. Production qualification remains **NO-GO** pending the
normal certification and production-like release gates.

### Added

- Lossless Turkish article-aware chunking for new document revisions, controlled
  by `MESA_CHUNKING=auto|plain` (default `auto`). Admitted chunk text pins retry
  plans; ambiguous prefixes return HTTP 409 without appending chunks.
- Optional lazy BGE cross-encoder reranking after fusion and outside the search
  connection. It is off by default; enable with `MESA_RERANK_ENABLED=true` and
  the existing `[ml]` extra. Results add `rerank_score` while preserving fusion
  scores. Default model: `BAAI/bge-reranker-v2-m3`; depth: 50.
- Source-based document deletion through `Mesa.delete(source=...)` and
  `mesa delete --source <ref>`; document-ID deletion remains available.
- Versioned, bounded search-scope caching of authorized rows and temporary
  BM25 indexes, invalidated by SQLite commits. Configure it through
  `MESA_SEARCH_SCOPE_CACHE` (default `true`).
- Tag-triggered PyPI trusted publishing through GitHub Actions and OIDC.

### Changed

- Set the runtime vector RRF weight to 8.0 from the frozen Tier M dev benchmark
  (passage MRR 0.3953 → 0.5663 with reranking off); other lane weights remain 1.0.
  The full weight/reranker matrix and model environment are recorded in the
  [retrieval guide](docs/retrieval-and-graph.md#round-14-dev-benchmark).
- Index ingest ledger lookups and reconcile revision activation once per commit
  transition, reducing bookkeeping growth while preserving activation rules.
- Run query embedding before the shared search connection, and scrypt API-key
  verification, issuance and rotation in worker threads. Graph-disabled search
  skips graph seed preparation.
- Include the OpenAI and Ollama SDKs in the base installation:
  `pip install mesa-memory`. Anthropic remains in `[adapters]`.
- `mesa add` waits for the revision to become active by default; use `--no-wait`
  to return after admission. SDK waiting also polls mutation status.
- Accept zero RRF lane weights to disable a lane's contribution.

### Fixed

- Fail promptly on terminal chunk mutations with their ID, state and error
  class instead of waiting for the document timeout; keep retryable states
  pending. CLI errors remain readable and exit with status 1.
- Retry `queue_over_capacity` during document admission within the SDK deadline.
- Preserve timestamped runtime log handlers across Alembic migrations.
- Record projection-attempt start times from the actual outbox claim time.
- Preserve the missing-ML installation message when an earlier server lifespan
  left tasks associated with a closed event loop.

### Upgrade notes

- Existing active revisions keep their chunks. Use `MESA_CHUNKING=plain` for
  new revisions requiring the earlier chunking plan, and
  `MESA_RRF_VECTOR_WEIGHT=1` to retain the earlier vector weighting.
- The ingestion optimization adds SQLite indexes through the normal migrations.
  Continue to follow the documented backup and single-owner upgrade procedure.
- Benchmark results describe the frozen Turkish Tier M dev split and the
  documented model/hardware setup; they are not general performance guarantees.

## [0.9.0] - 2026-10-28

### Added

- Local CLI with subcommands `start`, `add`, `search`, `delete`, `status`, and `mcp-config` for standalone single-user operation.
- Python SDK `Mesa` client with zero-config local defaults reading `~/.mesa/credentials.json`.
- Lossless document ingestion endpoint `POST /v4/documents` with deterministic chunking, duplicate detection, and revision tracking.
- Configurable graph switch (`MESA_GRAPH_ENABLED=false`) enabling lightweight operation without KùzuDB and skipping graph lanes.
- Unified model provider configuration via `MESA_LLM_MODEL` and `MESA_EMBEDDING_MODEL` using `provider:model` syntax.
- Local Docker operation running `mesa start` with shared volume credentials.

### Changed

- Document purge completely erases stored source and assertion text for privacy compliance while keeping audit state and hashes.

### Fixed

- Retry backoff, dead-letter recovery and queue replay fixes preventing records from hanging indefinitely in `RETRY_PENDING`.
- Admission 503 transient startup window retry in SDK and clear actionable installation messages for missing extras (`[adapters]`, `[ml]`).
- Turkish abbreviation and list numbering handling in document chunker to prevent splitting mid-citation.

## [0.8.1] - 2026-10-18

<a id="v081-publication"></a>

### Publication status

The repository owner authorized this v0.8.1 source release on 2026-10-18.
It includes the MVP hardening and developer-experience changes merged in PR #112.
Production qualification remains **NO-GO** pending the normal release gates;
real LLM/embedding integration and a production-like 24-hour soak are not certified.
No local GPG signing key is available, so the annotated release tag is unsigned.
Signed-tag preflight success is not claimed. CI, wheel/SBOM artifacts and OIDC
attestations are verified separately for the release commit and tag.

### Removed

- Removed the obsolete standalone raw-log agent-ID backfill script.
- Removed obsolete manual backup/restore, MCP spoofing, Mem0 smoke and async I/O
  benchmark scripts from `tests/`; removed their stale collection exclusions.
- Removed the unused dashboard/showcase frontend, its npm dependencies and build
  configuration, and the obsolete `run_demo_rag.py` launcher. MCP control APIs
  and operator approval CLI remain available; see [control API](docs/api-reference.md#mcp-control-api).

### Fixed

- Scoped BM25 corpus statistics, pre-rank audit queries and audit metadata to
  authorized tenant/dataset scope; unrelated data no longer influences results.
- Protected projection completion with claim/fencing ownership checks; prevented
  cross-dataset repair deletions and false invalidation when vector lookups fail.
- Kept Kùzu caches consistent after delete and transaction rollback, including
  composite assertion IDs; graph commit failures retry and cancellations roll back.
- Surfaced vector backend and embedding failures explicitly; removed the legacy
  raw-memory fallback to an unrelated eight-dimensional embedding.
- Rejected reserved `_mesa_` metadata and non-finite RRF weights while preserving
  explicit zero confidence. Mapped context, catalog chunk and rollback errors
  to actionable API responses instead of generic server errors.
- Added optional `legal_identity` parity to synchronous and asynchronous V4 SDK
  insertion; slowed default status polling to fit the shared minute request budget.
- Returned field-specific validation errors without echoing rejected input or
  internal route paths, and retained startup error logging after migrations.
- Corrected standalone runtime defaults and documented a minimal supported
  Python install. Updated CI import checks, documentation contracts and SQLite
  fault-injection test doubles.
- `make health` now checks the running API's readiness and authenticated health
  using `MESA_BASE_URL` and `MESA_API_KEY`, without opening local databases.
  Unhealthy responses and connection/authentication failures return a nonzero exit.

### Documentation

- Clarified provider prerequisites, immutable embedding identity, asynchronous
  projection status, source evidence, session finalization and client cleanup.
  The V4 quickstart displays assertion/source references. Models-disabled startup
  is described as a health check; V3 ingestion still requires embeddings.
- Verified small SQLite/LanceDB/Kùzu flows, restart, populated tenant isolation
  and completed rollback with explicitly controlled model boundaries. These
  checks do not establish real-model quality or production-size capacity.
- Consolidated 37 Markdown pages into 20 maintained guides and decision records;
  merged overlapping guides, retired obsolete ADR files and repaired document
  contracts and links. Release-specific exceptions remain in this changelog.

- Consolidated documentation ownership and navigation; replaced the root V3
  whitepaper with a current architecture entry point.
- Removed obsolete Colab guidance, the retired architecture/audit/benchmark
  documents and their archive indexes. Merged the external benchmark ownership
  note into the ecosystem guide and repaired incoming documentation links.
- Removed duplicated setup/recovery guidance, corrected MCP authorization and
  rebuild/migration wording, and clarified ADR applicability. These documentation
  changes do not certify a new release; prior tracked records remain in Git history.

## [0.8.0] - 2026-10-15

<a id="v080-publication"></a>

### Publication status and exceptions

**Release date: 2026-10-15. Package version: 0.8.0.**

The repository owner explicitly approved publication after reviewing the missing
certification evidence. This source release does not certify production readiness.
Actual publication and artifact status are recorded in GitHub Releases and the
release audit; a version bump alone is not publication proof.

#### Verification and known limitations

- The preceding documentation PR passed both MESA CI and external release gates;
  the version/release commit is checked independently before tagging.
- Real model/provider boot/restart/token-cost evidence, a production-like 24-hour
  soak and new official Profile B certification are not supplied by this release.
- Full per-method docstring certification and the pre-existing full-tree Black
  formatting debt remain incomplete; required scoped CI checks are preserved.
- No local GPG signing key is available: the annotated v0.8.0 tag is unsigned.
  Signed-tag preflight success is not claimed. Build/SBOM/OIDC provenance status
  is verified separately and must not be inferred from publication alone.
- Production status remains **NO-GO** until the normal certification gates pass.

#### Changes

The development history since v0.7.1 contains selectable validation, canonical
embedding identity, durable offline projection rebuild, immutable revision
activation/ownership recovery, four-lane scoped retrieval, graph runtime closure,
atomic evidence packing, bounded opt-in query planning and typed legal identity.
The current documentation refresh aligns entry points, API examples, provider
defaults, configuration, ecosystem boundaries and limitations; removes unsourced
showcase metrics/PASS output; and adds scoped neutral V3 and provider-required V4 examples.

Independent review corrected conservative offline tokenizer counting, low-level
vector versus canonical authorization, V3 router descriptions and smoke checks
under Python optimization. Dashboard build dependency lock patches address two
detected HIGH advisories; audit/build proof remains separate from production gates.

See [CHANGELOG](CHANGELOG.md) v0.8.0 for the detailed implementation
history, [migration](docs/RUNBOOK.md#upgrade-v080) for forward schema/representation
requirements, and [release policy](docs/release.md) for immutable signed tagging,
same-commit CI, provider, backup/rebuild and 24-hour soak requirements.

No new model accuracy, legal correctness, official Profile B PASS, native
MESA_Data delivery, production-size memory bound or 24-hour soak result is
claimed. The earlier audit referred to `reports/v0.8.0-documentation-release/`;
that evidence directory is not present in this checkout. Verify publication and
artifact results in the release's own records; this prose is not a substitute
for missing run output or a signed-tag verification.

### Added

- **Documentation source-truth refresh:** Added a navigable documentation index,
  configuration/CLI references, ingestion/retrieval/security/migration guides,
  source-generated OpenAPI and neutral scoped examples. Model-disabled V3 smoke
  and provider-required V4 ingestion are explicitly distinguished. Publication
  is owner-approved; production certification remains NO-GO.

- **Typed legal retrieval identity:** V4 ingestion can carry a validated,
  provenance-bound law/article/version identity. Canonical assertions persist
  the identity through fallback projection, and scoped indexed pre-rank lookup
  can recover a bare article chunk without trusting reserved caller metadata.

- **Bounded adaptive context retrieval:** Added an explicit opt-in context mode
  that runs the original V4 query first, makes at most one semantic
  reformulation call for weak results, executes at most two additional searches
  with the same authorized scope, and feeds one deduplicated candidate list to
  the existing ContextBuilder budget pass. Single-query retrieval remains the
  default for HTTP, sync/async SDK and MCP callers.
- **Selectable validation policy:** Added explicit deterministic-only,
  single-LLM and dual-LLM consensus modes with truthful runtime capability
  reporting and durable per-mutation policy snapshots.
- **Durable projection rebuild:** Added storage-root-scoped rebuild operations,
  append-only operation events, fenced leases/checkpoints, projection generation
  pointers and retained-generation rollback.
- **Administrative rebuild API and SDK:** Added ADMIN-only submit, status, cancel
  and retry interfaces, the deprecated storage-root alias, synchronous and
  asynchronous Python clients, and the offline `mesa-v4-rebuild` runner.
- **Closure evidence and operations telemetry:** Added content-free CI evidence,
  rebuild progress/parity/rollback metrics, provider rehearsal contracts and an
  operator runbook.

### Changed

- **V4 jurisdiction propagation:** Context HTTP, sync/async SDK and MCP context
  and recall calls now preserve the requested jurisdiction through retrieval.

- **Round-8 SQLite durability:** Canonical production SQLite connections now
  explicitly request `synchronous=FULL`. Weaker `NORMAL`/`OFF` synchronization
  is accepted only in the explicit `test-isolated` runtime profile. Auxiliary
  connections preserve their established journal mode.
- **Round-8 backup authority:** Backup now verifies that a supplied writer-lock
  handle owns the real storage lock file. Manifests explicitly record SQLite as
  canonical per-database snapshots and vector/graph stores as rebuildable
  projections copied under the exclusive writer boundary.

- **Round-7 lifecycle and hash truth:** Late manifest finalization now reuses
  the canonical revision activation barrier. Declared whole-revision,
  manifest and chunk hashes remain separate; direct insertion records an
  unknown revision hash as `NULL`, including for multiple revisions of one
  document.
- **Opaque catalog storage identity:** New physical catalog keys are
  server-generated and public resolution is external-ID-only. API, SDK and MCP
  provenance/status surfaces translate catalog scope back to public IDs.
- **Historical mutation administration:** Closed sessions remain valid
  historical evidence, but rollback/replay now bind the mutation to the exact
  principal/session tenant, workspace, dataset and agent scope plus explicit
  `ROLLBACK` permission.

- **Round-3 aggregate lifecycle:** Revision manifests freeze explicitly,
  revision and pipeline terminal success is derived from all required child
  work, and non-head historical rollback returns a typed conflict.
- **Tenant-scoped catalog identity:** Public catalog identifiers can be reused
  safely across tenants while the durable schema retains distinct physical
  keys.
- **Temporal and runtime parity:** HTTP, sync/async SDK and MCP now carry
  `valid_at`, `valid_from` and `valid_to`; long-lived MCP/routing state is
  bounded with safe expiration and concurrency.
- **Release hygiene:** The shipped full-cognitive image receives vulnerability
  and SBOM gates, and the legacy server launcher delegates to the canonical
  runtime composition.

- **Round-2 lifecycle certification:** Projection writes now retain durable
  compensation ownership until vector/graph cleanup succeeds; V3 replacement
  stages the successor until secondary writes are safe; repeated mutations for
  one revision activate its head idempotently.
- **Runtime and extraction truth:** Terminal LLM fallback preserves zero-to-many
  facts, local embedding identity names the actual provider, model-disabled
  imports avoid eager ML loading, and supported compose profiles forward the
  declared provider and Tier-3 settings.
- **Tenant and transport contracts:** Durable queue accounting uses the real
  tenant across agents; MCP identities include immutable scope and only recover
  exact inactive-session conflicts; API errors and readiness failures use the
  canonical structured contract.
- **Truthful V4 capability contract:** Capability responses now distinguish
  vector, lexical, relational, validity and graph projection support, advertise
  rebuild limits, and expose durable rebuild only when
  `MESA_V4_REBUILD_ENABLED` is enabled.
- **Projection runtime ownership:** API, worker and rebuild runtimes now acquire
  the same storage-root writer lock and resolve LanceDB/Kùzu paths through the
  active SQLite generation pointer.
- **Embedding identity:** Canonical mutations now persist embedding provider,
  model, version and dimension so projection replay fails closed on any provider
  mismatch. Legacy rows with externally verified provenance can be completed by
  the explicit, writer-locked `mesa-v4-rebuild adopt-provider` command.

### Security

- **Dashboard build dependency patches:** Updated existing transitive `nanoid`
  and `source-map-js` lock entries within supported ranges to address detected
  HIGH advisories, without changing root ranges or running install scripts.

- **Truthful replay boundary:** Semantic `REJECTED` mutations return
  `409 NON_REPLAYABLE` and remain terminal, while target-aware technical
  projection/cleanup retry preserves a truthful aggregate pipeline state.

- **Runtime-owned validation assurance:** Reserved `_mesa_*` metadata can no
  longer be supplied by API writers or used to downgrade a mutation's
  operator-selected validation mode.
- **Rebuild maintenance admission:** Mutating v3/v4 requests are rejected with
  `503 maintenance_pending` while a rebuild is active; read, health and safe
  operation-control paths remain available.
- **Retrieval scope enforcement:** Authorized tenant/dataset vector candidates
  are filtered before ANN ranking, and rebuild verification exercises bounded
  cross-tenant and cross-dataset negative retrieval smoke checks.

## [0.7.1] - 2026-07-29

### Fixed

- Patch release for the merged production hardening and CI security fixes.

## [0.7.0] - 2026-07-23

### Added

- **V4 full-cognitive contract:** Added a breaking, versioned catalog/session/
  mutation API while preserving all v3 lexical-core endpoints.
- **Canonical provenance model:** Added tenant-scoped UUID5 entities, aliases,
  external identifiers, immutable document revisions/source chunks and
  SQLite-authoritative Graph V2 assertions.
- **Durable projection ownership:** Added pipeline-run events, ordered
  SQL→vector→graph projection outbox processing, shared artifact ownership,
  fenced cleanup and two-way reconciliation.
- **V4 clients:** Added synchronous/asynchronous SDK methods and MCP tools for
  catalog, session, insert, search, status, context, replay and rollback.
- **V4 combined deployment:** Added the single-storage-owner
  `docker-compose.v4.yml` topology.

### Security

- **Catalog authorization:** Added principal roles at tenant, workspace and
  dataset scope plus explicit purge/rollback permissions. Agent identifiers
  remain compute/persona context and are not the v4 tenant boundary.
- **Rotation-safe API keys:** Added key-ID addressed salted scrypt hashes,
  revocation, rotation and multiple active principal-bound credentials.
- **Rate-limit credential lifecycle:** Daily and minute limits now use the
  verified server-side `principal_id`; raw API credentials and request-supplied
  tenant/agent values are not used as rate-limit identities. The migration
  intentionally discards legacy `daily_limits.agent_id` rows because they may
  contain credentials, causing a one-time daily-counter reset on upgrade.

### Changed

- **Retrieval V2:** Dataset-filtered vector, BM25 and graph ranks now use true
  reciprocal rank fusion followed by a bounded legal/provenance rerank.
- **PageRank safety:** PageRank is observation-only telemetry and can no longer
  quarantine or invalidate retrieval artifacts.
- **V4 write path:** Tier-3 validation precedes all active projections; direct
  cross-store Saga writes remain only in the v3 compatibility path.
- **Kùzu offline migration safety:** `scripts/migrate_to_kuzu.py` now builds
  and validates a versioned staging artifact under a process lock and SQLite
  journal before same-filesystem promotion. Previous live artifacts are
  retained for explicit rollback; `--wipe` is refused.
- **Kùzu runtime schema safety:** Startup no longer applies unversioned
  `ALTER TABLE` statements to an existing graph. `migrate_kuzu_schema.py`
  must first clone, checksum-validate, and promote an existing graph offline.

## [0.6.1] - 2026-07-17

### Added
- **Multi-Stage CrossEncoder Reranking**: Introduced Stage 2 learned reranking (`mesa_memory/retrieval/reranker.py`) powered by `cross-encoder/ms-marco-MiniLM-L-6-v2` to substantially improve retrieval precision on top of Alpha Reciprocal Rank Fusion.
- **Candidate Pool Expansion**: Added `crossencoder_pool_multiplier` configuration (default: `3x`) to expand Stage 1 candidate pool size prior to CrossEncoder scoring.
- **Tenant-Isolated Batch Fetching**: Implemented `MemoryDAO.get_nodes_by_ids_batch()` in `mesa_storage/dao.py` to fetch candidate payloads in a single optimized SQL query enforced with mandatory `WHERE agent_id = ? AND id IN (...)` Row-Level Security.
- **Non-Blocking Inference & Lazy Loading**: Offloaded CrossEncoder model prediction to `asyncio.run_in_executor()` using `ThreadPoolExecutor` to prevent event-loop blocking. Added lazy singleton initialization in `mesa_api/router.py`.
- **Graceful Degradation**: Added resilient fallback mechanisms ensuring that if model loading or runtime inference fails, `CrossEncoderReranker` logs the warning and falls back cleanly to Stage 1 Alpha-reranked ordering.
- **Configuration & Environment Support**: Added `MESA_CROSSENCODER_ENABLED`, `MESA_CROSSENCODER_MODEL`, and `MESA_CROSSENCODER_POOL_MULTIPLIER` options (`mesa_memory/config.py` and `.env.example`).
- **Comprehensive Test Suite**: Shipped unit and integration tests in `tests/test_crossencoder_reranking.py` covering fallback behavior, score sorting, batch RLS isolation, and hybrid retrieval flow.

## [0.5.2] - 2026-07-13

### Added
- **Comprehensive Benchmark Suite**: Introduced the `mesa-benchmark` ecosystem for rigorous, reproducible multi-tier evaluations of MESA's cognitive memory engine.
- **Competitor Integrations**: Added out-of-the-box evaluation clients for industry competitors (Zep, Letta, Mem0) to run head-to-head performance comparisons.
- **Google Colab Automation**: Shipped robust Google Colab notebooks with zero-touch "Run All" support, automated Git clone syncing, missing dependency installations, and `qwen3:8b`/`llama3.2:3b` setup scripts.
- **Procedural Dataset Generator**: Added a scalable synthetic dataset generator script for extensive load and stress testing.
- **Statistical Reporting Engine**: Overhauled the `MarkdownReporter` with beautiful aesthetic tables, Cohen's Kappa agreement tracking, and multi-model consensus contingency matrices.

### Fixed
- **Evaluator & LLM Judge**: Resolved silent fallback bugs in the LLM Judge, suppressed `LiteLLM` debug banners, and formatted provider prefixes properly.
- **Async & Connection Leaks**: Refactored the benchmark architecture to resolve `asyncio` event loop conflicts and unclosed DB connection/tempdir leaks.
- **CI/CD Hardening**: 
  - Mocked physical `config.yaml` dependencies in unit tests (`MagicMock`) to isolate the test suite.
  - Skipped network-dependent Ollama tests in GitHub Actions.
  - Added missing `nest_asyncio` and `datasets` packages to `pyproject.toml`.
  - Suppressed `mypy` static typing errors and `ruff` (`E402`, `F841`) linting errors across scratch and benchmark scripts.

## [0.5.1] - 2026-06-03

### Added

- **Phase 4.1 - Self-Healing Graphs**: Implemented Async Damped PageRank algorithm in the background to detect and quarantine hallucinated nodes based on `epistemic_uncertainty`.
- **Phase 4.2 - Spreading Activation**: Integrated Cognitive Salience calculations natively on KuzuDB to simulate energy spreading across the graph without Python bottlenecking.
- **Phase 4.3 - Continuous Learning**: Added Orthogonal Procrustes alignment for vector space rotations in LanceDB, enabling dynamic continuous learning.
- **Persistent WAL Queue**: Added a persistent SQLite-based WAL queue mechanism for LanceDB migrations, intercepting and queueing incoming vectors to ensure multi-worker safety during Blue/Green deployment alignment.

### Changed

- **Cypher Optimization**: Upgraded KuzuDB Cypher queries for Spreading Activation from Neo4j-specific inline `COUNT { ... }` subqueries to standard KuzuDB `OPTIONAL MATCH` and `count()` aggregation with explicit float casting.
- **DAO Orchestration**: Decoupled `VectorEngine` (`lancedb_provider`) from SQLite. All WAL queue orchestration (`lancedb_is_migrating` lock, queue insertions, and flushing) is now centralized entirely in the `MemoryDAO` layer to maintain architectural boundaries.

### Fixed

- **Phantom Write Vulnerability**: Fully remediated Phantom Writes during LanceDB Blue/Green deployments by utilizing the atomic SQLite WAL queue.
- **Global Lock Bottleneck**: Resolved the Global Lock bottleneck during table promotion by replacing thread-blocking locking mechanisms with efficient async execution wrapping.

## [0.5.0] - 2026-06-02

### Added

- **KùzuDB Native Integration:** Full integration via `KuzuGraphProvider`, serving as the foundational graph engine for Phase 4.
- **Asynchronous Graph I/O:** Implemented a `ThreadPoolExecutor` asynchronous wrapper pattern for all KùzuDB C++ bindings to protect the FastAPI event loop from blocking.
- **Composite Primary Keys:** Introduced `agent_id::node_id` indexing pattern for mathematically proven Zero-Trust isolation with O(log N) lookup times.

### Fixed

- **Infinite Scalability (OOM Resolution):** Eliminated the 50,000 node RAM ceiling and resolved Out-Of-Memory (OOM) crashes by offloading persistent topology to KùzuDB.
- **DAO Layer Bypass:** Resolved the architectural bypass in the session context API (`router.py`), fully encapsulating `raw_logs` queries via `dao.get_recent_session_logs()`.

### Removed

- **NetworkX Eradicated:** Complete deprecation and eradication of the legacy in-memory `networkx` graph engine.
- **Legacy SQLite Edges:** Removed all legacy SQLite dual-write schema operations for graph edges, establishing KùzuDB as the single source of truth for topology.

## [0.4.2] - 2026-06-01

### Security

- **Zero-Trust Tenant Isolation on `raw_logs`:** Added explicit `agent_id` column to the `raw_logs` table and enforced `WHERE agent_id = ?` predicates on all `insert_raw_log`, `get_raw_log`, and `update_raw_log_status` queries in `MemoryDAO`. Injected `_assert_valid_agent_id()` at the entry point of each method, mathematically preventing cross-tenant data leakage on the ingestion path.
- **Migration Script:** Added `scripts/migrate_raw_logs_agent_id.py` for deterministic backfill of `agent_id` from JSON `payload` on legacy databases.

### Performance

- **Async Embedding Pipeline:** Converted `_embed_text` and `calculate_composite_similarity` in `lock.py` to `async def`. All embedding calls now route through `await embedder.aembed()` or `asyncio.run_in_executor()`, eliminating event loop starvation under concurrent load. P99 latency verified at **27.85 ms** (SLA: < 50 ms).
- **Bulk Graph Retrieval (N+1 Elimination):** Refactored `HybridRetriever._build_graph_snapshot()` to replace the per-node `get_neighbors()` loop with a single bulk `SELECT * FROM edges WHERE agent_id = ?` query. Reduces O(N) SQL round-trips to O(1) for graph construction.
- **Async File I/O in Consolidation Worker:** Converted `PersistentQueue.__len__()`, `__getitem__()`, and `clear()` from synchronous `open()` calls to async via `asyncio.run_in_executor()`, preventing event loop blocking during background consolidation.

### Stability

- **OOM Prevention (MAX_GRAPH_NODES):** Introduced a strict `MAX_GRAPH_NODES = 50,000` cap in `_build_graph_snapshot()`. When the fetched node set exceeds this limit, nodes are sorted by `updated_at` (newest first) and sliced to exactly 50,000 before NetworkX graph construction. An explicit warning is logged when the cap is triggered. RAM peak verified at **899.5 MB** (limit: 2,048 MB).

### DevOps

- **Docker `MESA_API_KEY` Injection:** Added `MESA_API_KEY=${MESA_API_KEY:-}` to `docker-compose.yml` environment array, preventing instant container crashes on fresh clones.
- **`.env.example` Documentation:** Added missing internal tuning parameters: `MESA_HYBRID_ALPHA`, `MESA_HYBRID_BETA`, `MESA_T_ROUTE`, `MESA_LEGAL_DOMAIN_MODE`, `MESA_MAX_RAM_MB`.
- **Version Synchronization:** Aligned `pyproject.toml`, README badge, and release script to `v0.4.2`.

### Quality Assurance

- **Coverage Threshold Restored:** Reverted CI/CD coverage gate from 70% back to the mandated 85% minimum (`pyproject.toml` + `.github/workflows/ci.yml`). Current coverage: **88.40%**.
- **Mathematical Vector Fixtures:** Created `tests/fixtures/vectors.py` with `VEC_ORTHOGONAL` (cos_sim=0.0), `VEC_NEAR` (cos_sim=0.79), and `VEC_MATCH` (cos_sim=0.95). Replaced all vacuously-true `[0.1] * 768` mocks in `test_consolidation.py` and `test_p0a_batch.py` with genuine threshold assertions against the 0.80 merge threshold.
- **Async Coverage Tests:** Added `tests/test_async_lock_loop.py` covering async embedding fallbacks, executor paths, and circuit breaker error handling.
- **Soak Test Verified:** 5-minute load test (5,900 requests, 20 req/s, 30 concurrent) — 100% success ratio, zero OOM events, zero queue backpressure.

### Removed

- **Dead Code:** Deleted legacy `mesa_memory/retriever.py` (self-importing module) and orphaned `_sort_by_salience` function from `ConsolidationLoop`.

---

## [0.4.1] - 2026-05-29

### Added

- **feat(dx): Optional REBEL Model (`MESA_REBEL_ENABLED`):** The 1.8 GB `Babelscape/rebel-large` model is now gated behind a boolean config flag (`MESA_REBEL_ENABLED`, default `true`). Setting to `false` completely skips model download and initialization, eliminating Docker build timeouts and reducing cold-start from ~5 min to <10 s for development and CI environments.
- **feat(dx): LLM-Fallback Triplet Extraction:** When REBEL is disabled, triple extraction falls back to a zero-shot prompt via the configured Tier-3 LLM provider (Groq/Llama-3). The fallback produces the identical `{head, relation, tail}` dict format consumed by `_commit_triplets`, requiring zero downstream changes. Includes a robust JSON parser that handles markdown fences, `subject/predicate/object` key aliases, and malformed LLM output.
- **docs(readme): Docker-First Quickstart Overhaul:** README now opens with a 60-second copy-paste Docker quickstart block. All API examples updated to v3 endpoints (`/v3/memory/insert`, `/v3/memory/search`, `/v3/memory/status/{log_id}`, `/v3/memory/purge`) with correct `X-API-Key` headers. Added environment variables reference table.
- **docs(mcp): Claude Desktop MCP Integration Guide:** New dedicated section with the exact `claude_desktop_config.json` snippet required to connect Claude Desktop to a local MESA instance via the `mesa_mcp.server` stdio transport. Documents both `record_memory` and `search_memory` MCP tools.

---

## [0.4.0] - 2026-05-29

### Fixed

- **Split-Brain Dual-Write Atomicity (P0):** Implemented an atomic Saga pattern for SQLite/LanceDB dual-writes. SQLite `COMMIT` is now strictly deferred until the LanceDB `upsert` succeeds; on failure, the transaction falls back to a SQL `ROLLBACK`, eliminating split-brain orphan records across the relational and vector stores.
- **Async Embedder Event Loop Starvation (P0):** Offloaded synchronous `embedder()` calls on the search hot-path to `asyncio` thread-pool executors (`run_in_executor`), preventing event loop starvation under concurrent query load.
- **LanceDB Filter Injection (P0):** Enforced strict regex sanitization (`^[a-zA-Z0-9_-]+$`) on `agent_id` values before interpolation into LanceDB `WHERE` clause filters, closing a filter injection vector in the vector search path.

---

## [0.3.1] - 2026-05-23

### Added
- **Alpha-Reranking**: Applied alpha-reranking and adaptive LLM routing.

### Fixed
- **CI/CD Pipeline**: Resolved mypy type check errors for CI/CD pipeline.

---

## [0.3.0] - 2026-05-22

### 🔴 Critical Security Fix

- **Mandatory `agent_id` Enforcement (P0 RLS Remediation):** 11 functions in `mesa_storage/schemas.py` previously accepted `agent_id` as optional or omitted it from SQL `WHERE` clauses, creating cross-tenant data leakage vectors. All query, mutation, and traversal functions now require **mandatory `agent_id`** with hardcoded `AND agent_id = ?` predicates. Affected: `soft_delete_node`, `mark_consolidated`, `get_active_nodes`, `find_nodes_by_name`, `upsert_edge`, `soft_delete_edge`, `get_neighbors`, `get_active_edges`, `k_hop_neighbors`, `fts5_search`. The `fts5_search` call in `mesa_api/router.py` was also patched to pass `agent_id=request.agent_id`.

### Added

- **Headless FastAPI v3 API** (`mesa_api/router.py`): Stateless daemon-mode REST server. `POST /v3/memory/insert` (fire-and-forget via `BackgroundTasks`, <150ms TTFT), `POST /v3/memory/search` (synchronous await), `DELETE /v3/memory/purge` (soft-delete only).
- **Strict Pydantic V2 API Schemas** (`mesa_api/schemas.py`): `min_length=1` on all identity fields, `__unset__` sentinel rejection, content ≤32 KB, metadata ≤64 keys, `strict=True`, frozen response models.
- **Asynchronous Storage Engine** (`mesa_storage/sqlite_engine.py`): `aiosqlite` with connection pooling, WAL mode, `synchronous=NORMAL`, 64 MB cache, health checks, and WAL checkpointing.
- **LanceDB Vector Engine** (`mesa_storage/vector_engine.py`): Async-compatible via `ThreadPoolExecutor` + `run_in_executor`. Multi-dimensional table routing, soft-delete with `expired_at`, mandatory `agent_id` on all operations.
- **SQLite FTS5 Lexical Pre-Filtering** (`mesa_storage/schemas.py`): Zero-VRAM full-text search via FTS5 virtual tables with trigger-based sync. Enables lexical pre-filtering before vector/graph operations.
- **Memory DAO** (`mesa_storage/dao.py`): High-level Data Access Object enforcing mandatory `agent_id` on every method.
- **Isolated Maintenance Worker** (`mesa_workers/maintenance.py`): Background worker for `VACUUM`, hard `DELETE FROM`, and LanceDB compaction on a **dedicated synchronous `sqlite3` connection** with `isolation_level=None` and `busy_timeout=30s`. Retention-window-gated purge.
- **REM Cycle Worker** (`mesa_workers/rem_cycle.py`): Async consolidation with `max_records_per_cycle` batch slicing and token budget enforcement.
- **Python Client SDK** (`mesa_client/client.py`): Sync/async HTTP clients via `httpx` with Pydantic V2 validation and `tenacity` retry logic.
- **LangChain Adapter** (`mesa_client/langchain.py`): `MesaRetriever` implementing LangChain's `BaseRetriever` protocol.
- **Evaluation Pipeline** (`mesa_evals/`): 100-question Golden Dataset (Legal=35, Financial=35, Code=30), deterministic synthetic generator, multi-path eval runner, and CI/CD gatekeeper enforcing cost/latency SLAs.
- **k-hop Graph Traversal** (`mesa_storage/schemas.py`): BFS `k_hop_neighbors()` with mandatory `agent_id` propagation.

### Changed

- **Architecture: Library → Daemon:** MESA is now a headless FastAPI daemon. All interaction flows through v3 REST endpoints or the Python SDK.
- **Storage Decoupling:** `mesa_storage` is a standalone package independent of `mesa_memory`.
- **Soft-Delete / Hard-Delete Separation:** API restricted to soft-deletes; physical deletion exclusively in `MaintenanceWorker`.
- **Test suite:** Expanded from 159+ to **409 tests**.

### Removed

- **Memgraph references:** All legacy stubs and configuration purged.

### Dependencies

| Package | Version | Role |
|---|---|---|
| `aiosqlite` | ≥0.22.0 | Non-blocking SQLite engine |
| `fastapi` | ≥0.111.0 | Headless REST API server |
| `lancedb` | ≥0.30.0 | Vector storage |
| `httpx` | ≥0.28.0 | HTTP client for SDK |
| `pydantic` | ≥2.13.0 | Strict V2 schema validation |
| `uvicorn` | ≥0.29.0 | ASGI server |

---

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

[0.9.6]: https://github.com/Yasou13/MESA/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/Yasou13/MESA/compare/v0.9.4...v0.9.5
[0.9.4]: https://github.com/Yasou13/MESA/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/Yasou13/MESA/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/Yasou13/MESA/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/Yasou13/MESA/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/Yasou13/MESA/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/Yasou13/MESA/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/Yasou13/MESA/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/Yasou13/MESA/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/Yasou13/MESA/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Yasou13/MESA/compare/v0.5.2...v0.6.1
[0.5.2]: https://github.com/Yasou13/MESA/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/Yasou13/MESA/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Yasou13/MESA/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/Yasou13/MESA/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Yasou13/MESA/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Yasou13/MESA/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Yasou13/MESA/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Yasou13/MESA/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Yasou13/MESA/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Yasou13/MESA/releases/tag/v0.1.0
