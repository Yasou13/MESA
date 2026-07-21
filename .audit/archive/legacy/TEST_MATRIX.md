# Test Matrisi

Gerçek test komutları Faz 1 ve Faz 8’de repository yapılandırmalarından çıkarılır; komut tahmini yapılmaz.

| Gereksinim veya risk | Test seviyesi | Test dosyası | Test adı | Başarı senaryosu | Hata senaryosu | Durum | Sonuç | İlgili bulgu |
|---|---|---|---|---|---|---|---|---|
| Faz 1 sınıflandırması | — | — | — | — | — | Aşağıdaki kayıtlar yetkilidir | — | — |


## Faz 1 test sınıflandırması

| Gereksinim veya risk | Test seviyesi | Test dosyası | Test adı | Başarı senaryosu | Hata senaryosu | Durum | Sonuç | İlgili bulgu |
|---|---|---|---|---|---|---|---|---|
| Config/schema/mock adapter sözleşmeleri | Unit/component | tests/test_config.py, test_config_edge_cases.py, test_api_schemas.py, test_adapter_factory.py | 70 seçili test | Fonksiyonel test: sentetik env/mock provider ile seçili davranışlar | Pydantic/adapter hata yolları | Functional pass | 70 geçti; isolation: Failed / Not verified due to SEC-001; 4,94 sn | — |
| Güvenli test collection | Collection | Aynı dört dosya | pytest --collect-only | Testlerin importsuz/servissiz toplanması | Collection hatası | Otomatik çalıştırıldı | 70 toplandı; 1,10 sn | — |
| Kısmi coverage | Unit/component | config, schemas, adapter factory | pytest-cov seçili alt küme | Hedef modüllerin satır kapsamı | Eksik coverage | Functional pass | Hedef modüller %95; production release coverage değildir | — |
| Ollama adapter canlı yolları | Integration | tests/test_adapters.py, go_live_proofs/mock_ollama.py | OllamaAdapter testleri | Yerel Ollama ile adapter davranışı | Servis/model yok | Manuel test gerekli; Ollama gerekli | Çalıştırılmadı | — |
| Canlı OpenAI-compatible adapter | Integration | tests/test_adapter_live.py | Live adapter testleri | Sağlayıcı bağlantısı | Ücretli/ağ riski | Güvenlik nedeniyle çalıştırılmadı; Ücretli dış servis riski | Çalıştırılmadı | — |
| Mem0/Qdrant | Integration/benchmark | tests/test_mem0.py | Mem0 testi | Harici adapter/storage | API/servis riski | Manuel test gerekli | Çalıştırılmadı | — |
| Kuzu concurrency/performance | Performance | tests/test_kuzu_performance.py | gather tabanlı testler | 50+ concurrent query | RAM/CPU yoğunluk | Kaynak yoğun; Daha sonraki performans fazına ertelendi | Çalıştırılmadı | — |
| Async benchmark | Benchmark | tests/bench_async_io.py | run_benchmark | Async IO ölçümü | Yüksek concurrency | Kaynak yoğun; ileri faza ertelendi | Çalıştırılmadı | — |
| Locust load | Load | tests/bench/locustfile.py | Locust senaryosu | HTTP yük testi | API/RAM yükü | Kaynak yoğun; ileri faza ertelendi | Çalıştırılmadı | — |
| Benchmark subproject | Benchmark | mesa-benchmark/tests/ | 5 test dosyası | Dataset/client/evaluator pipeline | Dataset/servis kaynakları | Daha sonraki performans fazına ertelendi | Çalıştırılmadı | — |
| Soak/load/eval CLI | Soak/load | mesa_evals/soak_test.py, mesa_evals/load_test.py | CLI harness | Uzun süreli kalite ölçümü | Ollama/servis/RAM riski | Manuel test gerekli; Kaynak yoğun | Çalıştırılmadı | — |
| API health/SDK/worker/restart | E2E smoke | API server, scripts/health_check.py | Startup sonrası smoke | Ready, health, local smoke | API ready değil | Güvenli ancak başarısız | BOOT-001 | BOOT-001 |
| Faz 1 baseline güvenlik ve izolasyon kanıtı | Audit/prosedür | Faz 1 command log, manifestler, config import zinciri | Salt-okunur doğrulama | Gerçek `.env` olmadan, onaylı core manifestiyle, izole storage’da baseline | Dotenv auto-load, eksik manifest, disk/artefakt kanıtı | Yeni test çalıştırılmadı | SEC-001 ve OPS-001 nedeniyle geçmedi; OPS-002 izlenebilirlik riskidir | SEC-001, OPS-001, OPS-002 |

## Faz 3 kritik akış test eşlemesi

