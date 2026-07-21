# Ertelenen Konular

Şimdilik ertelenen ancak kaybolmaması gereken konular; gerekçe, risk ve yeniden değerlendirme koşuluyla kaydedilir.

| ID | Konu | Kaynak / kanıt | Erteleme gerekçesi | Risk | Yeniden değerlendirme koşulu | Hedef faz / tarih | Durum |
|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | Bekliyor |

| CONC-CAND-001 | `PersistentQueue` rotation/clear aynı dosyada lock olmadan çalışır | `mesa_memory/consolidation/loop.py:PersistentQueue.append/clear` | Faz 4 yalnız statik; gerçek race için güvenli concurrency tasarımı gerekir | Queue entry kaybı/sıralama | Faz 6 düşük concurrency testi, Faz 7 queue ownership | Faz 6-7 | Açık aday; kesin bug değil |
| DEAD-CAND-001 | İkinci application composition root ve stale/dev davranış riski | `scripts/run_server.py`; Faz 2 DOC-001 | Production entry’de çağrılmadığı kanıtlanmadı | Config/lifecycle drift | Faz 12 deployment parity | Faz 12 | Açık aday |
| DEAD-CAND-002 | `MesaStore.mdelete` sessiz `pass` ile interface görünümü sağlıyor | `mesa_client/langchain.py:MesaStore.mdelete` | Consumer zinciri tamamlanmadı | Delete beklentisi sessiz karşılanmayabilir | Faz 8 contract testi | Faz 8 | Açık aday |
| CONFIG-CAND-001 | `max_batch_tokens` extraction batch yolunda kullanılmıyor | `mesa_memory/config.py:max_batch_tokens` | Model/runtime çalıştırma yasak | Token budget aşımı | Faz 10 kontrollü test | Faz 10 | Açık aday |

| SECRET-CAND-001 | CI canary environment’ında production-biçimli sabit API-key literal | `.github/workflows/ci.yml:installation-verification` | Literalin gerçek credential mı test fixture mı olduğu statik olarak kanıtlanamaz; değer raporlanmadı | Secret yanlışlıkla committed ise credential exposure | Secret rotation/TruffleHog sonuç artefactı ve CI owner teyidi | Faz 12 / güvenli operasyon incelemesi | Şüpheli; kesin bulgu değil |
| SEC-CAND-001 | FastAPI default `/docs`, `/redoc`, `/openapi.json` authentication dışındadır | `mesa_memory/api/server.py:FastAPI(...)` | Veri/auth bypass kanıtı yok; endpoint discovery hardening kararı deployment politikasına bağlı | Düşük-orta saldırı yüzeyi | Faz 12 ingress/OpenAPI exposure kararı ve negative test | Faz 12 | Açık aday |
| MCP-CAND-001 | MCP exception metni ham `str(e)` ile tool caller’a döner | `mesa_mcp/server.py:call_tool` | Hassas değer içeren gerçek exception kanıtlanmadı; aktif hata üretimi yapılmadı | Internal path/provider detail leakage | Sentetik exception redaction contract testi | Faz 8 | Açık aday |


| CONC-CAND-002 | Maintenance worker processler arası exclusivity ve vector mutation lock dışı direct I/O | `mesa_workers/maintenance.py:_purge_vector_records/_compact_vector_storage`; `VectorEngine._mutation_lock` | Aynı deployment'ta birden fazla API/maintenance process'i çalışıp çalışmadığı statik olarak bilinmiyor; aktif concurrency testi Faz 1.5 kapısı nedeniyle yapılmadı | VACUUM/compact ile live mutation lock contention veya maintenance çakışması | Tek/çok process kontrollü integration testi ve deployment topology teyidi | Faz 7/10/12 | Açık aday; DATA-001 altında izleniyor |
| TXN-CAND-001 | `AsyncEngine.transaction()` commit'i çağırana bırakır | `mesa_storage/sqlite_engine.py:transaction`; DAO transaction çağrıları | İncelenen DAO yolları explicit commit kullanıyor; tüm call-site'ler runtime crash/cancellation ile sınanmadı | Uncommitted-close/cancellation semantiği | Fault-injection/cancellation component testleri | Faz 8 | Açık aday; kesin bulgu değil |


