# MESA Üretime Hazırlık Görevleri

## Durumlar

- [ ] BEKLİYOR
- [~] ÇALIŞILIYOR
- [x] ÇÖZÜLDÜ
- [!] ÇÖZÜLMEDİ
- [?] BLOKLANDI

## Aşama 1 — İlk Analiz ve Temizlik

- [x] P0-001 — Control-plane için server-side rol denetimi ekle
  - Kapsam: `mesa_api/routers/control/router.py`, RBAC
  - Doğrulama: Cross-role negatif route testleri
  - Bağımlılık: Yok

- [x] P0-002 — V3 route’larını authenticated principal’a bağla
  - Kapsam: `mesa_api/router.py`, session authorization
  - Doğrulama: Insert/search/status/record cross-principal testleri
  - Bağımlılık: P0-001

- [x] P0-003 — Sahte V4 rebuild kabulünü fail-closed yap
  - Kapsam: `mesa_api/v4_router.py`, rebuild operation
  - Doğrulama: Yetkisiz ve side-effect içermeyen çağrı testleri
  - Bağımlılık: P0-001

- [x] P0-004 — SPA static dosya containment kontrolü uygula
  - Kapsam: `scripts/run_server.py`, dashboard static route
  - Doğrulama: Encoded traversal negatif testleri
  - Bağımlılık: Yok

## Aşama 2 — Mimari Düzen

- [x] P0-005 — Revision işlemlerini gerçek dataset sahipliğine bağla
  - Kapsam: `mesa_api/v4_router.py`, `mesa_storage/dao.py`
  - Doğrulama: Aynı tenant cross-dataset revision negatif testleri
  - Bağımlılık: P0-001

- [x] P0-006 — Catalog tenant/workspace FK bütünlüğünü kur
  - Kapsam: V4 catalog migrationları, `MemoryDAO`
  - Doğrulama: Cross-tenant workspace ve ID collision migration testleri
  - Bağımlılık: P0-005

- [x] P0-007 — Approval kararını authenticated aktör ve CAS ile sınırla
  - Kapsam: control router, `approval_repo`
  - Doğrulama: PENDING dışı, yarışan ve bilinmeyen approval testleri
  - Bağımlılık: P0-001

- [ ] P0-008 — Approval payload hash ve audit kaydını güvenli hale getir
  - Kapsam: MCP middleware, operation ledger, activity repository
  - Doğrulama: Hash mismatch ve secret canary storage/log testleri
  - Bağımlılık: P0-007

- [ ] P1-001 — MemoryDAO sorumluluk sınırları için ayrıştırma planı uygula
  - Kapsam: `mesa_storage/dao.py`, repository arayüzleri
  - Doğrulama: Ayrıştırılan repository sözleşme testleri
  - Bağımlılık: P0-005, P0-006

- [ ] P2-001 — Katman bağımlılık yönlerini tek yönlü hale getir
  - Kapsam: `mesa_memory`, `mesa_api`, `mesa_storage`, `mesa_workers`
  - Doğrulama: Import-cycle ve package import testleri
  - Bağımlılık: P1-001

- [ ] P2-002 — Çoklu instance state sınırını açık kapasite modeline bağla
  - Kapsam: router/valence/gateway process-local state
  - Doğrulama: Documented single-writer veya multi-instance contract testi
  - Bağımlılık: P1-001

## Aşama 3 — Güvenlik ve Hata Düzeltmeleri

- [ ] P0-009 — V4 HTTP ve MCP yazma girişlerini ortak sınırlandır
  - Kapsam: V4 schema, MCP adapter, request middleware
  - Doğrulama: Body, metadata depth/byte ve secret rejection testleri
  - Bağımlılık: P0-001

- [ ] P0-010 — V4 idempotency ve catalog admission’ını atomikleştir
  - Kapsam: V4 insert route, receipt ve catalog transactionları
  - Doğrulama: Fault-injection retry ve orphan-free admission testleri
  - Bağımlılık: P0-005

- [ ] P0-011 — Purge sonrası vector varlığını exact doğrula
  - Kapsam: `mesa_storage/vector_engine.py`, purge saga
  - Doğrulama: 150k+ kayıtta failed-delete fail-closed testi
  - Bağımlılık: P0-010

- [ ] P0-012 — Tier-3 için bağımsız adapter zorunluluğu koy
  - Kapsam: adapter config, runtime startup, Tier-3 validator
  - Doğrulama: Aynı provider/model reddi ve dual-adapter testleri
  - Bağımlılık: P0-009

- [ ] P0-013 — Çelişkili güncellemeleri Tier-3’e yönlendir
  - Kapsam: valence signal üretimi, consolidation routing
  - Doğrulama: Explicit correction uçtan uca regression testi
  - Bağımlılık: P0-012

- [ ] P0-014 — V3, V4 ve control için gerçek rate limit uygula
  - Kapsam: API middleware, router dependencies
  - Doğrulama: Principal bazlı minute ve günlük 429 testleri
  - Bağımlılık: P0-001