| Gereksinim veya risk | Test seviyesi | Test dosyası | Başarı/hata senaryosu | Durum ve gap | İlgili bulgu |
|---|---|---|---|---|---|
| Cold path stage geçişleri | Unit/mock | tests/test_ingestion_worker.py, tests/test_storage_unification.py | DEFERRED→processing→processed; ECOD, DB/status, Tier-3 hata | Kaynak statik incelendi; DAO/LLM mock. API crash/restart/replay kanıtı yok | FLOW-001 |
| Cold path LLM tolerance | Unit/mock | tests/test_fault_tolerance.py | Retry ve circuit breaker | Kalıcı queue teslimatı/worker restart testi değil | FLOW-001 |
| Retrieval tenant izolasyonu | Component | tests/test_rbac_leak.py | Agent-B get/FTS/vector/neighbor negatif testi | DAO seviyesinde güçlü; endpoint RBAC ve graph-purge E2E yok | DATA-001 |
| Retrieval endpoint failure | Router/unit | tests/test_router_coverage.py | Permission 403, genel hata 500, timeout 504 | Retriever mock; gerçek üç-store arıza kanıtı yok | — |
| Purge ilk vector hata telafisi | Component/chaos | tests/test_chaos.py | İlk soft-delete exception sonrası SQLite aktif | Çok-node kısmi hata, Kuzu mutation/retention eşitliği yok | DATA-001 |
| Session finalization | Router/unit | tests/test_router_coverage.py | 200 ended, RBAC/error mapping | Enqueue/işleme assertion yok; kodda enqueue yok | FLOW-002 |
| SDK/MCP URL ve response contract | Contract/E2E | — | Insert handle, purge response, varsayılan MCP URL | Test yok; statik uyumsuzluk doğrulandı | SDK-001, SDK-002 |

## Faz 4 modül-test eşlemesi

| Modül | Davranış | Test dosyası | Happy path | Failure/negative path | Gerçek bağımlılık | Yeterli mi |
|---|---|---|---|---|---|---|
| Storage/DAO | Insert, FTS, tenant scope | `test_dao_coverage.py`, `test_chaos.py` | SQLite/vector fixture yolları | Vector hata kısmen | Kuzu failure/triple-store rollback yok | Hayır — DATA-002 |
| Vector | Upsert/search/tombstone | `test_vector_engine.py`, `test_vector_engine_coverage.py` | Normal merge upsert/search | Bazı concurrent search | Merge fallback duplicate/provider error yok | Hayır — DATA-003, DATA-004 |
| API/RBAC | Router permission/status | `test_router_coverage.py`, `test_rbac.py` | Bazı 403/policy kombinasyonları | Permission error | Principal→agent binding, start→status chain yok | Hayır — SEC-002, LOGIC-001 |
| Extraction/consolidation | Batch/mock consensus | `test_consolidation.py`, `test_tier3_resilience.py`, `test_p0a_batch.py` | Mock happy path/retry | Bazı exception/DLQ | Partial parser/bisection coverage kaybı yok | Hayır — LOGIC-002 |
| Retrieval | Cold start/rerank | `test_retrieval.py`, `test_retrieval_edge_cases.py`, `test_p0c_loop.py` | Vector fallback/ordering | Empty results | Quarantine + cold/no-graph yok | Hayır — LOGIC-003 |
| SDK/MCP | HTTP client contract | — | — | — | Sync/async auth ve MCP gerçek contract yok | Hayır — SDK-001..003 |
| Observability | Metrics labels | — | — | — | Parametreli path cardinality testi yok | Hayır — PERF-001 |

## Faz 4 kritik davranış test boşlukları

| Risk | Önerilen seviye | Senaryo | Durum | İlgili bulgu |
|---|---|---|---|---|
| Principal-agent izolasyonu | API security integration | Ayrı principal başka agent için session açamaz | Çalıştırılmadı; Faz 5/8 | SEC-002 |
| Graph write atomikliği | Component/chaos | Kuzu error sonrası response ve üç store görünürlüğü | Çalıştırılmadı; Faz 6/8 | DATA-002 |
| Partial extraction teslimatı | Unit/component | Malformed partial batch sessiz consolidated olmaz | Çalıştırılmadı; Faz 7/8 | LOGIC-002 |
| Quarantine policy | Retrieval component | Cold/no-graph candidate quarantined id dönmez | Çalıştırılmadı; Faz 5/8 | LOGIC-003 |
| SDK/MCP contract | API contract | Sync, async, MCP ortak URL/header/schema | Çalıştırılmadı; Faz 8 | SDK-001..003 |
| Credential persistence | Storage security | Daily limits raw credential tutmaz | Çalıştırılmadı; Faz 5/11 | SEC-003 |

## Faz 5 güvenlik test matrisi

