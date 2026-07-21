# Kritik Veri Akışları

Her akış statik, unit/component, integration/runtime veya staging kanıt seviyelerinden biriyle sınıflandırılır. Statik akış haritası runtime doğrulaması sayılmaz.

## Akış şablonu

### FLOW-XXX — Akış adı

| Alan | Değer |
|---|---|
| Durum | Henüz tanımlanmadı |
| Başlangıç noktası | — |
| Authentication | — |
| Validation | — |
| Service katmanı | — |
| Persistence | — |
| Worker veya queue | — |
| Response | — |
| Failure path | — |
| Retry davranışı | — |
| Transaction sınırı | — |
| Tenant veya agent izolasyonu | — |
| İlgili testler | — |
| Kanıt | — |


# Faz 2 — Koddan çıkarılan veri akışları

## FLOW-START-001 — Startup/shutdown

Başlangıç: Docker/uvicorn → `mesa_memory.api.server:app`. Auth: lifespan başında `_require_api_key`. Core: logging/tracing, üç engine, DAO, RBAC, loop/worker başlangıcı. Storage: `MESA_STORAGE_PATH` altında SQLite, LanceDB, Kuzu, RBAC, Valence. Worker: API process task’leri. Response: `state.is_ready=True`, `/health/init` DAO health kontrolü. Failure: herhangi init hatası readiness öncesi lifespan’ı keser; worker scheduling hataları loglanıp devam edebilir. Tenant: başlangıçta yok. Doğrulama: statik. Shutdown: REM/maintenance/PageRank/WAL stop/cancel, loop stop, valence persist, graph/SQLite close; Vector close ve üç task cancellation’ı açıkça görülmedi.

## FLOW-ING-001 — Ingestion

Başlangıç: POST `/v3/memory/insert`. Auth: router dependency API key/daily limit; endpoint RBAC WRITE. Validation: `MemoryInsertRequest`. Core: `dao.insert_raw_log(agent_id,payload)` ardından `BackgroundTasks.process_cold_path`. Storage: SQLite `raw_logs` kalıcı staging. Worker: aynı API process BackgroundTask; ECOD/novelty → opsiyonel REBEL → ConsolidationLoop/Tier-3 → DAO graph/vector/SQLite write ve status. Response: 202/`DEFERRED`, cold persistence tamamlanmadan. Failure: cold path status `failed/rejected`; static source top-level exception yakalar. Tenant: payload `agent_id` DAO ve worker’a taşınır. Doğrulama: statik; `router.py` ve `ingestion_worker.py` CWD debug dosyası yazdığı için storage sınırı ihlali ARCH-003 altında kaydedildi.

## FLOW-RET-001 — Retrieval

Başlangıç: POST `/v3/memory/search`. Auth: API key/daily limit ve `HybridRetriever` READ RBAC. Validation: `MemorySearchRequest`. Core: query normalize/entity extraction → DAO seed/cold-start → vector, graph, FTS coroutine’leri `asyncio.gather` ile paralel → cold-start veya alpha fusion → opsiyonel CrossEncoder batch hydration/rerank. Storage: DAO üzerinden LanceDB, Kuzu, SQLite FTS ve node hydration. Worker: yok. Response: `MemorySearchResponse`; router 30s timeout. Failure: individual vector/graph/FTS hata sonucu boş/fallback; timeout 504. Tenant: agent_id bütün DAO çağrılarına parametre olarak taşınır. Doğrulama: statik.

## FLOW-PURGE-001 — Purge/lifecycle

Başlangıç: DELETE `/v3/memory/purge`. Auth: API key/daily limit + RBAC WRITE. Validation: `MemoryPurgeRequest`. Core: `dao.purge_memory(agent_id,scope,session)`. Storage: Vector soft-delete, SQLite tombstone/invalid-at; physical removal API hot path dışında. Worker: `MaintenanceWorker` hard-delete/VACUUM/Vector optimize schedule. Response: `purged` ve count. Failure: DAO exception 500. Tenant: agent_id DAO predicate. Doğrulama: statik; graph tombstone/physical lifecycle sonucu Faz 6/7’ye açık soru.

