# Düzeltme Planı

Yalnızca doğrulanmış veya yeterli kanıtla önceliklendirilmiş bulgular buraya eklenir. Kod değişikliği, ilgili fazın tamamlanması ve kullanıcı onayı sonrasında yapılır.

| ID | Öncelik | Önem | Etki alanı | Doğrulama / regresyon testi | Düzeltme yönü | Bağımlılıklar | Tahmini efor | Düzeltme riski | Durum |
|---|---|---|---|---|---|---|---|---|---|
| SEC-002 | P0 | Kritik | Principal/tenant modeli; Release blocker: Evet | WAVE-001 E2 unmapped/mapped/inactive/read-only session tests; E3 cross-principal HTTP eksik | API auth subject’ini RBAC/session create ile bağla; diğer session/status/purge yollarını principal scope’a taşı | API auth, RBAC, session, config isolation | Yüksek | Orta-Yüksek | Fixed but not verified |
| DATA-002 | P0 | Kritik | Triple-store hata semantiği; Release blocker: Evet | WAVE-002 E2 Kuzu failure → vector compensation + SQLite skip geçti; gerçek tüm-store/restart eksik | Compensation/outbox veya retryable pending state | DAO, Kuzu, LanceDB, SQLite | Yüksek | Yüksek | Fixed but not verified |
| SEC-003 | P1 | Yüksek | Credential lifecycle; Release blocker: Evet | Raw credential persistence negative testi | Non-reversible rate-limit subject | API auth, rate-limit storage | Orta | Orta | Confirmed open |
| LOGIC-002 | P1 | Yüksek | Extraction terminal-state contract; Release blocker: Evet | Partial malformed response → DLQ/retry | Coverage bilgisini koru; empty extraction explicit karar olsun | Ingestion worker, DLQ | Orta | Orta | Confirmed open |
| LOGIC-003 | P1 | Yüksek | Retrieval policy; Release blocker: Evet | Cold/no-graph quarantined candidate testi | Ortak epistemic filter | Retriever, DAO | Orta | Küçük-Orta | Confirmed open |
| SDK-001, SDK-002, SDK-003 | P1 | Yüksek | Ortak API/SDK/MCP contract; Release blocker: Evet | Sync/async/MCP URL-auth-schema testi | URL, header, purge schema hizalama | API, SDK, MCP | Orta | Orta | Confirmed open |
| DATA-003 | P1 | Yüksek | Embedding failure policy; Release blocker: Hayır | Provider failure fixture | Zero fallback yerine failure, DLQ veya repair | Embedding provider | Orta | Orta | Confirmed open |
| DATA-004 | P1 | Yüksek | Vector merge failure; Release blocker: Hayır | WAVE-002 E2 single/bulk merge failure fixture | Fail closed; gerçek Lance/replay repair kanıtı | Vector store | Orta | Orta | Fixed but not verified |
| LOGIC-001 | P1 | Yüksek | Session/status authorization; Release blocker: Hayır | start→insert→status endpoint testi | Status scope’unu raw-log ownership ile hizala | Router, RBAC, raw-log DAO | Orta | Küçük-Orta | Confirmed open |
| ARCH-003 / Alt iş: CWD persistence | P1 | Yüksek | Runtime storage ownership; Release blocker: Evet | Isolated CWD write-negative testi | CWD debug persist’ini kaldır/managed path’e al | Router, ingestion worker, storage | Orta | Küçük | Confirmed open |
| PERF-001 | P2 | Orta | Metrics route metadata; Release blocker: Hayır | Parametreli URL cardinality testi | Raw path yerine route template | Metrics middleware | Düşük | Küçük | Confirmed open |
| RLS-001 | P1 | Yüksek | Tenant state modeli; Faz 6 concurrency; Release blocker: Evet | İki tenant valence/telemetry isolation testi | Valence state, threshold ve telemetry cache’ini agent-scoped yap | Valence, routing, telemetry | Yüksek | Orta-Yüksek | Confirmed open |
| INPUT-001 | P1 | Yüksek | API request limit politikası; Release blocker: Evet | UTF-8 total-size, list/depth, metadata total-body negatif testleri | Body byte bütçesi + recursive metadata/extra validation | API schema/validation | Orta | Orta | Confirmed open |
| ARCH-004 | P1 | Yüksek | MCP stats contract; Release blocker: Evet | İki agent graph count negative MCP/API testi | Direct storage bypass’ını kaldır; scoped DAO/REST stats kullan | MCP, SDK, DAO | Orta | Küçük-Orta | Confirmed open |
| ARCH-003 / Alt iş: raw content logging | P1 | Yüksek | Runtime log/storage politikası; Release blocker: Evet | Raw content’in CWD/log dışında kaldığını doğrulayan test | Debug file write’larını kaldır; redacted structured event kullan | Router, logging | Orta | Küçük | Confirmed open |
| CI-001 | P2 | Orta | CI action update politikası; Release blocker: Hayır | SHA pin/policy lint | Third-party action’ları immutable SHA ile pinle | CI workflow | Düşük | Küçük | Confirmed open |