| Risk | Test dosyası / adı | Negative test | Integration test | Yeterli mi | İlgili bulgu |
|---|---|---|---|---|---|
| Missing/invalid API key | `tests/test_p0b_missing.py:test_server_lifespan_health_metrics` | `/health`, `/metrics`, yanlış key 401 | Mock lifespan; gerçek startup değil | Kısmen | — |
| Empty agent/session ve input formatı | `tests/test_api_schemas.py` request model testleri | Empty, whitespace, sentinel, identifier formatı | HTTP body/parser değil | Kısmen | INPUT-001 |
| Cross-agent read | `tests/test_rbac_leak.py` | SQL, FTS, vector, neighbor negative assertions | Gerçek local storage fixture | Kısmen güçlü; endpoint/principal yok | SEC-002, ARCH-004 |
| Kùzu tenant filter | `tests/test_kuzu_isolation.py:test_rogue_edge_does_not_leak_via_traversal` | Rogue cross-tenant edge traversal negative | Local Kùzu fixture | Kısmen; MCP raw query yok | ARCH-004 |
| Cross-agent update/purge/status/session | `test_dao_coverage.py`, `test_router_coverage.py` ayrı happy/error yolları | End-to-end principal/agent negative yok | Yok | Eksik | SEC-002, LOGIC-001 |
| LanceDB tenant filter | `tests/test_rbac_leak.py`, vector testleri | Agent-B vector search negative | Local fixture | Kısmen | — |
| Bulk input/payload >1 MB | — | Metadata list/depth/total-byte negative yok | Yok | Eksik | INPUT-001 |
| Sanitization application path | `tests/test_rbac.py:test_sanitize_cmb_content` | Fonksiyon-level only | Insert/bulk/MCP path yok | Eksik | ARCH-003 |
| MCP agent isolation | `tests/go_live_proofs/verify_r10_mcp_spoofing.py` | Tool argument agent spoofing | Mock client; normal pytest/CI gate değil | Kısmen | ARCH-004, SDK-003 |
| SDK secret/error redaction | — | Header/error redaction yok | Yok | Eksik | SDK-003 |
| Error/log raw content redaction | — | Raw log/query debug output negative yok | Yok | Eksik | ARCH-003 |
| CORS/docs/metrics exposure | `test_p0b_missing.py` metrics auth only | CORS/docs/health-init policy yok | Mock lifespan | Eksik | — |
| Secret/dependency scan | CI TruffleHog gate; Dependabot config | CI result artefactı auditte yok | Çalıştırılmadı | Kısmen | CI-001 |
| Tenant adaptive state | — | Tenant A embedding/telemetry B state’ini değiştirmez | Yok | Eksik | RLS-001 |


## Faz 6 veri bütünlüğü ve concurrency test matrisi

| Risk | Önerilen seviye | Senaryo | Mevcut kanıt | Durum | İlgili bulgu |
|---|---|---|---|---|---|
| Alignment in-flight write | Component/controlled concurrency | Insert flag read ile promotion arasına barrier ile yerleşir; node kaybolmaz | `test_dao_coverage.py` yalnız bool dönüşü kontrol eder | Çalıştırılmadı; kritik gap | DATA-005 |
| WAL flush/restart | Component/chaos | Bulk-upsert partial fail, crash before/after ack, startup replay | WAL flush failure/replay testi yok | Çalıştırılmadı; kritik gap | DATA-005, DATA-004 |
| Raw-log single claim | Component/controlled concurrency | Aynı `log_id` için iki worker, yalnız biri side-effect üretir | Worker testleri mock/happy-path ağırlıklı | Çalıştırılmadı; kritik gap | CONC-002, FLOW-001 |
| Terminal state integrity | Component/chaos | Kuzu edge/SQLite commit hatası processed yerine retryable state üretir | `test_chaos.py` ilk vector hata yolunu sınar; worker edge hata zinciri yok | Çalıştırılmadı; kritik gap | CONC-002, DATA-002 |
| Purge/maintenance coordination | Component/controlled concurrency | Çok kayıtlı purge, vector hard-delete/compact ve Kuzu retention eşitliği | Tek ilk vector hata testi; graph ve maintenance race yok | Çalıştırılmadı | DATA-001 |
| Valence deterministic state | Unit/controlled concurrency | Aynı shared motorla barrier admission/recalibration | Tekil evaluate testleri var; concurrent/per-agent/save-race yok | Çalıştırılmadı | CONC-003, RLS-001 |
| Shutdown drain | Lifecycle integration | Worker task cancel/drain, valence save, vector/SQLite close sırası | Static lifecycle okuması; runtime shutdown yok | Faz 1.5 kapısı nedeniyle çalıştırılmadı | ARCH-002 |

Faz 1.5 güvenlik/izolasyon kapısı (`SEC-001`, `OPS-001`) açık kaldığı için bu matristeki hiçbir dinamik, concurrency, restart veya fault-injection testi Faz 6'da çalıştırılmadı. Yük/benchmark/stress/soak ve Ollama/REBEL işlemleri de özellikle çalıştırılmadı.


## Faz 7 worker/queue test eşlemesi