## FLOW-SDK-001 — SDK request

Başlangıç: `MesaClient` veya `AsyncMesaClient`. Auth: constructor API key varsa `X-API-Key`. Validation: paylaşılan `mesa_api.schemas` request modelleri. Core: httpx retry wrapper; POST insert/search veya DELETE purge `/v3/memory/*`. Storage/worker: yalnız REST server arkasında. Response/failure: response Pydantic model; HTTP/network/validation özel client exception. Tenant: request modelde agent/session. Doğrulama: statik.

## FLOW-MCP-001 — MCP request

Başlangıç: stdio MCP `call_tool`. Auth/config: import-time `MESA_AGENT_ID`, optional API key/base URL. Validation: MCP argument kontrolleri ve paylaşılan Pydantic request modelleri. Core: record/search/forget → `AsyncMesaClient` → REST API. Storage: bu üç tool dolaylı server üzerinden; `get_stats` doğrudan AsyncEngine/MemoryDAO/KuzuGraphProvider açar. Response/failure: `TextContent`, exception metni. Tenant: agent id environment’tan; `get_stats` graph count sorgusu agent predicate taşımıyor. Doğrulama: statik; Phase 5 isolation doğrulaması gerekli.

# Faz 3 — Kritik akış, hata ve izolasyon doğrulaması

Bu bölüm yalnız statik call-chain ve mevcut test kaynaklarından çıkarılmıştır. Yeni runtime, servis, Ollama, benchmark veya yük testi çalıştırılmadı.

## FLOW-ING-001 — Ingestion / cold path

| Alan | Kanıtlanmış akış |
|---|---|
| Başlangıç/auth | POST /v3/memory/insert → strict MemoryInsertRequest ve WRITE RBAC (mesa_api/router.py:191-242). |
| Kalıcı kabul | dao.insert_raw_log agent kapsamlı SQLite raw_logs INSERT+commit yapar; response 202 DEFERRED, agent_id, log_id'dir (dao.py:1638-1666; router.py:257-265). |
| İşletim | Aynı API process'indeki FastAPI BackgroundTasks process_cold_path çağrısını ekler. Worker DEFERRED → processing → processed/rejected/failed:<neden> yazar (router.py:248-255; ingestion_worker.py:140-279). |
| Retry/recovery | LLM çağrısı retry eder. Startup şeması eski processing kayıtlarını DEFERRED'e çevirir; server lifespan'ta bunları claim edip yeniden teslim eden raw_log consumer yoktur (schemas.py:68-83; server.py:145-329). |
| Triple-store | Triplet ise embedding → iki insert_memory → Kuzu edge; yoksa raw MEMORY node. Tier-3 hata verse de commit sonrası processed yazılır (ingestion_worker.py:229-278,760-931). |
| Tenant/test gap | Raw log get/update id AND agent_id ile kapsamlıdır. Worker testleri çoğunlukla mock DAO kullanır; 202 → process crash → restart → replay E2E yoktur. |

## FLOW-RET-001 — Retrieval

| Alan | Kanıtlanmış akış |
|---|---|
| Başlangıç/işleme | POST search → HybridRetriever READ RBAC → normalize/entity extraction → agent kapsamlı seed/cold-start → vector+graph+FTS gather → fusion/rerank/hydration (router.py:327-435; hybrid.py:47-227). |
| Failure | Vector/graph/FTS alt görev hataları loglanır ve fallback/boş sonuçla sürer; router 30 saniye timeout'ta 504 döndürür. |
| Tenant/tutarlılık | DAO vector, FTS, SQLite hydration ve Kuzu neighbor çağrıları agent kapsamlıdır. Hydration'da node yoksa router minimal phantom sonuç ekler (dao.py:690-824; router.py:381-393). |
| Test gap | test_rbac_leak DAO get/FTS/vector/neighbor negatif tenant testini içerir; endpoint RBAC policy ve graph-purge sonrası retrieval E2E yoktur. |

