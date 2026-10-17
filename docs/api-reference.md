# API reference

MESA iki versioned yüzey sunar:

- v3, geriye uyumlu lexical-core API’dir.
- v4, tenant/dataset yetkili versioned API’dir. Paket 0.8.1; yayın, sahibinin açık onayıyla production sertifikasyonu dışında yapılır.

Versioned memory, health ve metrics çağrıları `X-API-Key` ister. `/health/init`, OpenAPI, Swagger ve Redoc public yüzeylerdir; ağ/reverse-proxy politikası gerekir. V4 anahtarı
`mesa-v4-admin issue-key` ile üretilir; yalnız saltlı scrypt özeti saklanır.
OpenAPI sözleşmesinin çalışan kaynakları `mesa_api/router.py` ve
`mesa_api/v4_router.py` dosyalarıdır.

## V4 capability

`GET /v4/capability` returns `api_version`, `features`, `capabilities` and
`limits`. `features` contains the capability names whose values are true.
The response depends on the configured and operational runtime; a sample
response must not be used as a fixed feature promise.

| Capability | Meaning |
|---|---|
| `canonical_ledger`, `projection_outbox`, `idempotent_ingestion` | Durable canonical work and projection contracts |
| `vector_retrieval` | Operational embedding generation, not merely configured identity |
| `lexical_retrieval`, `assertion_relational_lane` | SQLite lexical/assertion retrieval; not graph traversal |
| `graph_projection` | Graph projection availability |
| `graph_neighbor_retrieval` | Operational neighbor retrieval implementation |
| `validity_interval_filtering` | Validity filtering, not bitemporal semantics |
| `durable_rebuild` | Enabled by `MESA_V4_REBUILD_ENABLED`; still offline and full-root |
| `associative_ppr`, `bitemporal_query`, `human_review` | Not advertised as implemented Core capabilities |