| Worker/risk | Mevcut test | Gerçek queue/storage | Failure path | Durum |
|---|---|---|---|---|
| Ingestion happy path | `test_ingestion_worker.py` | Mock ağırlıklı | Kısmi status/ECOD | Mevcut fakat yetersiz |
| Duplicate claim / stale processing / crash recovery | — | — | CAS/lease/restart | Eksik — CONC-002, FLOW-001 |
| BackgroundTasks durability | Router testleri | Gerçek process yok | 202→crash/restart | Eksik |
| Consolidation partial batch | `test_consolidation.py`, `test_p0a_batch.py` | Mock | Partial parser/DLQ outcome | Mevcut fakat yetersiz — LOGIC-002 |
| Tier-3 deferred terminal state | — | — | `run_batch` failure→mark consolidated | Eksik |
| DLQ routing/replay | Bazı run_batch mock yolları | JSONL gerçek crash/tenant yok | clear-before-ack, retry, poison | Eksik — DLQ-001 |
| REM threshold/budget/lifecycle | `test_rem_cycle.py` | Mock DAO/LLM | Threshold, per-record exception | Mevcut fakat yetersiz; multi-instance/idempotency yok |
| Entity consolidation | `test_entity_consolidation_worker.py` | Mock DAO/LLM | Single-entity LLM failure | Mevcut fakat yetersiz; storage saga/overlap yok |
| Maintenance/VACUUM | `test_maintenance_worker.py` | SQLite fixture ve mocks | Bazı error paths | Mevcut fakat yetersiz; live mutation/graph/multi-instance yok |
| WAL checkpoint | — | — | busy/disk/locked/final checkpoint | Eksik |
| PageRank tenant isolation | `test_pagerank_coverage.py` | Component/mock | Per-agent scope kısmi | Mevcut fakat yetersiz; concurrent mutation/schedule yok |
| WAL replay partial success | — | — | claim/retry/restart | Eksik — DATA-005 |
| Worker shutdown / health | Worker lifecycle unit testleri kısmi | Real lifespan yok | task death/readiness/drain | Eksik — ARCH-002, WORKER-001 |
| Queue backlog/backpressure | — | — | quota/disk/alert | Eksik — QUEUE-001 |

Faz 1.5 `SEC-001` ve `OPS-001` açık kaldığı için Faz 7'de worker testi, collection, runtime task, sentetik storage veya dinamik doğrulama çalıştırılmadı.


## Faz 8 kapsam ve minimum production test kapısı

| Alan | Mevcut kanıt | Eksik kritik kanıt | Durum |
|---|---|---|---|
| API/ingestion | Schema/router happy/error, BackgroundTask TestClient | Principal binding, durable 202→crash/restart, payload global byte limit | Yetersiz |
| Storage/integrity | DAO/vector/Kuzu fixtures, first vector chaos rollback | Kuzu/SQLite commit failure, partial multi-store, exact-once repair | Yetersiz |
| Tenant/security | DAO retrieval negative `test_rbac_leak` | Cross-agent endpoint write/purge/status/session, MCP direct stats, SDK auth | Yetersiz |
| Worker/queue | REM/entity/maintenance mock/unit coverage | Claim, stale recovery, DLQ ack/replay, backlog, worker health | Yetersiz |
| Migration | `initialize_schema` idempotency ve alignment bool calls | Old schema, interrupt/rollback, WAL replay/partial flush | Eksik |
| SDK/MCP | Go-live mock spoofing script | Sync/async/API/MCP contract | Eksik |
| Lifecycle | Health auth/unit, storage health | Worker death, partial startup, shutdown/restart/executor close | Eksik |
| CI/coverage | pytest+85%, RBAC/chaos, graph audit, docker build | SDK coverage/contract, worker/DLQ/WAL, production-entry smoke determinism | Yetersiz |

Kaynak maliyeti: core unit/schema/ASGI mock hafif; SQLite/Lance/Kuzu component orta; worker crash/fault orta ve izole storage gerektirir; Ollama/REBEL/CrossEncoder, `mesa_evals` load/soak ve benchmark LLM judge manuel/kaynak yoğun olup 16 GB RAM/Iris GPU için bu fazda çalıştırılmadı.


## Faz 9 ek regresyon kanıtı

| Test ID | Bulgu | Seviye | Dosya/komut | Bağımlılık | Failure path | Sonuç | CI uygunluğu |
|---|---|---|---|---|---|---|---|
| STATIC-DLQ-001 | DLQ-001 | Static invariant | `.audit/runtime/faz9/` source assertion + `python -m py_compile mesa_memory/consolidation/loop.py` | Uygulama importu yok | destructive clear / tenant context / selected ack | Çalıştırıldığı raporlandı; artefakt mevcut değil, runtime verified değil | Normal pytest regression gerekli |
## Faz 10 ertelenmiş güvenli performans doğrulama planı

| Risk | Ölçüm seviyesi | İzolasyon ve kaynak sınırı | Ölçümler / kabul eşiği | Durum | İlgili bulgu |
|---|---|---|---|---|---|
| Cold-start search tam tarama | Component | Sentetik `/tmp` storage, gerçek `.env`/LLM yok; tek process, düşük concurrency | SQL satır/sorgu sayısı, p50/p95, peak RSS; tenant büyüdükçe count kararının O(1) kalması | Faz 1.5 gate açık olduğu için çalıştırılmadı | PERF-002 |
| Entity/REM/PageRank backlog | Component/lifecycle | Küçük kademeli sentetik tenant, mock adapter; tek worker, timeout/disk bütçesi | Batch başına kayıt/token/süre, queue lag, CPU/RSS, cancellation sonrası tekrar işleme | Çalıştırılmadı | PERF-003, WORKER-001, QUEUE-001 |
| Search hydration N+1 | Component | SQLite temp storage, LLM/vector/graph mock; 1/10/50 sonuç | DAO query count, p50/p95, connection semaphore wait | Çalıştırılmadı | PERF-004 |
| SQLite WAL/VACUUM ve Lance compaction | Ops/component | Ayrı disposable storage, idle-window ve tek process; production volume yok | WAL boyutu, lock wait, compact/vacuum süresi, disk headroom | Faz 1.5 ve topology gate’i nedeniyle çalıştırılmadı | DATA-001, CONC-CAND-002 |
| Çok process worker çoğalması | Staging topology | Ayrı staging, iki kontrollü process; LLM/mock ve disposable storage | Leader sayısı, duplicate periyodik tur, lag, executor/thread sayısı | Çalıştırılmadı | ARCH-001, WORKER-CAND-001, PERF-003 |