## FLOW-PURGE-001 — Purge / retention

| Alan | Kanıtlanmış akış |
|---|---|
| Başlangıç | DELETE purge strict agent/session scope ve WRITE RBAC uygular (router.py:450-490; schemas.py:293-347). |
| Mutasyon | SQLite hedef id seçimi → her id LanceDB soft-delete → SQLite nodes.invalid_at commit (dao.py:986-1061). |
| Hata/atomiklik | İlk vector hatası SQLite güncellemesini engeller; çok kayıtlı işlemde sonraki vector hatası öncesi soft-delete edilmiş vectorler için compensation yoktur. Tek global transaction yoktur. |
| Graph/retention | DAO purge Kuzu mutasyonu yapmaz. Maintenance sadece SQLite invalid node ve expired vectorleri fiziksel siler; graph provider almaz (maintenance.py:401-438,507-602). |
| Test gap | test_chaos ilk vector hata telafisini, test_dao_coverage session scope'u sınar; çok kayıtlı kısmi hata, Kuzu temizliği ve üç-store retention eşitliği test edilmez. |

## FLOW-SESSION-001 — Session yaşam döngüsü

Start yeni sess_<uuid> üretip WRITE grant verir. Context READ RBAC sonrasında yalnız raw_logs session payload'larından oluşturulur. End endpoint'i final consolidation'ı yalnız loglar; task/queue çağrısı yoktur (router.py:525-660). test_router_coverage yalnız HTTP durumlarını mock RBAC ile sınar.

## FLOW-SDK-MCP-001 — Dış sözleşme

SDK /v3/memory/insert, /search ve /purge yolunu sabit ekler ve paylaşılan response modellerini parse eder. Insert API log_id döndürdüğü için SDK'da async job handle kaybolur. Purge API purged/deleted_records_count döndürürken SDK PURGED/scope/scope_id/records_affected bekler. MCP default base URL'si http://localhost:8000/v3 olduğundan SDK ile çift /v3 oluşur; get_stats local storage açar ve graph edge count'u agent filtresizdir. Bu sözleşmeler için gerçek HTTP contract testi bulunamadı.

## Faz 4 invariant matrisi

| Invariant | Kod kanıtı | Test kanıtı | Sonuç | İlgili bulgu |
|---|---|---|---|---|
| Her memory agent’a ait olmalı | DAO mutation API’leri `agent_id` zorunlu ve validate eder | DAO/RBAC tenant testleri mevcut | Büyük ölçüde doğrulandı; API principal binding ayrı risk | SEC-002 |
| Agent’lar birbirinin verisini görememeli | DAO sorgularında agent predicate; session create caller-controlled | `test_rbac_leak.py` DAO seviyesini kapsar | Endpoint authz için ihlal riski doğrulandı | SEC-002 |
| Başarısız extraction graph’ı değiştirmemeli | Empty/partial extraction `mark_consolidated` yoluna gider | Partial bisection test yok | İhlal: bilgi kaybı/yanlış terminal state | LOGIC-002 |
| Partial write split-brain oluşturmamalı | Graph exception yutulurken vector/SQLite devam eder | Kuzu failure chaos testi yok | İhlal | DATA-002 |
| Tombstoned kayıt retrieval’da görünmemeli | DAO/vector normal filtreleri mevcut | Vector tombstone testleri var | Kısmen doğrulandı; çok-store purge ARCH/DATA-001 açık | DATA-001 |
| Session başka agent tarafından kullanılamamalı | Exact RBAC lookup vardır fakat session create agent identity’ye bağlı değil | Endpoint negative auth testi yok | İhlal riski | SEC-002 |
| Retry duplicate üretmemeli | Lance `merge_insert` hata halinde `add()` | Normal upsert testi var; fallback yok | İhlal riski doğrulandı | DATA-004 |
| Quarantined node retrieval’da dönmemeli | Alpha branch filtreli; cold/no-graph branch filtresiz | PageRank testleri var, branch contract yok | İhlal | LOGIC-003 |
| API success gerçek kabul durumuyla uyumlu olmalı | Partial extraction success/consolidated olabilir | Terminal state contract yok | İhlal | LOGIC-002 |
| Status state machine geçerli geçiş kullanmalı | status endpoint `__any__` grant bekler | start→status zinciri yok | Sorgulanabilirlik bozuk | LOGIC-001 |
| Failed kayıt kaybolmamalı | DLQ var fakat partial coverage kaybı var | Partial path yok | İhlal | LOGIC-002 |
| Shutdown state kaybetmemeli | Unawaited task/resource bulguları Faz 2’de | Runtime shutdown testi yok | Açık; Faz 7 | ARCH-002 |

