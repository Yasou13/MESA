# API reference

MESA iki versioned yüzey sunar:

- v3, geriye uyumlu lexical-core API’dir.
- v4, tenant/dataset yetkili full-cognitive API’dir ve yayınlanmamıştır.

Tüm HTTP çağrıları `X-API-Key` ister. V4 anahtarı
`mesa-v4-admin issue-key` ile üretilir; yalnız saltlı scrypt özeti saklanır.
OpenAPI sözleşmesinin çalışan kaynakları `mesa_api/router.py` ve
`mesa_api/v4_router.py` dosyalarıdır.

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

Catalog hiyerarşisi
`Tenant → Workspace → Dataset → Document → DocumentRevision → SourceChunk`
şeklindedir. Revision ve chunk içerikleri immutable’dır; güncelleme yeni
revision oluşturur.

## V4 session ve memory

| Method | Path | İşlev |
|---|---|---|
| `POST` | `/v4/sessions/start` | Sunucu üretimli, dataset kapsamı değişmez session başlatır |
| `POST` | `/v4/memory/insert` | Exact source’u kabul edip mutation ve pipeline run oluşturur |
| `POST` | `/v4/memory/search` | Yetkili datasetlerde vector/BM25/graph RRF araması yapar |
| `GET` | `/v4/mutations/{mutation_id}` | State, pipeline, artifact ve projection durumunu verir |
| `POST` | `/v4/mutations/{mutation_id}/replay` | Yetkili DLQ/retry işini yeniden kuyruğa alır |
| `POST` | `/v4/mutations/{mutation_id}/rollback` | Kaynak sahipli rollback başlatır |
| `GET` | `/v4/sessions/{session_id}/context` | Session context ve mutation provenance’ını verir |
| `POST` | `/v4/sessions/{session_id}/end` | Durable finalization ister ve session’ı kapatır |

`POST /v4/memory/insert` tenant, workspace ve agent değerlerini istemciden
almaz; bunları doğrulanmış session’dan türetir. İstek `session_id`,
`dataset_id`, document/revision/chunk kimlikleri, `source_ref` ve exact
`content` taşır. `202` yanıtı `mutation_id`, `candidate_id`,
`pipeline_run_id` ve `raw_log_id` döndürür.

Başarılı mutation yolu:

```text
RECEIVED → EXTRACTED → VALIDATED
         → SQL_APPLIED → VECTOR_APPLIED → GRAPH_APPLIED → COMMITTED
```

Pipeline run görünümü:

```text
QUEUED → RUNNING → EXTRACTED → VALIDATED → PROJECTING → COMMITTED
```

Terminal/kurtarma durumları arasında `REJECTED`, `RETRY_PENDING`, `DLQ`,
`ROLLING_BACK`, `ROLLED_BACK` ve `BLOCKED` bulunur. Tier-3 reddi aktif
retrieval artifact’ı üretmez.

## V4 Python SDK

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
        source_ref="contract://a",
        content="Exact source text",
    )
    committed = client.wait_until_committed(accepted["mutation_id"])
```

`MesaV4Client` ve `AsyncMesaV4Client`; catalog, session, insert, search,
status, wait, replay, rollback, purge, context ve end işlemlerini sunar. V3
istemcileri `MesaClient` ve `AsyncMesaClient` olarak korunur.

## V4 authorization

Route katmanı şu bağlamı sunucu tarafında çözer:

```text
principal → tenant → workspace → dataset → agent → session
```

`OWNER`, `WRITER` ve `READER` rolleri aşağı doğru miras kalır. Purge ve
rollback için ayrıca dataset `PURGE` veya `ROLLBACK` izni gerekir. Agent bir
persona/hesaplama bağlamıdır; tek başına güvenlik sınırı değildir.

Operator komutları:

```bash
mesa-v4-admin issue-key --principal service-a
mesa-v4-admin grant-role --principal service-a --tenant tenant-a --role OWNER
mesa-v4-admin grant-agent --principal service-a --agent agent-a \
  --permission SESSION_CREATE
mesa-v4-admin grant-dataset-permission --principal service-a \
  --tenant tenant-a --dataset dataset-a --permission ROLLBACK
```

## `MemoryDAO` v4 sınırı

`mesa_storage.dao.MemoryDAO`, v4 için SQLite karar kaynağını ve LanceDB/Kùzu
projection’larını yönetir. Başlıca sözleşmeler:

- catalog/session: `create_v4_*`, `list_v4_*`, `get_v4_session`;
- ingestion: `record_mutation`, `record_mutation_extraction`;
- projection: `claim_projection`, `complete_projection`,
  `fail_projection`;
- pipeline: `get_pipeline_run`, compare-and-swap state ve event kayıtları;
- ownership: `memory_artifacts` ile `artifact_sources`;
- recovery: rollback/cleanup outbox, replay ve iki yönlü reconciliation;
- retrieval: `search_v4_memory`, zorunlu tenant/dataset filtresi ve RRF;
- observability: `get_v4_projection_health`.

Projection anahtarı `mutation_id + artifact_id` ile idempotenttir. Sıra
SQLite → vector → graph olarak veritabanı önkoşullarıyla korunur. Kùzu karar
kaynağı değil, SQLite assertion ledger’ının projection’ıdır.

## Graph V2

Entity kimliği sabit namespace ile tenant, entity type ve identity key’den
UUID5 üretilerek hesaplanır. Identity key önce ontology/external identity’yi,
yoksa NFKC + whitespace normalization + casefold uygulanmış canonical adı
kullanır. Alias ve external ID aynı entity’ye bağlanır.

Assertion; subject, predicate, entity object veya literal, source/evidence,
jurisdiction, authority, confidence, temporal alanlar, mutation ve pipeline
run provenance’ını taşır. `CONTRADICTS` ve `SUPERSEDES` assertion’lar arası
ilişkilerdir. PageRank yalnız gözlem telemetrisidir ve artifact karantinası
veya silme kararı veremez.

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

- `/health`: process ve store sağlık özeti
- `/health/init`: readiness
- `/metrics`: Prometheus exposition

V4 metrikleri projection backlog/DLQ/stuck lease, cleanup backlog/BLOCKED,
ownerless registry ve shared artifact sayılarını kapsar. Alert kuralları
`docs/prometheus_alerts.yml` içindedir.