## Faz 13 — Static-only rehearsal sonucu

| Test / rehearsal alanı | Durum | Gerekçe / Faz 14 etkisi |
|---|---|---|
| Staging entry gate ve yöntem seçimi | Static-only | SEC-002, DATA-005, DLQ-001, dotenv ve runtime blocker’ları nedeniyle `BLOCKED` / `STATIC_PLAN_ONLY`. |
| Docker/artifact doğrulaması | Static-only | Docker kurulu değil; build/Compose yok; wheel/sdist yok; kaynak sürümü `0.6.1`; runtime/artifact testi yapılmadı. |
| API/worker startup-shutdown | Blocked | `.env` import etkisi ve automatic worker start. |
| Health/readiness, auth, tenant, insert/search/status, session/purge | Blocked | API başlatılmadı; `/health/init` worker sağlığını dikkate almıyor. |
| Persistence/restart, migration, backup/restore, rollback | Blocked | Açık P0 veri dayanıklılığı ve doğrulanmış mekanizma/artifact yok. |
| Process/container/volume/port kalıntısı | Not applicable | Dinamik rehearsal yapılmadı; kalıntı oluşmadı. |

## Faz 13.5 audit bütünlüğü kontrol matrisi (tarihsel, Faz 11/12 persistence öncesi)

| Kontrol | Durum | Kanıt / sonuç |
|---|---|---|
| 16 audit dosyası varlık/okunabilirlik | Passed | Tümü mevcut, okunabilir ve boş değil. |
| Faz 0–10 kayıt izi | Passed with documented gaps | Faz kayıtları mevcut; CHANGELOG ayrıntısı eksik, bazı kanıtlar statik. |
| Faz 11 kayıt izi | Failed | Faz bölümü, komut ve tamamlama kaydı yok. |
| Faz 12 kayıt izi | Failed | Faz bölümü, komut ve tamamlama kaydı yok. |
| Faz 13 persistence | Passed | `STATIC_PLAN_ONLY`, `BLOCKED`, `Kısmen tamamlandı` ve recovery kaydı mevcut. |
| Faz 9 remediation verification | Failed / partial | Kod diff’i mevcut; source-invariant artefaktı yok, runtime/pytest yok. |
| Critical code cross-check | Passed (static) | SEC-002, DATA-005, remaining DLQ risk, STAGE-001, CONFIG-002 ve worker-unaware health kodla uyumlu. |
| P0/P1 canonical count | Failed | Historical minimum — superseded/non-canonical: Bilinen 5 P0/30 P1; Faz 11/12 eksik olduğundan kapsamlı toplam değil. |
| Faz 14 entry gate | Failed | `NOT_READY_FOR_PHASE_14`. |


## Faz 11–12 formal test matrisi ekleri (2026-07-19)

| Faz | Risk / bulgular | Gerekli test | Sonuç | Neden çalıştırılmadı |
|---:|---|---|---|---|
| 11 | MIG-001, MIG-004 | Prior-version SQLite fixture, schema fingerprint, tenant backfill dry-run/rollback | Not tested / Blocked | Bu görev migration çalıştırmayı yasaklar; runtime kapısı kapalı |
| 11 | MIG-002, MIG-003 | Kùzu lock/idempotency, interruption-resume, duplicate/reconcile | Not tested / Blocked | Yeni analiz veya migration çalıştırılmadı |
| 11 | BACKUP-001, RESTORE-001, TEST-002 | Isolated backup manifest, full restore, Lance/SQLite/Kùzu/WAL reconcile | Not tested / Blocked | Backup/restore bu görev kapsamı dışı |
| 12 | DOCKER-001..003 | Docker build, image inspect, Compose persistence/restart | Not tested / Blocked | Local Docker kurulu değil; build/Compose çalıştırılmadı |
| 12 | CONFIG-001, HEALTH-001 | Negative config, worker fault/lag health/readiness | Not tested / Blocked | API/worker başlatma yasak |
| 12 | CI-002, RELEASE-001 | Clean wheel/sdist install, artifact canary, staged rollback | Not tested / Evidence missing | Artifact bulunmadı; CI/release çalıştırılmadı |

`Passed` sonucu üretilmemiştir. Bu matris statik kanıtla gerekli gelecekteki testleri ayırır.


## Faz 13.5 revalidation matrisi (2026-07-19)