## Faz 6 önceliklendirilmiş çözüm sırası

| ID | Öncelik | Önem | Etki alanı | Doğrulama | Düzeltme yönü | Durum |
|---|---|---|---|---|---|---|
| DATA-005 | P0 | Kritik | Vector migration/WAL | Barrier, partial-flush ve restart component/chaos testleri | Lease/fencing + mutation barrier + durable WAL claim/ack/replay + idempotency | Planlandı |
| CONC-002 | P1 | Yüksek | Raw-log worker | Duplicate delivery, lease expiry ve edge failure testleri | SQL CAS claim/lease; guarded transitions; idempotent completion/outbox | Planlandı |
| DATA-002, DATA-001 | P0/P1 | Kritik/Yüksek | Triple-store/purge/maintenance | WAVE-002 E2 journal/tombstone, Kuzu/vector failure, retry/exact scope; real-store E3 eksik | SQLite canonical journal, visible retry/repair, maintenance coordination | Fixed but not verified |
| CONC-003, RLS-001 | P1 | Yüksek | Valence/routing state | Barrier, per-agent ve shutdown-save testleri | Per-agent single-writer/lock, consistent snapshot ve orderly drain | Planlandı |
| ARCH-002 | P1 | Yüksek | Shutdown/executor lifecycle | Controlled shutdown task/resource testi | Producer cancel/drain → state save → storage/executor close sırası | Planlandı |


## Faz 7 worker/queue çözüm sırası

| Sıra | ID | Öncelik | Düzeltme yönü | Önce gerekli regresyon |
|---:|---|---|---|---|
| 1 | DLQ-001 | P0 | Tenant-aware durable claim/ack/replay; destructive clear'ı kaldır | Crash-before/after-ack, >batch, cross-agent, poison record |
| 2 | FLOW-001, CONC-002, LOGIC-002 | P0/P1 | Raw-log CAS lease, attempt/idempotency, terminal-state outcome ve startup recovery | Duplicate claim, stale lease, edge failure, processed integrity |
| 3 | QUEUE-001 | P1 | Queue quota/backpressure, bounded dispatch, disk/backlog metrics | Limit/reject, worker-offline/backlog, disk failure |
| 4 | DATA-005 | P0 | Migration WAL replay owner/ack/idempotency | Partial flush/restart/concurrent alignment |
| 5 | ARCH-002, WORKER-001 | P1 | Worker registry/drain/supervision/health | Task death, shutdown cancellation, lag/readiness |
| 6 | DATA-001, CONC-003 | P1 | Maintenance coordination ve agent-scoped serial state | Maintenance/live mutation, valence concurrency |


## Faz 8 test yazma sırası ve minimum production kapısı

| Sıra | Zorunlu test | Mevcut durum | Fixture/önkoşul | Seviye |
|---:|---|---|---|---|
| 1 | Principal→agent auth, cross-agent write/purge/status/session | DAO read kısmi; endpoint negative eksik | Sentetik key, iki principal/agent, ayrı storage | API security integration |
| 2 | SQLite/Lance/Kuzu dual-write ve failure/recovery | İlk vector chaos kısmi | Fault-injection Kuzu/commit/partial success | Component/chaos |
| 3 | Raw-log CAS claim, duplicate, stale lease/crash/restart | Eksik | 1-2 controlled task, SQLite temp storage | Component |
| 4 | DLQ tenant claim/ack/crash/poison replay | Eksik | Isolated JSONL/SQLite or durable replacement | Component |
| 5 | Purge/tombstone/hard-delete üç-store scope | Kısmi | SQL/Lance/Kuzu fixture | Integration |
| 6 | Startup/readiness/worker death/shutdown drain | Mock happy path kısmi | Sentetik env, separate storage, no external adapter | Lifecycle integration |
| 7 | Sync/async SDK + MCP HTTP contract | Eksik | ASGI/mock transport | Contract |
| 8 | Migration schema/alignment WAL idempotency/restart | Schema init kısmi | Disposable DB/vector fixture, fault injection | Migration/component |
| 9 | Secret scan, dependency scan, Docker smoke, backup/restore | CI secret scan/docker build; smoke/backup unsafe/manual | Isolated CI container/storage | Ops/manual |
| 10 | Boundary, performance/load/soak, flaky stabilization | Kısmi/manuel | Resource budget and no production storage | Faz 10+ |