Rebuild limits are `rebuild_kind=projection`, `rebuild_scope=storage_root`, and
`requires_offline_runner=true`. A projection-only graph adapter does not supply
neighbor retrieval. Custom embedding providers are non-operational until a
successful embedding call. Configured backend failure is surfaced explicitly;
see [retrieval behavior](retrieval-and-graph.md) and [troubleshooting](RUNBOOK.md#troubleshooting).

## V4 administrative rebuild operations

Bu yüzey yalnız control-plane `ADMIN` principal içindir ve fiziksel path veya
canonical içerik kabul etmez:

| Method | Path | Başarı | İşlev |
|---|---|---|---|
| `POST` | `/v4/operations/rebuild` | `202` | Zorunlu `Idempotency-Key` ile storage-root operation submit eder |
| `GET` | `/v4/operations/{operation_id}` | `200` | Content-free state/progress döndürür |
| `POST` | `/v4/operations/{operation_id}/cancel` | `200` | Yalnız `PENDING`/`RETRYABLE_FAILED` operation'ı iptal eder |
| `POST` | `/v4/operations/{operation_id}/retry` | `202` | Güvenli `RETRYABLE_FAILED` checkpoint'ini tekrar pending yapar |
| `POST` | `/v4/rebuild` | `202` | Deprecated storage-root alias |

Flag kapalı submit/retry `501`, unsupported scoped alias `409`, invalid state
`409` döndürür. Yetkisiz status/cancel/retry operation varlığını sızdırmamak
için `404` olur. State akışı
`PENDING → CLAIMED → RUNNING → VERIFYING → READY_TO_CUTOVER → COMPLETED`;
yan durumlar `RETRYABLE_FAILED`, `FINAL_FAILED` ve `CANCELLED`'dır. Aktif veya
retryable operation sırasında mutation admission'ı `503 maintenance_pending`
olur. Offline işletim prosedürü
[`v4-rebuild-runbook.md`](v4-rebuild-runbook.md) içindedir.

## V4 catalog

| Method | Path | Gerekli yetki | İşlev |
|---|---|---|---|
| `POST` | `/v4/catalog/workspaces` | Tenant `OWNER` | Tenant/workspace oluşturur |
| `GET` | `/v4/catalog/workspaces` | Görünür kapsamda `READER` | Workspace listeler |
| `POST` | `/v4/catalog/datasets` | Dataset/workspace `OWNER` | Dataset oluşturur |
| `GET` | `/v4/catalog/datasets` | Görünür kapsamda `READER` | Dataset listeler |
| `POST` | `/v4/catalog/documents` | Dataset `WRITER` | Document oluşturur |
| `GET` | `/v4/catalog/documents` | Dataset `READER` | Document listeler |
| `POST` | `/v4/catalog/revisions` | Dataset `WRITER` | Immutable revision oluşturur |
| `GET` | `/v4/catalog/revisions` | Dataset `READER` | Revision listeler |
| `POST` | `/v4/catalog/source-chunks` | Dataset `WRITER` | Exact source chunk oluşturur |
| `DELETE` | `/v4/catalog/documents/{document_id}` | Dataset `OWNER` + `PURGE` | Kaynak sahipli purge başlatır |

Catalog IDs are tenant-scoped public IDs; physical storage keys are not public
aliases. Revision/chunk identity, finalization and the distinction between
`declared_content_hash`, `manifest_hash` and chunk `content_hash` are defined in
[concepts and ingestion](concepts-and-ingestion.md).

## V4 session ve memory

| Method | Path | İşlev |
|---|---|---|
| `POST` | `/v4/sessions/start` | Sunucu üretimli, dataset kapsamı değişmez session başlatır |
| `POST` | `/v4/memory/insert` | Exact source’u kabul edip mutation ve pipeline run oluşturur |
| `POST` | `/v4/memory/search` | Yetkili datasetlerde vector/BM25/assertion/graph RRF araması yapar |
| `GET` | `/v4/mutations/{mutation_id}` | State, pipeline, artifact ve projection durumunu verir |
| `POST` | `/v4/mutations/{mutation_id}/replay` | Yetkili DLQ/retry işini yeniden kuyruğa alır |
| `POST` | `/v4/mutations/{mutation_id}/rollback` | Kaynak sahipli rollback başlatır |
| `GET` | `/v4/sessions/{session_id}/context` | Session context ve mutation provenance’ını verir |
| `POST` | `/v4/sessions/{session_id}/end` | Durable finalization ister ve session’ı kapatır |

Insert derives tenant/workspace/agent from the authorized session and returns
`202` with `mutation_id`, `candidate_id`, `pipeline_run_id` and `raw_log_id`.
This is admission; poll status and require `COMMITTED` before assuming success.
`wait_until_committed()` also returns failed terminal states.

For multiple chunks use distinct IDs/ordinals, `finalize_revision=false` until
the last insert, then `true`. Retry the exact payload with the same body
`idempotency_key`. Revision lifecycle, rejection/replay and rollback semantics
are defined in [concepts and ingestion](concepts-and-ingestion.md).

The HTTP insert contract accepts optional typed `legal_identity`: `law_id` and
`article_id` are required within that object; `statute_code`, `version_id`,
`paragraph_id` and `clause_id` are optional. Identity must resolve consistently
through the bounded legal aliases. Caller metadata cannot supply trusted
`_mesa_*` fields. The producer remains responsible for source truth.

Search and context both accept `jurisdiction`, `valid_at`, `valid_from` and
`valid_to`. Context uses query parameters and defaults to `token_budget=2048`,
`retrieval_mode=single`. Opt-in `adaptive` permits at most one planner call and
two additional scoped searches. Scores, graph provenance, proof packing and
offline token counting are defined in [retrieval and graph](retrieval-and-graph.md).
Raw `/v4/memory/search` remains the single-query primitive.

## V4 Python SDK

Bu örnek, model-enabled V4 sunucu, provision edilmiş `credential`, mevcut
`tenant-a/workspace-a/dataset-a` kataloğu ve `agent-a` session izni varsayar.
Bunları da kuran örnek için [Getting started](getting-started.md) kullanılır.

```python
from mesa_client import MesaV4Client

with MesaV4Client("http://127.0.0.1:8000", api_key=credential) as client:
    session = client.start_session(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_ids=["dataset-a"],
        agent_id="agent-a",
    )
    accepted = client.insert(
        session_id=session["session_id"],
        dataset_id="dataset-a",
        document_id="doc-a",
        revision_id="rev-1",
        chunk_id="chunk-1",
        title="Neutral source example",
        source_ref="demo://source-a",
        content="Exact source text",
    )
    terminal = client.wait_until_committed(accepted["mutation_id"])
    if terminal["state"] != "COMMITTED":
        raise RuntimeError(terminal["state"])
    result = client.search(session_id=session["session_id"], query="Exact source")
    for hit in result["results"]:
        print(hit["assertion_id"])
        for source in hit.get("provenance", []):
            print(source["source_ref"], source["dataset_id"], source["chunk_id"])
    context = client.get_context(session_id=session["session_id"], query="Exact source")
    print(context["context"])
    client.end_session(session_id=session["session_id"])
```

`accepted` is durable admission, not finished retrieval. `client.status(mutation_id)`
exposes `state`, `failure_class`, `rejection_reason`, `pipeline_run`, `artifacts`
and `projections`. Wait for `COMMITTED`; `REJECTED`, `DEAD_LETTER`, `BLOCKED` and
`ROLLED_BACK` are terminal outcomes too. A wait timeout raises `MesaNetworkError`
and leaves the mutation running. Recheck status; for an uncertain insert retry
the same payload with an explicit, unchanged `idempotency_key`.

`results=[]` on a successful search means no matching visible evidence. Provider,
storage and authorization failures raise errors instead. Each hit's
`assertion_id` identifies a canonical claim; `provenance` links it to the source
and dataset/chunk. A score ranks evidence; it is not a truth probability.
Context returns packed text in `context` and structured evidence in
`canonical_memories`. Finish retrieval before `end_session`; closing the HTTP
client alone does not end the server session.

For async code use `async with AsyncMesaV4Client(...) as client` and `await` each
of these methods, including polling and `end_session`. Do not run the blocking
`MesaV4Client` inside an event loop. Async clients also support `await client.aclose()`;
sync clients support `client.close()`.

Çok chunk'lı revision için ilk çağrılarda `finalize_revision=False`, son
çağrıda varsayılan `True` kullanılır. `search` ve `get_context` metotları
`valid_at`, `valid_from` ve `valid_to` parametrelerinin üçünü de destekler.

`MesaV4Client` ve `AsyncMesaV4Client`; capability, catalog, session, insert,
search, status, wait, replay, rollback, purge, context ve end işlemlerinin
yanında `submit_rebuild`, `operation_status`, `cancel_operation` ve
`retry_operation` control-plane metotlarını sunar. V3 istemcileri `MesaClient`
ve `AsyncMesaClient` olarak korunur. Dataset-bound MCP yüzeyi mutating global
rebuild aracı sunmaz.

## V4 authorization

Route katmanı şu bağlamı sunucu tarafında çözer:

```text
principal → tenant → workspace → dataset → agent → session
```

`OWNER`, `WRITER` ve `READER` rolleri aşağı doğru miras kalır. Purge ve
rollback için ayrıca dataset `PURGE` veya `ROLLBACK` izni gerekir. Agent bir
persona/hesaplama bağlamıdır; tek başına güvenlik sınırı değildir.
Rollback ve replay tarihsel yönetim işlemleridir; originating session kapalı
olabilir. Bununla birlikte principal/session ownership, mutation ile session'ın
tenant/workspace/dataset/agent scope eşleşmesi, dataset rolü ve açık
`ROLLBACK` izni her çağrıda doğrulanır.

Provision keys, roles and explicit dataset permissions using the
[operations runbook](RUNBOOK.md#credential-bootstrap); the
[CLI reference](api-reference.md#cli) identifies admin entry points. RBAC uses its
own schema version in the policy database, independently of Alembic migrations
for canonical storage. Lost grants from an old database cannot be inferred or
recreated automatically.

Internal DAO ownership, projection and Graph V2 structure belong in the
[architecture](architecture-v4.md); they are not separate public HTTP methods.

## V3 compatibility API

| Method | Path | İşlev |
|---|---|---|
| `POST` | `/v3/memory/insert` | Durable lexical-core dispatch |
| `POST` | `/v3/memory/search` | V3 retrieval |
| `GET` | `/v3/memory/status/{log_id}` | Raw-log iş durumu |
| `DELETE` | `/v3/memory/purge` | V3 soft-delete akışı |
| `POST` | `/v3/memory/session/start` | V3 session başlatma |
| `GET` | `/v3/memory/session/{session_id}/context` | V3 context |
| `POST` | `/v3/memory/session/{session_id}/end` | Durable finalization |

V3’ün `agent_id/session_id` RBAC ve telafi edici multi-store davranışı yalnız
uyumluluk yüzeyi içindir. V4’ün dataset güvenliği veya ledger/outbox
garantileriyle karıştırılmamalıdır.

## Health ve metrics

- `/health` ve `/v3/health`: API anahtarıyla `status`, `maintenance`, `rebuild_state` özeti
- `/health/init`: public readiness; hazırsa `200 {"status":"ready"}`, değilse `503`
- `/metrics`: API anahtarıyla Prometheus sayaçları ve gauge'ları

`/health` degraded veya maintenance durumunda da HTTP 200 dönebilir; yalnız
HTTP kodunu başarı saymayın, `status` alanını kontrol edin. Ayrıntılı kuyruk,
projection ve rebuild sayaçları `/metrics` üzerinden okunur.

V4 metrikleri projection backlog/DLQ/stuck lease, cleanup backlog/BLOCKED,
ownerless registry, shared artifact ve rebuild state/duration/progress,
parity-missing, staging-byte ve rollback sayılarını kapsar. Alert kuralları
`docs/prometheus_alerts.yml` içindedir.

## İstek/SDK ayrıntıları

V4 HTTP insert ve sync/async SDK insert için `title` zorunludur. `title` 1..512,
`content` 1..32768 karakter, `source_ref` 1..2048, `evidence_span` en çok 4096,
`idempotency_key` en çok 128 karakterdir. Dataset session üyeliğinde olmalıdır.
HTTP ve sync/async SDK `insert`, opsiyonel `legal_identity` parametresini destekler.
Public/source ID validator farkları için çalışan OpenAPI kullanılır.

Search request `session_id`, `query` gerektirir; query 1..4096 karakter,
`limit` default 10 ve 1..50, `dataset_ids` verilirse 1..64 ve session altkümesi,
`graph_mode` enabled/disabled'dır. ISO timestamp string'leri timezone offset
içermelidir. `valid_from > valid_to` reddedilir. Response `session_id`,
`dataset_ids`, `results`, optional `scope_audit`, `graph_ablation`, `vector_lane`
taşır. Sonuç provenance'ın varlığı source truth garantisi değildir.

SDK sync/async client timeout default 10s ve max_retries 3; yalnız güvenli
network retry boundary geçerlidir. API HTTP hata yanıtları `MesaAPIError`,
network `MesaNetworkError`, SDK schema `MesaValidationError` olur. Status
400/401/403/404/409/413/422/429/503 endpoint'e göre değişir; OpenAPI ve contract
testleri kaynak otoritesidir. Queue saturation/rebuild maintenance için
Retry-After dikkate alınır. Dakikalık default 60/minute, daily default 10000
principal bazındadır; health/metrics daily budget dışındadır.

### Actionable errors

Catch `MesaAPIError` to inspect `status_code`, `error` and `detail`. HTTP bodies
also include `retryable`; the SDK does not automatically retry HTTP failures.
Rejected request values and internal route paths are omitted from validation
errors. For example, a non-string search query returns HTTP 422:

```json
{"error":"VALIDATION_ERROR","detail":"body.query: Input should be a valid string","status_code":422,"retryable":false}
```

| Response | Next step |
|---|---|
| 401 `UNAUTHORIZED` | Supply the issued MESA key, not the provider key |
| 403 `ACCESS_DENIED` | Check tenant/dataset roles, agent grant and session scope |
| 404 `NOT_FOUND` | Check the ID and credential scope; hidden objects may also return 404 |
| 409 `SESSION_INACTIVE` | Start an authorized active session before new writes |
| 409 `IDEMPOTENCY_CONFLICT` | Reuse the original payload with that key, or use a new key for new content |
| 422 `VALIDATION_ERROR` | Correct the named field/format; timestamps need a timezone |
| 503 `BACKEND_UNAVAILABLE`, detail `canonical_processing_unavailable` | Enable configured extraction/embedding in one combined owner; models-off startup cannot ingest V4 |
| 503, detail `vector_backend_unavailable` / `graph_backend_unavailable` | Check provider credentials, embedding identity and backend readiness; an empty result is not a substitute |
| 503, detail `queue_over_capacity` / `maintenance_pending` | Respect `Retry-After` when present and wait for capacity/maintenance |

Failed background processing is reported by mutation status even when the insert
returned 202. Inspect its failure/projection fields; an admission response or an
empty early search does not establish successful processing. Startup
configuration errors appear in the server terminal before readiness; see
[configuration](configuration.md#environment-file-and-first-startup).

List catalog çağrıları burada açıkça belgelenmiş pagination garantisi sunmaz;
response/schema'yı inceleyin. İlk catalog/ACL ve credential ön koşulları dahil
çalıştırılabilir örnek [Getting started](getting-started.md) içindedir.

Canonical V4 insertion requires the model-enabled combined processing consumer.
Model-disabled boot is topology/compatibility smoke and rejects V4 writes with
`503 canonical_processing_unavailable`; it is not a V4 insert/search demonstration.

The [OpenAPI snapshot](reference/openapi.json) is a generated reference.
Use the running app's `/openapi.json` and route/schema source for the exact
checkout; do not infer snapshot freshness from this page. Generating a schema
without server startup does not exercise authentication or runtime behavior.

<a id="cli"></a>

## CLI commands

The entry points are declared in `pyproject.toml`. Run `.venv/bin/<command>
--help` after the locked install. These commands do not have a universal
`--version`; read package metadata with:

```bash
.venv/bin/python -c "from importlib.metadata import version; print(version('mesa-memory'))"
```

| Command | Scope |
|---|---|
| `mesa-v4-admin` | Offline credentials and ACL provisioning; defaults to `MESA_STORAGE_ROOT/rbac_policy.db` |
| `mesa-recovery` | Trusted-root checksummed backup, validate and new-root restore |
| `mesa-v4-rebuild` | Offline full-root projection rebuild/adoption; never an online worker |
| `mesa-mcp` | stdio server; V3 default, explicit V4 settings supported |
| `mesa-mcp-gateway` | Independently supervised HTTP/Streamable HTTP control gateway |
| `mesa-antigravity-bridge` | Binding-scoped stdio bridge with encrypted write spool |
| `mesa` | Codex/Antigravity install, doctor, profiles, credential lifecycle and operations approvals |

### Admin bootstrap

```bash
.venv/bin/mesa-v4-admin --help
.venv/bin/mesa-v4-admin grant-role --principal docs-principal --tenant docs-tenant --role OWNER
.venv/bin/mesa-v4-admin grant-agent --principal docs-principal --agent docs-agent --permission SESSION_CREATE
```

Use these only against the intended stopped/new policy database. `issue-key`
prints the credential once; `rotate-key --key-id` revokes/replaces it.
`grant-control` assigns `ADMIN`. `grant-dataset-permission` explicitly assigns
`PURGE`/`ROLLBACK`; role inheritance alone does not give those operations.
Dataset role grants require both `--workspace` and `--dataset`. Corresponding
`revoke-role`/`revoke-dataset-permission` remove authorization.

### Recovery, rebuild and MCP

Use [operations](RUNBOOK.md) and [rebuild](v4-rebuild-runbook.md) for safety,
trusted paths, locking, status, retry/lease, exit codes and retained generations.
Never use arbitrary raw-store cleanup as a CLI substitute.
[MCP](../README_MCP.md) describes binding credentials, encryption and local
operator approval. `mesa operations approve/reject` acts on durable operation
IDs with server-side ADMIN authority; it does not bypass gateway policy.

`python -m mesa_memory.runtime_entrypoint` selects the explicit runtime profile;
there is no `mesa serve` command. `mesa-benchmark` belongs to the separate
benchmark project and is not installed by this Core package.


<a id="mcp-control-api"></a>

## MCP control API

MCP istemcilerini, bağlantıları, credential özetlerini ve onayları yönetmek için
sunucu tarafı control API kullanılır. Kaynaklar `mesa_api/routers/control/router.py`,
`mesa_mcp/gateway/` ve `mesa_storage/control/` dizinlerindedir.

### Yetki ve erişim

API prefix'i `/control/mcp`'dir. Her çağrı aktif authenticated principal ve
server-side `ADMIN` rolü gerektirir. Loopback erişimi bu yetkiyi otomatik vermez.
Credential özetleri plaintext token içermez. Gateway transport/binding credential
ayrı bir yetki sınırıdır. Uzak erişimde authenticated reverse proxy kullanılır.

### Mevcut route'lar

| Method | `/control/mcp` altındaki path | İşlev |
|---|---|---|
| POST/GET | `/clients` | Client oluştur/listele |
| GET | `/clients/{client_id}` | Client ayrıntısı |
| PUT | `/clients/{client_id}/enabled` | Enable/disable (`enabled` query) |
| GET | `/clients/{client_id}/bindings` | Project/dataset bindings |
| GET | `/connections` | Aktif bağlantılar |
| GET/POST | `/settings` | Ayarlar; POST `key` ve `value` query alanları |
| GET/POST | `/policies` | Politika kuralları |
| GET | `/managed-clients` | Codex/Antigravity güvenli binding/credential özeti |
| POST | `/credentials/{credential_id}/revoke` | Credential iptali |
| GET | `/activity`, `/activity/{call_id}` | Bounded aktivite listesi/ayrıntısı |
| GET | `/approvals`, `/approvals/pending` | Onay listesi |
| POST | `/approvals/{approval_id}/decide` | Yetkili karar |
| GET | `/overview` | Aggregate durum |

Canlı OpenAPI, parametre ve şemaların son kaynağıdır. Bu API bir web arayüzü
sunmaz; kaldırılmış dashboard ve showcase uygulamalarını başlatmak gerekmez.

### Gateway ve onay yaşam döngüsü

Codex doğrudan HTTP MCP gateway'e; Antigravity binding-scoped stdio bridge ile
bağlanır. Gateway policy, credential, approval ve durable operation state'ini
saklar. Varsayılan write onayı işlem ID'siyle pending döner; uzun stdio çağrısını
açık tutmaz. `mesa operations approve/reject` yalnız geçerli pending operation,
aktif client/binding ve ADMIN principal için karar kaydeder. Gateway normal
consumer'ı onaylanmış işi yürütür. Aynı idempotency key aynı operation'ı döndürür.

CLI, public operation ID kullanır. Control API'nin
`POST /control/mcp/approvals/{approval_id}/decide` yolu ise approval ID kullanır;
bu iki kimlik birbirinin yerine geçmez. Legacy middleware yalnız approval ID
döndürdüğünde karar control API üzerinden verilir.

Credential install, doctor, rotate, disable/uninstall, protected XDG files ve
operator CLI örnekleri [MCP rehberindedir](../README_MCP.md).

### Kanıt sınırı

Aktivite kaydı context yanıtının teslim edildiğini gösterebilir; modelin o
bilgiyi gerçekten kullandığını veya cevabın doğruluğunu kanıtlamaz. İnsan
onayı Core V4 `human_review` legal capability bayrağına eşit değildir.