## Faz 4 akış ekleri

| Akış | Yeni failure point | Davranış | Sonraki doğrulama |
|---|---|---|---|
| API auth → session | Global key → arbitrary `agent_id` | WRITE grant caller’s agent claim olmadan verilir | Faz 5 security integration |
| Insert → vector/graph/SQLite | Kuzu insert exception | Warning sonrası SQLite commit/success | Faz 6 chaos/compensation |
| Extraction → writer | Partial parse/bisection | Missing coverage boş sayılır; record consolidated olabilir | Faz 7/8 contract |
| Retrieval → cold fallback | Vector/FTS candidates | Quarantine data fetch/filter yok | Faz 5/8 component |
| Async SDK/MCP → API | Auth header | Server accepted header’dan farklı | Faz 8 HTTP contract |

## Faz 5 endpoint authentication ve authorization matrisi

| Endpoint / surface | Auth zorunlu mu | Uygulama noktası | Fail-closed / hata | Tenant/RBAC sonucu |
|---|---|---|---|---|
| `POST /v3/memory/insert` | Evet | Router-level `get_api_key`; route WRITE check | Missing/wrong key 401; permission 403 | Session scoped, fakat caller agent binding SEC-002 |
| `GET /v3/memory/status/{log_id}` | Evet | Router-level auth; `__any__` READ check | 401/403/404 | DAO log query agent scoped; grant modeli LOGIC-001 |
| `POST /v3/memory/search` | Evet | Router auth; `HybridRetriever` READ check | 401/403/504 | DAO/vector/graph scoped; cold quarantine LOGIC-003 |
| `DELETE /v3/memory/purge` | Evet | Router auth; WRITE check | 401/403 | Scope schema/DAO agent scoped; Kuzu purge DATA-001 |
| Session start/context/end | Evet | Router auth; session RBAC | 401/403 | Start client agent için grant verir: SEC-002 |
| `/health`, `/v3/health`, `/metrics` | Evet | Explicit dependency | 401 | Metrics protected; health reveals engine path only authenticated callera |
| `/health/init` | Hayır, bilinçli probe | Lifespan state | 503 or `{status: ready}` | Data değil, readiness discovery; deployment policy test yok |
| `/docs`, `/redoc`, `/openapi.json` | Hayır, FastAPI default | App default | N/A | Endpoint/schema discovery; veri/auth bypass kanıtı yok, hardening adayı |
| MCP `record/search/forget` | MCP process env agent + SDK | Async SDK → API | SDK header drift ile 401 | Caller tool arg agent’i override edemez; shared local process boundary |
| MCP `get_stats` | API auth zinciri yok | Direct SQLite/Kuzu | Internal exception text dönebilir | Kuzu edge count unscoped: ARCH-004 |

## Faz 5 tenant-scope matrisi