| WORKER-CAND-001 | Uvicorn multi-worker/reload ile periodic worker setinin çoğalması | `server.py` her lifespan'ta PageRank/consolidation/Tier3/DLQ/maintenance/REM/WAL task'i oluşturur; processler arası leader lock yok | Çalıştırma yasak; actual deployment worker count bilinmiyor | Duplicate periodic mutation/LLM maliyeti | Güvenli staging topology + iki process negative test | Faz 12 | Statik risk; ARCH-001 altında izleniyor |
| QUEUE-CAND-002 | Human-review JSONL consumer/limit semantiği | Config path ve GraphWriter producer var; production consumer entry point/`human_review_max_size` enforcement kanıtı eksik | Kapsamlı consumer zinciri bulunmadı | Review item backlog/loss | Owner/entry point doğrulaması ve queue contract testi | Faz 8 | Açık aday |


| TEST-CAND-001 | Gerçek pytest collection/pass/fail ve coverage sonucu | Faz 8 statik sayı; Faz 1.5 SEC-001/OPS-001 gate açık | Collection gerçek dotenv/import/storage etkisi taşıyabilir | Test discovery veya dependency drift görünmeyebilir | Sentetik env ve ayrı storage ile controlled collect/test | Faz 8 sonrası | Açık |
| FLAKY-CAND-001 | 11 test dosyasında sleep/real clock/random/poll patterni | Static pattern scan; CI geçmişi yok | Flaky olduğu kanıtlanmadı | Nondeterministic CI failure | Repeat/seed/time-freeze analizi | Faz 8/10 | Şüpheli |


| DLQ-REMEDIATION-001 | DLQ cross-process claim/lease, per-record outcome ve legacy item migration | Faz 9 DLQ-001 yalnız destructive clear/context kaybını sınırlı giderdi | Tam çözüm durable queue schema/protokol ve crash testleri gerektirir | Duplicate/lease/crash semantics açık | Faz 9 sonrası tasarım kararı ve isolated fault tests | Faz 9/11/12 | Açık |
| PERF-TEST-001 | Cold-start count, retrieval hydration ve worker backlog kapasite ölçümü | Faz 10 statik kanıt: PERF-002..004 | Faz 1.5 izolasyon gate’i açık; benchmark/load/soak ve gerçek LLM/servis kullanımı yasak | Capacity/SLO sayıları henüz ölçülmedi | Sentetik `/tmp` storage, mock adapter, tek process, düşük concurrency ve açık disk/RAM bütçesi | Faz 10 sonrası güvenli ortam | Açık |
| PERF-TEST-002 | SQLite VACUUM/WAL, Lance compaction ve multi-process topology ölçümü | Maintenance/lifecycle kaynak zinciri; CONC-CAND-002, WORKER-CAND-001 | Production volume/servis ve çoklu API process topology doğrulanmadı | Lock, disk headroom, duplicate worker ve executor contention | Disposable storage + staging topology, LLM/mock, before/after disk/lag metriği | Faz 10/12 | Açık |

| STAGE-MANUAL-001 | Faz 13 dinamik rehearsal matrisi: API/worker startup, tenant smoke, persistence/restart, migration, backup/restore, rollback; full Compose, gerçek model/provider, multi-worker/replica, network/disk/OOM, registry/TLS/canary/load/soak | Faz 13 `STATIC_PLAN_ONLY`; açık P0’lar, dotenv/runtime blocker’ları, Docker/artifact yokluğu ve kullanıcı sınırları | Deployment/lifecycle/dayanıklılık kanıtı yok | SEC-002/DATA-005/DLQ-001 ile config/runtime kapıları kapandıktan sonra, yalnız isolated staging ve sentetik veri | Faz 13 sonrası, production öncesi | Açık; kritik storage/tenant/rollback senaryoları production öncesi zorunlu |


## Faz 11–12 sonrası doğrulama kuyruğu (2026-07-19)