Önceki Faz 13.5 kontrol matrisi, Faz 11/12 persistence öncesindeki tarihsel sonucu saklar; aşağıdaki satırlar güncel canonical sonuçtur.

| Kontrol | Güncel durum | Kanıt |
|---|---|---|
| 16 audit dosyası varlık/okunabilirlik | Passed | Tümü mevcut, okunabilir, boş değil; yapısal bozulma yok |
| Faz 0–13 kayıt izi | Passed with documented gaps | Faz 11/12 formal static-only kayıtları persisted; Faz 9 runtime kanıtı ayrı açık |
| Faz 11 / Faz 12 formal durum | Passed as static-only record | Runtime sonucu değil: `Blocked` / `Not tested` sınıfları korunuyor |
| Faz 13 persistence | Passed | `STATIC_PLAN_ONLY`, `BLOCKED`, Kısmen tamamlandı ve recovery kaydı mevcut |
| Faz 9 remediation verification | Failed / partial | Diff + static invariant var; pytest/runtime/integration yok |
| P0/P1 canonical count | Passed | 9 P0, 40 P1; P0’ların tümü blocker listesinde |
| Critical code cross-check | Passed (static) | SEC-002, DATA-005, DLQ-001 kalan riski, STAGE-001, CONFIG-002, dotenv, worker startup ve readiness kaynakla uyumlu |
| Faz 14 audit giriş kriteri | `READY_FOR_PHASE_14_WITH_DOCUMENTED_GAPS` | Kayıtlar tam, sayımlar güvenilir, açık kanıt boşluğu açıkça sınıflandırılmış |

Bu matristeki `Passed`, yalnız audit-kayıt veya static cross-check sonucudur; çalıştırılmamış Docker, API, worker, test, migration, backup veya restore işlemi için runtime başarı iddiası değildir.


## Faz 14 — NO_GO yeniden değerlendirme test kapısı (2026-07-19)

| Gate | Zorunlu sonuç | Mevcut durum |
|---|---|---|
| Auth/tenant/session | İki principal ile cross-agent write/read/status/session/purge negatif testleri geçer | Blocked / Not tested |
| Triple-store integrity | Graph/vector/SQLite failure ve compensation assertions geçer | Blocked / Not tested |
| Queue/DLQ | Multi-process claim, crash-before/after-ack, poison/legacy tests geçer | Partially fixed; Not verified |
| Migration | Prior-version, tenant backfill, lock/idempotency/resume/rollback geçer | Blocked / Not tested |
| Backup/restore | Manifest/checksum backup, isolated restore ve full reconciliation geçer | Blocked / Not tested |
| Docker/runtime | Correct volume ile build/startup/worker/health/shutdown/restart geçer | Blocked / Not tested |
| Artifact/CI | Wheel/sdist/image install-smoke, checksum/provenance ve critical gates geçer | Evidence missing |
| Staging | Dinamik rehearsal kritik smoke’larla geçer | `STATIC_PLAN_ONLY` |

## Faz 1 functional test / isolation ayrımı (2026-07-19)

| Alan | Kanonik sonuç |
|---|---|
| Functional test sonucu | 70 seçili config/schema/mock-adapter testi geçti |
| Isolation durumu | Failed / Not verified due to SEC-001 |
| Coverage yorumu | Hedef modüller %95; production release coverage değildir |
| Production sonucu | API/worker/runtime isolation veya release readiness kanıtı değildir |

## WAVE-001 clean restart authorization regression (2026-07-19)

| Risk | Test seviyesi | Komut / kapsam | Sonuç | Kalan gap | İlgili bulgu |
|---|---|---|---|---|---|
| Unmapped principal session self-grant | E2 component | `tests/test_principal_authorization.py` | 5 passed: unmapped=403, mapped=200, inactive=401, READ-only create reddi | E3 iki-principal HTTP/runtime yok | SEC-002 |
| RBAC/router/session compatibility | E2 unit/component | `test_principal_authorization.py`, `test_rbac.py`, `test_router_coverage.py`, `test_session_lifecycle.py` | 33 passed, 1 deprecation warning | SDK/MCP `/session/start`, status/purge/session-owner contract yok | SEC-002, LOGIC-001, SDK-003 |
| Runtime config isolation | Not tested / Blocked | API server import/lifecycle çalıştırılmadı | `.env` isolation ve runtime profile kapısı açık | E3 verilemez | SEC-001, CONFIG-002, BOOT-001 |

## WAVE-002 controlled remediation

| Risk | Test seviyesi | Test dosyası | Senaryo | Sonuç | Kanıt / kalan gap | İlgili bulgu |
|---|---|---|---|---|---|---|
| Graph write split-brain | Deterministic component fault injection | `tests/test_triple_store_mutation_contract.py` | Kuzu `insert_node` hata → exception, yeni vector telafisi, SQLite transaction yok | Önce fail; sonra geçti | E2; gerçek Kuzu/SQLite/Lance/restart yok | DATA-002 |
| Merge fallback duplicate | Deterministic component fault injection | aynı | Tekli ve bulk `merge_insert` hata → `add()` çağrılmaz, hata yükselir | Önce fail; sonra geçti | E2; gerçek Lance/WAL replay yok | DATA-004 |
| Purge graph lifecycle | Design/integration | — | Agent/session purge sonrası üç store retention eşitliği | Çalıştırılmadı | Kuzu soft-delete/restore contract yok; DATA-001 açık | DATA-001 |