## Faz 9 dalga 1 sonucu

| ID | Durum | Regresyon kanıtı | Kod değişikliği | Kalan risk |
|---|---|---|---|---|
| DLQ-001 | Partially fixed / Mitigated | Önce fail eden source invariant; sonrası static invariant + `py_compile` geçti | `loop.py`: agent_id propagation, no-clear replay, selected atomic acknowledgement | Cross-process claim/lease, crash/outcome contract, legacy queue migration; Faz 9 sonrası ayrı tasarım/test gerekir |

Faz 9'da SEC-002, DATA-002, DATA-005, FLOW-001 ve CONC-002 büyük/public-contract veya migration/queue tasarımı gerektirdiği için otomatik değiştirilmedi.
## Faz 10 performans çözüm sırası

| ID | Öncelik | Önem | Etki alanı | Doğrulama | Düzeltme yönü | Durum |
|---|---|---|---|---|---|---|
| PERF-002 | P1 | Yüksek | Search hot path RAM/latency | Büyük sentetik tenantta query-count, p95 ve peak RSS testi | Cold-start için agent-scoped COUNT/EXISTS; tam satır hydration’ı kaldır | Planlandı |
| PERF-003 | P1 | Yüksek | Worker CPU/RAM/LLM ve backlog | Paged claim, cancellation/restart, lag ve kapasite testi | Cursor/claim batch, per-tenant time/token/record budget, PageRank sınırı/ayrı worker | Planlandı |
| PERF-004 | P2 | Orta | Search hydration SQL round-trip | Limit 1/10/50 query-count ve p95 component testi | Agent-scoped batch hydration | Planlandı |

Quick win: PERF-002 için `get_memories` yerine agent-scoped sayım eklendiğinde request path’in tam row hydration’ı kalkar. Mimari değişiklik gerektiren ana iş PERF-003’tür: periodic işler API process’inden ayrılmış, tenant-budget/claim/lag görünürlüğü olan tekil worker topolojisine geçmelidir.

## Faz 13 sonrası zorunlu rehearsal sırası

| Sıra | Bulgu / kapı | Düzeltme | Tekrar edilecek adım |
|---:|---|---|---|
| 1 | SEC-002 | Principal→agent authorization’ı tüm write/status/purge/session yollarında zorunlu kıl | İki-agent tenant isolation smoke |
| 2 | CONFIG-002, SEC-001 | Dotenv isolation ve storage-write öncesi fail-closed config preflight | Config negative matrix, API-only startup |
| 3 | STAGE-001, WORKER-001, ARCH-002 | Ayrı API/worker role, lifecycle/drain ve worker-aware readiness | API/worker startup-shutdown ve duplicate negative |
| 4 | DATA-005, DLQ-001 | Crash-safe replay/claim ve cross-store recovery | Persistence/restart, migration/queue rehearsal |
| 5 | Docker/artifact/rollback/backup eksikleri | Doğrulanmış immutable artifact ve isolated restore yolu | Single-container, rollback ve separate-target backup/restore |

Bu sıra tamamlanmadan Faz 13 dinamik smoke tekrar edilmez.

## Faz 13.5 zorunlu audit düzeltme sırası

| Sıra | ID | Zorunlu kayıt düzeltmesi | Çıkış kriteri |
|---:|---|---|---|
| 1 | AUDIT-INT-001 | Faz 11 migration/backup/restore ve Faz 12 Docker/CI/CD/operasyon sonuçlarını mevcut kanıtla, static/runtime ayrımıyla persist et | Her iki faz için CURRENT_PHASE/COMMAND_LOG/CHANGELOG/findings-blockers-plan-test-readiness izi mevcut |
| 2 | RECORD-001 | Faz 11/12 sonrası canonical açık P0/P1 durum indeksini üret | Benzersiz ID, önem, durum ve blocker eşleşmesi tek sayım verir |
| 3 | EVIDENCE-001 | DLQ-001 için kalıcı, izole regression ve crash/tenant/claim kanıtı ekle | Finding `Verified` yapılmadan target + related test geçer |
| 4 | RECORD-002 | DLQ-001 ikinci başlığını canonical olmayan durum tarihçesi olarak normalize et | Heading tabanlı duplicate ID sayımı kalmaz |
| 5 | AUDIT-INT-001 | Faz 13.5 bütünlük doğrulamasını yeniden çalıştır | Tek giriş sonucu üretilir ve Faz 14’e geçiş yeniden değerlendirilir |