| ID | Ertelenen çalışma | Bağımlılık / yeniden başlatma koşulu |
|---|---|---|
| DR-001 | İzole full backup/restore ve reconciliation rehearsal | Güvenli runtime, sentetik storage, manifest/encryption/retention tasarımı |
| MIG-VERIFY-001 | Prior-version migration, lock/idempotency/resume/rollback doğrulaması | Tenant-safe fixture ve migration yetkisi |
| OPS-VERIFY-001 | Docker build/Compose persistence/restart ve image inspection | Docker runtime; isolated secret-free config |
| REL-VERIFY-001 | Wheel/sdist artifact canary, staged deploy/rollback ve migration-DR compatibility | Üretilmiş immutable artifact, CI/release ortamı |

Bu maddeler `Verified` değildir; Faz 13.5 revalidation’dan sonra yetkili fazlarda planlanmalıdır.


## Faz 14 deferred sınıflandırması (2026-07-19)

| Deferred iş | Kategori | Risk | Sahip | Son tarih | Karara etkisi |
|---|---|---|---|---|---|
| CONC-CAND-001 | Production öncesi zorunlu | Queue kaybı/yarış | Atanmalı | Belirlenmeli | Queue güvenliği kanıtlanmadan trafik yok |
| DEAD-CAND-001 | İlk kontrollü production trafiğinden önce zorunlu | Entry-point parity | Atanmalı | Belirlenmeli | Prod/canary entry aynı artifact ile doğrulanmalı |
| DEAD-CAND-002 | İlk release sonrasında kısa sürede zorunlu | Sessiz delete contract | Atanmalı | Belirlenmeli | Client contract izlenmeli |
| CONFIG-CAND-001 | Kapasite arttırmadan önce zorunlu | Token budget | Atanmalı | Belirlenmeli | Yük/model ölçeği öncesi |
| SECRET-CAND-001 | Production öncesi zorunlu | Olası credential hijyeni | Atanmalı | Belirlenmeli | Secret scan/owner teyidi gerekir |
| SEC-CAND-001 | İlk release sonrasında kısa sürede zorunlu | Public docs surface | Atanmalı | Belirlenmeli | Ingress policy ile kapat/karar ver |
| MCP-CAND-001 | İlk kontrollü production trafiğinden önce zorunlu | Error detail leakage | Atanmalı | Belirlenmeli | MCP etkinse redaction testi |
| CONC-CAND-002 | Kapasite arttırmadan önce zorunlu | Maintenance concurrency | Atanmalı | Belirlenmeli | Çok process öncesi |
| TXN-CAND-001 | İlk kontrollü production trafiğinden önce zorunlu | Cancellation/commit semantics | Atanmalı | Belirlenmeli | Failure-path test gerekir |
| WORKER-CAND-001 | Kapasite arttırmadan önce zorunlu | Duplicate periodic workers | Atanmalı | Belirlenmeli | Replica artırmadan önce |
| QUEUE-CAND-002 | İlk kontrollü production trafiğinden önce zorunlu | Human-review backlog | Atanmalı | Belirlenmeli | Feature etkinse consumer/limit kanıtı |
| TEST-CAND-001 | Production öncesi zorunlu | Test collection/coverage belirsiz | Atanmalı | Belirlenmeli | Güncel worktree release gate |
| FLAKY-CAND-001 | İlk release sonrasında kısa sürede zorunlu | CI kararsızlığı | Atanmalı | Belirlenmeli | Repeat/seed sonucu |
| DLQ-REMEDIATION-001 | Production öncesi zorunlu | DLQ duplicate/loss/crash | Atanmalı | Belirlenmeli | Durable claim/ack + runtime tests |
| PERF-TEST-001 | Kapasite arttırmadan önce zorunlu | Bilinmeyen kapasite | Atanmalı | Belirlenmeli | Kapasite baseline’ı |
| PERF-TEST-002 | Kapasite arttırmadan önce zorunlu | Storage maintenance/topology | Atanmalı | Belirlenmeli | Replica/volume ölçeği öncesi |
| STAGE-MANUAL-001 | Production öncesi zorunlu | Staging hiç çalışmadı | Atanmalı | Belirlenmeli | Dinamik rehearsal geçmeli |
| DR-001 | Production öncesi zorunlu | Restore edilememe | Atanmalı | Belirlenmeli | Full restore/reconcile drill |
| MIG-VERIFY-001 | Production öncesi zorunlu | Migration veri/tenant riski | Atanmalı | Belirlenmeli | Prior-version ve rollback kanıtı |
| OPS-VERIFY-001 | Production öncesi zorunlu | Container persistence/startup | Atanmalı | Belirlenmeli | Docker/Compose restart drill |
| REL-VERIFY-001 | Production öncesi zorunlu | Artifact/rollback belirsiz | Atanmalı | Belirlenmeli | Immutable artifact ve rollback rehearsal |