- [ ] P0-015 — Approval dashboard sözleşmesini düzelt
  - Kapsam: `mesa_dashboard/src/api/controlApi.ts`, `Approvals.tsx`
  - Doğrulama: Pending approval görünürlüğü ve non-2xx UI testleri
  - Bağımlılık: P0-007

- [ ] P1-002 — Credential expiry ve atomik rotation ekle
  - Kapsam: API key, MCP credential ve migrationlar
  - Doğrulama: Expiry, rollback ve replacement-key testleri
  - Bağımlılık: P0-001

- [ ] P1-003 — MCP session ve circuit-breaker yarışlarını fence et
  - Kapsam: `v4_service.py`, gateway operations
  - Doğrulama: Concurrent session ve HALF_OPEN probe testleri
  - Bağımlılık: P0-008

- [ ] P1-004 — Worker dispatch lease yenilemesini ekle
  - Kapsam: `worker_runtime.py`, dispatch queue
  - Doğrulama: Long-running dispatch reclaim negatif testi
  - Bağımlılık: P0-010

- [ ] P1-005 — Retrieval degraded ve embedding migration semantiğini ekle
  - Kapsam: vector engine, hybrid retrieval, model schema
  - Doğrulama: LanceDB hata ve dimension-change regression testleri
  - Bağımlılık: P0-011

- [ ] P1-006 — Temporal filter ve revision manifest bütünlüğünü uygula
  - Kapsam: V4 search, revision/source-chunk persistence
  - Doğrulama: `valid_from/to` ve multi-chunk hash testleri
  - Bağımlılık: P0-005

- [ ] P1-007 — Benchmark dosya ve Ollama egress sınırını daralt
  - Kapsam: dashboard planner, paths, Ollama config
  - Doğrulama: `/dev/zero`, traversal ve allowlist negatif testleri
  - Bağımlılık: P0-004

- [ ] P1-008 — Benchmark job kaynak tüketimini sınırla
  - Kapsam: child process, event file, SSE stream
  - Doğrulama: Timeout, pipe drain ve bounded event retention testleri
  - Bağımlılık: P1-007

- [ ] P1-009 — Health, bridge ve HTTP audit çıktılarını redakte et
  - Kapsam: API health, MCP errors, observability
  - Doğrulama: Least-privilege response ve audit-field testleri
  - Bağımlılık: P0-008

## Aşama 4 — Eksik Üretim Gereksinimleri

- [ ] P1-010 — Benchmark image’ını digest-pinned ve non-root yap
  - Kapsam: `mesa-benchmark/Dockerfile`
  - Doğrulama: Image user, digest ve offline entrypoint smoke
  - Bağımlılık: P1-008

- [ ] P1-011 — Forward-only migration ve restore gate’ini netleştir
  - Kapsam: Alembic downgrade, recovery runbook
  - Doğrulama: Backup-restore parity ve destructive downgrade guard testi
  - Bağımlılık: P0-006

- [ ] P1-012 — Dependency ve artifact güvenlik taramasını release gate’e ekle
  - Kapsam: CI workflows, SBOM, Python/Node/image scans
  - Doğrulama: Locked scan job ve threshold failure testi
  - Bağımlılık: P1-010

## Aşama 5 — Test ve Regresyon

- [ ] P1-013 — MCP ve benchmark için ayrı coverage eşiği koy
  - Kapsam: pytest coverage, benchmark ve frontend CI jobs
  - Doğrulama: Coverage raporu ve eşik düşüşü negatif testi
  - Bağımlılık: P0-015, P1-008

- [ ] P2-003 — Mypy istisnalarını kritik modüllerden azalt
  - Kapsam: `pyproject.toml`, API/storage/worker/MCP modülleri
  - Doğrulama: Hedef mypy ve ratchet kontrolü
  - Bağımlılık: P0-010, P1-004

## Aşama 6 — Canlıya Çıkış Kontrolü

- [ ] P1-014 — Gereksiz root trace artefaktını güvenle kaldır
  - Kapsam: `cold_path_trace.txt`, ilgili kullanım ve ignore kuralları
  - Doğrulama: Import/runtime/test/dokümantasyon kullanım taraması
  - Bağımlılık: P1-009

- [ ] P2-004 — Kullanılmayan demo taslağını doğrula ve düzenle
  - Kapsam: `demo/Untitled-1.md`, demo dokümantasyonu
  - Doğrulama: Import/runtime/test/dokümantasyon kullanım taraması
  - Bağımlılık: P1-014

- [ ] P0-016 — P0 kabul kapıları kapalıyken production kararını NO-GO tut
  - Kapsam: release checklist, CI sonuçları
  - Doğrulama: Tüm P0 doğrulaması ve release preflight
  - Bağımlılık: P0-002, P0-003, P0-004, P0-006, P0-008, P0-010, P0-011, P0-012, P0-013, P0-014, P0-015