## Faz 11–12 formal remediation planı (2026-07-19)

| Sıra | Kapsam | İlişkili bulgular | Çıkış kanıtı | Mevcut durum |
|---:|---|---|---|---|
| 1 | Tenant-safe raw-log backfill ve Alembic schema verification | MIG-001, MIG-004 | Prior-version fixture, dry-run/reject report, fingerprint/postflight, rollback | Blocked — static-only |
| 2 | Kùzu versioned migration protokolü | MIG-002, MIG-003 | Lock/fencing, idempotency, interruption-resume, source-target reconcile | Blocked — static-only |
| 3 | Üç-store backup/restore DR tasarımı | BACKUP-001, RESTORE-001, TEST-002 | Manifest/checksum/encryption/retention, isolated restore drill, full reconciliation | Blocked — static-only |
| 4 | Compose volume/path ve build context düzeltmesi | DOCKER-001, DOCKER-002, DOCKER-003 | Image inspect, up/write/restart persistence, pinned build/SBOM | Blocked — Docker runtime yok |
| 5 | Fail-closed deployment config ve worker-aware readiness | CONFIG-001, HEALTH-001, CONFIG-002, STAGE-001 | Negative config, API-only role, worker fault/lag readiness | Blocked — isolated runtime kapısı kapalı |
| 6 | Artifact-based CI/release/rollback gate | CI-002, RELEASE-001, CI-001 | Clean wheel/sdist install, pinned actions, staged rollback and DR compatibility drill | Not verified |

Bu plan implementasyon yetkisi vermez; Faz 14 kararı içermez.


### Faz 13.5 revalidation plan durumu (2026-07-19)

| Plan maddesi | Güncel durum |
|---|---|
| AUDIT-INT-001 — Faz 11/12 formal persistence | Completed; kayıtlar ve çapraz eşleşmeler doğrulandı |
| RECORD-001 — canonical sayım | Completed; 9 P0 / 40 P1 / 43 teknik release blocker |
| RECORD-002 — DLQ duplicate normalize | Completed; noncanonical tarihçe etiketi mevcut |
| TEST-001 — P0 release gate izlenebilirliği | Plan kapsamına açıkça bağlı; Faz 8 minimum production gate bölümü ve `TEST_MATRIX.md` eşleşmesi vardır |
| EVIDENCE-001 — Faz 9 runtime regression | Open; Faz 14 öncesi karar kanıtını yanıltmayacak biçimde `Fixed but not verified` ayrımı korunur |


## Faz 14 sonrası zorunlu remediation sırası (2026-07-19)

| Dalga | Kapsam | Çıkış kanıtı |
|---:|---|---|
| 0 | Production deployment/trafik freeze | `NO_GO` operasyon kararı ve owner ataması |
| 1 | SEC-002 principal/tenant/session authorization; secret/config isolation | İki-tenant E3 suite ve negative auth tests |
| 2 | DATA-002/005, claim/idempotency, DLQ/worker recovery | Fault/crash/restart E2/E3 suite |
| 3 | TEST-001/002 kritik release gates | Güncel worktree ve immutable artifact üzerinde passing gates |
| 4 | MIG/BACKUP/RESTORE/DR | Prior-version migration + full restore/reconcile drill |
| 5 | Docker volume/config/health ve API-worker topology | Build/startup/shutdown/restart persistence rehearsal |
| 6 | Artifact release/rollback ve Faz 13 dinamik rehearsal | En az `REHEARSAL_PASS_WITH_LIMITATIONS`; kritik kapılar tamamen geçmeli |

## WAVE-003 remediation result

| Bulgu | Uygulanan E2 düzeltme | Kalan zorunlu doğrulama | Durum |
|---|---|---|---|
| DATA-005 | WAL claim/ack/replay, expiry recovery, SQLite transaction dışında vector upsert, alignment boyunca mutation barrier | Real store partial flush/crash-before-after-ack, dual alignment owner lease, E3 | Fixed but not verified |
| CONC-002 | Raw-log CAS claim/lease, owner/token fenced terminal transition, expired claim recovery | İki gerçek worker side-effect, Kuzu error→retry terminal, dispatcher/startup replay, E3 | Fixed but not verified |

WAVE-003-V bağımsız verification işi olarak kuyruğa alınmıştır; WAVE-004 bağımsız remediation dalgasını bloklamaz.

## WAVE-004 remediation result

DLQ file-queue claim/ack safety E2’de iyileştirildi. Sonraki zorunlu aynı-scope çalışma: raw-log dispatcher + exact-once completion receipt, capacity/admission/backlog policy ve worker-aware readiness. Bu materyal açıklar nedeniyle WAVE-004 tamamlanmadı.