| Katman | Fonksiyon/sorgu sınıfı | agent_id zorunlu / filtre | Sonuç |
|---|---|---|---|
| SQL | nodes, raw_logs, FTS, purge, get-by-id, telemetry DAO yolları | Public tenant DAO yollarında `agent_id` parametreli predicate | Statik olarak kapsamlı; internal agent discovery/maintenance bilinçli global |
| LanceDB | DAO search/upsert/tombstone/get-existing | DAO agent filter verir; filter value allowlist ile doğrulanır | API path için doğrulandı; engine public optional-agent methods internal-only aday |
| KùzuDB | Node/edge insert, neighbors, salience, PageRank | Composite id + node/edge agent bindings | Normal DAO traversal isolated; MCP raw global edge count istisna |
| Worker | REM, PageRank, entity consolidation | Global agent discovery sonrası her agent için scoped DAO/query | İşleme scope’u korunuyor; global valence/router state istisna |
| SDK/MCP | SDK request model/MCP env agent | SDK routes agent taşıyor; MCP env agent sabit | Header drift; direct get_stats bypass |


## Faz 6 — Transaction, claim ve recovery sınırları

### FLOW-ING-006 — Raw-log claim → commit → terminal state

| Alan | Kanıtlanmış akış |
|---|---|
| Claim sınırı | `get_raw_log` ile `DEFERRED` okunur; sonra ayrı `update_raw_log_status(..., processing)` çağrılır. UPDATE `id AND agent_id` ile yapılır, önceki status/lease/rowcount guard yoktur. |
| Commit sınırı | Triplet yolu iki `insert_memory` ve `insert_edge` çağrısından oluşur. Edge/insert catch'i yalnız vector soft-delete denemesi yapabilir; SQLite/Kuzu telafisi yoktur. |
| Terminal state | `_commit_triplets` exception'ı yuttuğunda caller `processed` yazar; başarılı kabul sinyali gerçek üç-store commit ile eş değildir. |
| Recovery | `FLOW-001` restart replay açığı devam eder; CAS/lease ve idempotency olmadığından güvenli redelivery kanıtı yoktur. |
| İlgili bulgular | CONC-002, DATA-002, FLOW-001 |

### FLOW-MIG-006 — Alignment → WAL flush → normal yazı

| Alan | Kanıtlanmış akış |
|---|---|
| Lock | `system_config.lancedb_is_migrating` boolean değeridir; ownership/fencing yoktur. |
| Transform | Vector copy/transform/verify sırasında normal upsert/bulk-upsert lock'ı alınmaz; yalnız table promotion `_mutation_lock` içindedir. |
| WAL flush | SQL transaction içinde WAL select → external vector bulk-upsert → WAL delete/commit yapılır. Hata sonunda flag false olur; WAL satırlarını claim/replay eden normal consumer yoktur. |
| Failure/retry | Kısmi flush, crash ve concurrent second alignment için exact-once/repair testi yoktur. |
| İlgili bulgular | DATA-005, DATA-004 |

### FLOW-SHUTDOWN-006 — Task stop → state persistence → storage close

Lifespan REM/maintenance/PageRank/WAL için kısmi stop/cancel uygular; consolidation loop yalnız `_running=False` yapar. `consolidation_task`, Tier-3 ve DLQ task'leri await edilmeden valence state save, graph/SQLite close aşamasına geçilir; `VectorEngine.close()` çağrısı yoktur. Bu nedenle in-flight producer/consumer, executor work ve persistence snapshot sıralaması runtime ile doğrulanmış değildir (ARCH-002, CONC-003).


## Faz 7 — Worker/queue failure akışları

### FLOW-DLQ-007 — Dead-letter JSONL replay

`ConsolidationLoop` extraction/validation hatasında `{cmb_id,error}` JSONL item'ı append eder. DLQ worker dosyadan batch okur, tüm dosyayı clear eder, sonra item'ın agent_id'si olmadığı için default system agent ile DAO lookup yapar. Başarı ack'i, durable claim/lease, retry count, max attempt, timestamp, retention veya poison isolation yoktur. Bu akış `DLQ-001` kritik blocker'dır.