## WAVE-002 DATA-001 approved lifecycle

| Risk | Seviye | Test | Sonuç | Kalan gap |
|---|---|---|---|---|
| Purge state machine / tombstone | E2 component + synthetic SQLite migration | `test_purge_journal_contract.py` | Passed; migration head twice, exact scope and `FINALIZED` | Real Kuzu/Lance E3 yok |
| Kuzu failure ordering | E2 deterministic fake | aynı | Passed; vector çağrılmadı, tombstone `RETRY_PENDING` | Real graph engine fault yok |
| Vector failure + crash recovery | E2 deterministic fake | aynı | Passed; recovery yalnız vector adımını tamamladı | Process restart / durable scheduler yok |
| Principal cross-tenant purge / partial API result | Router-unit E2 | aynı | 403/503 passed | Gerçek authenticated HTTP E3 yok |
| Restore/backup ledger reconciliation | — | — | Sadece pre-downstream rollback sınırı E2 | Operator-controlled backup/restore zorunlu ve çalıştırılmadı |

## WAVE-003 controlled remediation

| Risk | Seviye | Test | Sonuç | Kalan gap |
|---|---|---|---|---|
| Raw-log double claim / fenced terminal | E2 synthetic SQLite controlled concurrency | `test_wal_claim_replay_contract.py` | Pre 2 fail; post 2 pass | Real worker side-effect and Kuzu error path yok |
| Lease expiry / replay | E2 synthetic SQLite | aynı | Expired raw-log re-claim ve WAL single claim/ACK geçti | Process crash/restart E3 yok |
| WAL delete-before-ack | E1/E2 source + synthetic contract | DAO claim/replay/ack | Per-row ACK uygulandı | Real Lance partial bulk failure yok |
| Alignment mutation interval | E1 source invariant | VectorEngine complete `_mutation_lock` | Uygulandı | In-flight insert/promotion component barrier testi yok |
| WAVE-002 regression | E2 | purge + triple-store suites | 10 passed | Real-store E3 yok |
| General DAO regression | E2 | `test_dao.py` | 22 passed / 13 existing WAVE-002 graph fail-closed mock-fixture failure | Fixture repair WAVE-003 dışı |

## WAVE-004 controlled remediation

| Risk | Kanıt | Sonuç | Gap |
|---|---|---|---|
| DLQ exclusive claim/lease/ACK/poison | E2 `test_durable_dlq_contract.py` | Passed | E3 process recovery yok |
| Worker trace isolation | E2 trace + worker suites | 52 passed; protected hash unchanged | Production default CWD trace behavior korunur |
| DAO fixture contract | E2 `test_dao.py` | 33 passed | Product behavior değil harness alignment |
| Raw-log replay/admission/readiness | — | Not implemented | FLOW-001/QUEUE-001/WORKER-001 açık |

## WAVE-004A durable dispatch

| Scenario | Result |
|---|---|
| Idempotent dispatch + receipt | E2 passed |
| Recovery of receipt-less DEFERRED raw log | E2 passed |
| E3 crash/restart consumer | Not tested; W4D |

## WAVE-004B admission/backpressure

| Risk | Seviye | Kanıt | Sonuç / gap |
|---|---|---|---|
| Count/byte/tenant/in-flight/retry admission | E2 | `tests/test_queue_admission_contract.py` | 9 passed; queue rejection durable kayıt üretmez |
| HTTP overload mapping | Router-unit E2 | aynı | 503 + bounded Retry-After, 413 ve BLOCKED mapping geçti |
| Isolated durable restart accounting | E3 component | `/storage/mesa-lab/.../e3-component-rehearsal.txt` | Passed; API/worker runtime profile nedeniyle yapılmadı |

## WAVE-004C/D supervision and completion

| Risk | Seviye | Kanıt | Sonuç / gap |
|---|---|---|---|
| Worker crash/restart/readiness | E2 | `tests/test_worker_supervision_contract.py` | 3 passed; process role E3/W5 eksik |
| Completion receipt/fenced ACK | E2 | `tests/test_dispatch_completion_contract.py` | 2 passed; JSONL DLQ crash/restart E3 eksik |

## WAVE-005 and V-wave isolated E3

| Wave | Result | Gap |
|---|---|---|
| WAVE-005 | E2 20; API-only/worker-only E3 passed | combined/deployment matrix |
| WAVE-001-V | mapped/unmapped/invalid HTTP passed | inactive/READ-only/foreign scope |
| WAVE-003-V | lease/fence restart passed | WAL/alignment |
| WAVE-004-V | dispatch/admission/completion restart passed | JSONL DLQ process |

## Continuation execution

| Scenario group | Result |
|---|---|
| API-only profile readiness | E3 passed: 200 ready, zero worker tasks |
| W1 HTTP active/read-only/inactive | E3 passed: 200/403/401 |
| W3/W4 remaining process matrices | Existing deterministic regressions pass; WAL/alignment and JSONL DLQ process E3 remain open |