## WAVE-004A result

FLOW-001 E2 implementation `FIXED_NOT_VERIFIED`. WAVE-004B public admission policy (global/per-tenant counts, bytes, retry/in-flight limits ve overload response) kararı bekler; karar olmadan B/C/D başlamaz.

## WAVE-004B sonucu

`QUEUE-001` için default 10,000 record/512 MiB global, 2,000 record/128 MiB tenant, 32/8 in-flight, 2,000/500 retry, 8 MiB record ve 5 s Retry-After policy uygulanmıştır. Sonraki zorunlu iş W4C’de worker supervision/readiness, W4D’de completion receipt/DLQ E3 ve W5’te runtime profile izolasyonudur.

## WAVE-004C/D sonucu

W4C supervisor/readiness ve W4D completion receipt E2 düzeltmeleri tamamlandı. Sonraki kapı: WAVE-005 isolated API/worker profile; ardından WAVE-004-V gerçek process/DLQ recovery kanıtı. Hiçbir canonical finding kapanmadı.

## WAVE-005 verification sonucu

Test-isolated API-only ve worker-only profile güvenle kullanıldı. WAVE-006 açılmadan önce combined model-worker/deployment, WAVE-001-V auth matrix, WAVE-003-V WAL/alignment ve WAVE-004-V JSONL DLQ process E3 tamamlanmalıdır.

## Continuation safe resume

Next exact scenario group: `WAVE-001-V foreign-session/status/purge → WAVE-003-V WAL/alignment → WAVE-004-V JSONL DLQ process crash/replay/poison`; do not open WAVE-006 until these release matrices are complete.


## Continuation E3 matrix update — 2026-07-19

Sonraki güvenli remediation sırası: (1) WAVE-001-V için uygulamada olmayan session status/list/update/finalize yüzeyi hakkında ADR veya ek route kapsamı, ardından full authenticated tenant matrix ve SDK/MCP lifecycle; (2) WAVE-003-V gerçek Lance/Kùzu idempotent downstream alignment ile injected crash boundary; (3) WAVE-004-V write/flush/fsync/rename noktaları için kontrollü injection ve gerçek consumer receipt; (4) yalnız bunlar tamamlanınca release yeniden değerlendirmesi.


## Continuation contract/alignment/crash update — 2026-07-19

Sonraki exact scenario group: `WAVE-003-V_REAL_DOWNSTREAM_FAILURE_AND_STALE_FENCE_THEN_WAVE-004-V_CONSUMER_RECEIPT_AND_ROOT_POLICY`. W3’te gerçek Lance/Kùzu write failure, stale claimant ve reconciliation failure; W4’te downstream consumer success→ack ambiguity/idempotency ile configured-root symlink rejection tasarımı gerekir. `FLOW-002` için ayrı approved finalization/dispatch kararı olmadan W1 lifecycle kapanmaz.


W3/W4 continuation sonrası sonraki bounded iş: Kùzu composite-id fixiyle gerçek-store E3’ü tekrar et; mismatch matrisi (extra/payload/scope/unknown) için deterministic evidence ekle. Sonra JSONL consumer’ı durable SQLite completion receipt ile fence/restart reconciliation’a bağla ve process E3 çalıştır. Bu kapılar geçmeden canonical finding veya NO_GO değişmez.


Safe resume: implement/approve production JSONL consumer receipt bridge only after defining single fence ownership; separately implement full reconciliation matrix.

## Master closure sonrası zorunlu sıra — 2026-07-20

1. Clean environment dependency lock/`pip check` düzeltmesi (`ENV-001`, `OPS-001`).
2. Unmanaged legacy schema drift ve tenant backfill fail-closed migration (`MIG-001`, `MIG-004`), ardından Kuzu migration lifecycle (`MIG-002/003`).
3. Clean checkout üzerinde tek tur full core suite + coverage ve gerçek CI runner (`TEST-001`, `COVERAGE-001`, `CI-002`).
4. Docker image build, API/worker role, named-volume restart/persistence ve rollback (`DOCKER-001/003`, `RELEASE-001`).
5. Retrieval/worker bounded capacity (`PERF-002/003`).
6. Açık auth/SDK/logical residual blocker’lar ve Faz 13 dinamik rehearsal; ardından Faz 14 yeniden değerlendirmesi.
# Fast zero-closure completion — 2026-07-20

Yerel remediation planı tamamlandı. Sıradaki zorunlu işler yalnız `FAST_RESULT.md` içindeki external Docker, CI ve production-like topology/capacity komutlarıdır; yeni source fix planı yoktur.