### FLOW-REM-007 — REM periodic consolidation

REM startup sonrası 15 s bekler; aktif agent listesi her 30 s poll'da DAO'dan alınır. `queue_depth < 50` ise skip, `>=50` ise created_at sırasına güvenerek en çok 100 kayıt işler. Per-record exception loglanır fakat durable retry/attempt/DLQ olmaz; LLM failure fail-safe no-contradiction olarak mark_consolidated'a ilerleyebilir. DAO agent parameter taşıdığı için normal cross-agent query kanıtı bulunmadı; multi-instance claim/locking yoktur.

### FLOW-PERIODIC-007 — Entity/PageRank/Maintenance/WAL

Entity worker 4 saatte bir agent-scoped entity açıklamasını LLM+embedding ile günceller; gerçek entity merge/edge taşıma yapmaz. PageRank 3600 s döngüde her agent altgraphını parameterized query ile alır, CPU math'i executor'a verir ve Kuzu quarantine yazar; score version/persistence/leader lock yoktur. Maintenance schedule idle-window ve WAL checkpoint 300 s PASSIVE olarak aynı API process'te çalışır; WAL checkpoint sonucu/size metric'i okunmaz. Ayrı LanceDB WAL replay worker yoktur.
## Faz 10 — Akış performans eki

| Aşama | Kaynak davranışı | Sınır / sorun | Kanıt | İlgili bulgu |
|---|---|---|---|---|
| Cold-start kararı | `get_memories(agent_id)` tüm aktif node’ları SQLite’dan alır, yalnız `len` kullanılır | Limitsiz O(N) okuma ve request RAM’i | `HybridRetriever.retrieve`; `MemoryDAO.get_memories` | PERF-002 |
| Candidate retrieval | Vector 100, FTS 100, graph seed başına 15; `gather` ile paralel | Candidate havuzu bounded, fakat subquery sayısı decomposition’a bağlıdır; canlı maliyet ölçülmedi | `hybrid.py` | Faz 10 statik kanıt |
| Response hydration | Her final `cmb_id` için tek DAO read | Schema limiti 50 ile bounded N+1 | `mesa_api/router.py:search_memory`; `MemorySearchRequest.limit` | PERF-004 |
| Entity/REM/PageRank | Entity tüm node’larda ardışık LLM/embed/update; REM tüm backlog’u okuyup 100 işler; PageRank tüm graphı materialize eder | Bounded claim/budget/lag SLO yok | İlgili üç worker sembolü | PERF-003 |

## Kanonik akış kanıt indeksi (2026-07-19)

Statik akış haritası runtime doğrulaması değildir. Aşağıdaki indeks, mevcut akış kayıtlarının kanıt sınıfını tek biçimde belirtir.

| Akış | Kanıt seviyesi | Kanıt türü |
|---|---|---|
| FLOW-START-001 | E1 | Static call/import flow |
| FLOW-ING-001 | E1 | Static call/import flow |
| FLOW-RET-001 | E1 | Static call/import flow |
| FLOW-PURGE-001 | E1 | Static call/import flow |
| FLOW-SESSION-001 | E1 | Static call/import flow |
| FLOW-SDK-MCP-001 | E1 | Static call/import flow |
| FLOW-SDK-001 | E1 | Static call/import flow |
| FLOW-MCP-001 | E1 | Static call/import flow |
| FLOW-ING-006 | E1 | Static call/import flow |
| FLOW-MIG-006 | E1 | Static call/import flow |
| FLOW-SHUTDOWN-006 | E1 | Static call/import flow |
| FLOW-DLQ-007 | E1 | Static call/import flow |
| FLOW-REM-007 | E1 | Static call/import flow |
| FLOW-PERIODIC-007 | E1 | Static call/import flow |
| Faz 10 performans akış eki | E1 | Static call/import flow |

Kanıt efsanesi: E1 = Static call/import flow; E2 = unit/component; E3 = integration/runtime; E4 = staging/rehearsal.