Production öncesi zorunlu deferred kayıt sayısı 9’dur. Owner ve deadline repository’den bilinmediği için `Atanmalı` / `Belirlenmeli` olarak bırakılmıştır.

## WAVE-002 DATA-001 lifecycle verification gap

Onaylanan SQLite journal/tombstone modeli E2’de uygulandı. Ertelenen zorunlu kanıt: gerçek Kuzu/Lance failure/restart, background recovery worker wiring, operator-controlled backup/restore purge-ledger reconciliation ve E3 authenticated HTTP lifecycle. DATA-001 bu kanıtlar olmadan kapanmaz.

## WAVE-003 verification gap

| ID | Ertelenen doğrulama | Gerekçe / yeniden başlatma koşulu | Durum |
|---|---|---|---|
| WAVE-003-V | Real Lance/Kuzu + iki worker + crash-before/after-ACK + startup replay + dual alignment barrier/lease E3 | WAVE-005 config/runtime isolation ve disposable isolated runtime; user-owned trace-file write path güvenli hale gelmeli | Açık, production öncesi zorunlu |

## Remaining verification matrix

- WAVE-001-V: foreign session/status/purge and tenant mismatch route E3.
- WAVE-003-V: WAL downstream/alignment restart evidence.
- WAVE-004-V: JSONL DLQ append/crash/replay/poison process evidence.


## Continuation E3 matrix update — 2026-07-19

- `WAVE-001-V`: session status/list/update/finalize ve administrative global purge route’ları uygulamada bulunmadığı için matrixin bu maddeleri `BLOCKED_BY_ABSENT_ROUTE_SURFACE`; karar/ADR gerekir.
- `WAVE-003-V`: gerçek Lance/Kùzu durable downstream idempotency ve crash injection noktaları production öncesi zorunludur.
- `WAVE-004-V`: write/flush/fsync/rename arasındaki deterministic crash injection ve gerçek consumer downstream receipt production öncesi zorunludur.
- W5 external model-worker combined deployment, Docker/artifact/DR rehearsal bağımsız açık zorunlu işlerdir.


## Continuation contract/alignment/crash update — 2026-07-19

- `FLOW-002`: `/session/{id}/end` final consolidation yerine yalnız log üretir; approved durable finalization/dispatch tasarımı gerekir.
- W3: actual vector/graph write failure, stale-fence downstream write, incomplete-reconciliation ve consumer exact-once production öncesi zorunludur.
- W4: actual consumer receipt/downstream idempotency ve PersistentQueue configured-root/symlink rejection policy production öncesi zorunludur; power-loss fs guarantee local process-crash kanıtından çıkarılamaz.
- MCP process testini açmak için declared optional `mcp` dependency’nin uygun isolated environment’da bulunması gerekir.


- W3: run final real-store LanceDB/Kùzu downstream failure/stale-fence/restart E3 after composite-id verification correction; include reconciliation extra/payload/scope/unknown cases.
- W4: wire JSONL consumer completion to SQLite receipt and implement receipt-before-ACK restart reconciliation; run poison/partial-tail/root process E3.


- Full real-store reconciliation cases: extra, payload/version, scope, unknown.
- Production JSONL consumer automatic receipt/ACK restart bridge and receipt-write power-loss matrix.

## Master closure sonrası deferred / external verification — 2026-07-20

- Production öncesi zorunlu: clean full core suite, coverage ve harici CI runner.
- Production öncesi zorunlu: Docker build, Compose API/worker health, named-volume restart/persistence, rollback.
- Production öncesi zorunlu: unmanaged legacy drift ve raw-log tenant backfill migration kanıtı.
- Production öncesi zorunlu: açık 21 release blocker’ın kanıtla kapanması ve Faz 13 dinamik rehearsal.
- Kapasite öncesi zorunlu: PERF-002/003 bounded retrieval/worker measurement.
- Yerel environment: `pip check` optional dependency conflict’leri temiz/locked environment’ta çözülmeli.