## Continuation E3 matrix update — 2026-07-19

| Wave | Senaryo | Kanıt | Sonuç | Kalan boşluk |
|---|---|---|---|---|
| W1-V | API-key route own/foreign/read-only/inactive/unmapped/forged-agent/session purge | `test_session_principal_route_isolation.py` | Passed | status/list/update/finalize ve SDK/MCP yok |
| W3-V | SQLite WAL/reopen/commit crash/reclaim/fence/WAL ack | `/storage/mesa-lab/wave-003-v/e3-20260719T193826Z/summary.json` | Passed | real vector/graph downstream ve tüm injected points yok |
| W4-V | JSONL subprocess claim/restart/stale ack/poison/malformed tail/duplicate | `/storage/mesa-lab/wave-004-v/dlq/e3-20260719T194152Z/summary.json` | Passed | serialize/flush/fsync/rename ara crash ve consumer yok |
| W5 | API-only ready, worker-only controlled stop, combined model-disabled degraded | `/storage/mesa-lab/evidence/WAVE-005/rerun-20260719T194332Z/summary.json` | Passed | real external model-worker deployment yok |
| Regression | W1/W3/W4/W5 + purge/DAO | 63 passed | Passed | full-suite değildir |


## Continuation contract/alignment/crash update — 2026-07-19

| Wave | Senaryo | Doğrudan kanıt | Sonuç | Durum |
|---|---|---|---|---|
| W1-V | OpenAPI/README/SDK/MCP surface resolution | `/storage/mesa-lab/evidence/WAVE-001-V/contract-surface-20260719/summary.json` | start/context/end implemented; diğerleri absent by design; end finalization missing | FBNV |
| W1-V | Async SDK purge API-key route | `tests/test_async_client_auth_contract.py` | pre-fix 401; post-fix passed; MCP 1 skipped (`mcp` yok) | FBNV/BLOCKED |
| W3-V | Real LanceDB+Kùzu commit/crash/reopen/replay | `.../vector-alignment/e3-20260719T195738Z/summary.json` + repeat | 2 PASS | FBNV |
| W4-V | Injected JSONL write/ack boundaries | `.../injected-write-crashes/e3-20260719T195954Z/summary.json` + repeat | 12 scenario PASS | FBNV |
| W5 | API-only/worker-only/combined disabled rerun | `/storage/mesa-lab/evidence/WAVE-005/rerun-20260719T200350Z/summary.json` | PASS | FBNV external matrix |
| Regression | affected W1/W3/W4/W5/DAO/purge | 114 passed, 1 skipped | PASS | — |


| WAVE-003-V downstream/fence contract | E2 | `tests/test_downstream_fence_reconciliation_contract.py`, `tests/test_wal_claim_replay_contract.py` | 3 passed; real-store E3 final rerun required |
| WAVE-004-V trusted root + DLQ receipt | E2 | `tests/test_queue_trusted_root_contract.py`, durable DLQ/receipt tests | 7 passed; consumer receipt/restart E3 required |


| W3 final real-store E3 | E3 | `/storage/mesa-lab/storage/WAVE-003-V-final/rem-20260719-233859-W3W4-final-e3/summary.json` | PASS core; full reconciliation matrix open |
| W4 final JSONL/receipt E3 | E3 | `/storage/mesa-lab/storage/WAVE-004-V-final/rem-20260719-233859-W3W4-final-e3-retry/summary.json` | PASS harness; production consumer bridge open |
| W3/W4 regression | target | 9 pytest modules | 53 passed |

## Master closure final test özeti — 2026-07-20

| Kapı | Sonuç | Not |
|---|---|---|
| Campaign A–D target/regression groups | PASS | Gruplar ve E3 kanıtları `FINAL_TEST_MATRIX.md` içinde. |
| Repository-wide core | 889 passed, 10 failed | On stale/global-state failure hedefli düzeltildi. |
| Failure subset | 10/10 PASS | İki dar tekrar; full suite tekrar edilmedi. |
| CWD/protected regression | 4 passed | Trace/dummy hashleri önce/sonra aynı. |
| Runtime rehearsal | PASS | API-only ready; combined required-worker fail-closed; worker-only stop. |
| Wheel | PASS | v0.6.1 checksum/import. |
| Ruff/compile/parse/diff | PASS | Release-boundary static kapılar geçti. |
| `pip check` | FAIL | Üç optional dependency conflict. |
| Docker/CI/clean full rerun | EXTERNAL_PENDING | Independent audit handoff. |
# Fast zero-closure evidence — 2026-07-20

- Critical contract matrix: 54 passed.
- Metrics/worker and repaired async-loop matrix: 55 passed.
- Lifecycle/retrieval bounded group: 139 passed.
- Safe core suite: 902 passed, 1 fail-closed test-harness profile failure; corrected target: 1 passed. Full suite policy gereği tekrar edilmedi.
- Artifact: identical clean wheels, no bytecode, clean install/`pip check`/imports/CLI passed.
