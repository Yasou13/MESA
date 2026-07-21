# Teknik Kararlar

Kararlar kısa ADR biçiminde kaydedilir. Karar uygulanmadan önce etkilediği faz ve kanıtlar belirtilir.

## Karar şablonu

### DEC-XXX — Kısa başlık

| Alan | Değer |
|---|---|
| Durum | Öneri / Kabul edildi / Geçersiz kılındı |
| Bağlam | — |
| Seçenekler | — |
| Karar | — |
| Gerekçe | — |
| Sonuçlar | — |
| Geri alma yöntemi | — |
| Kanıt / ilgili bulgular | — |

## Kayıtlar

Henüz teknik karar kaydedilmedi.


## DEC-002 — Faz 2’de mimari gerçeklik kaynağı statik çağrı zinciridir

| Alan | Karar |
|---|---|
| Bağlam | Dokümantasyon process isolation, dev entry point ve Docker parity iddiaları kodla farklılık gösterdi |
| Karar | Bu fazda source/import/call chain gerçek mimari kanıtıdır; doküman iddiaları ayrı statüyle sınıflandırılır |
| Sonuç | Runtime gerektiren etkiler Faz 5-7 ve Faz 12/13’e açık soru olarak yönlendirildi; kod değişikliği yapılmadı |
| Geri alma | Runtime kanıtı yeni sonucu desteklemezse audit iddia/bulgu durumu güncellenir |

## DEC-005 — Faz 5 tenant isolation değerlendirme sınırı

- Tarih: 2026-07-17
- Bağlam: Faz 5 statik denetiminde DAO SQL/LanceDB/Kùzu normal erişim yolları agent-scoped bulundu; ancak API principal binding, MCP direct storage ve global adaptive state bu güvenlik sınırını aşmaktadır.
- Karar: Normal DAO RLS kontrolleri tek başına production tenant isolation kanıtı sayılmayacaktır. `SEC-002`, `ARCH-004` ve `RLS-001` kapanmadan production tenant isolation kapısı geçilmiş kabul edilmeyecek.
- Gerekçe: Tenant sınırı auth subject, API authorization, storage queries, worker state ve MCP tool’larının birlikte taşıdığı bir invarianttır.
- Geri alma: İlgili bulgular kapsamlı negatif integration/regression testleriyle kapatılırsa bu karar yeniden değerlendirilir.


## DEC-006 — Faz 6 dinamik concurrency kanıtı ertelendi

| Alan | Karar |
|---|---|
| Durum | Kabul edildi (audit sınırı) |
| Bağlam | Faz 1.5 güvenlik/izolasyon kapısı `SEC-001` ve `OPS-001` nedeniyle geçilmedi; kullanıcı donanım ve görev sınırları da ağır model, benchmark, yük/stress ve servis başlatmayı yasaklıyor. |
| Seçenekler | (1) Dinamik race/fault testi çalıştırmak, (2) statik kanıt ve mevcut test kaynaklarıyla sınırlı kalmak. |
| Karar | Faz 6'da yalnız statik kaynak/test denetimi yapıldı; runtime, concurrency, restart, active failure injection, Docker, Ollama/REBEL ve harici LLM çağrısı yapılmadı. |
| Gerekçe | İzolasyon kanıtı olmayan ortamda test, gerçek dotenv/config veya kalıcı storage etkisi taşıyabilir; aynı zamanda görevdeki kaynak sınırları aşılmamalıdır. |
| Sonuçlar | DATA-005, CONC-002 ve CONC-003 statik olarak doğrulandı; bunların dinamik senaryoları Faz 8 ve uygun güvenli environment sonrasına test matrisi olarak taşındı. |
| Geri alma yöntemi | SEC-001/OPS-001 kapandıktan sonra sentetik env, ayrı storage ve düşük concurrency barrier/fault testleri ile bulgu kanıtı güncellenir. |
| Kanıt / ilgili bulgular | SEC-001, OPS-001, DATA-005, CONC-002, CONC-003 |


## DEC-007 — Faz 7 worker doğrulama sınırı

| Alan | Karar |
|---|---|
| Durum | Kabul edildi |
| Bağlam | Faz 1.5 izolasyon kapısı açık; görev worker/process/model/disk etkili testleri sınırlıyor. |
| Karar | Faz 7 worker/queue sonuçları kaynak ve mevcut test kanıtına dayanır; runtime worker, JSONL/SQLite fixture, concurrency, restart veya fault-injection çalıştırılmadı. |
| Sonuç | DLQ-001, QUEUE-001, WORKER-001 statik kanıtla doğrulandı; davranışsal kanıtlar Faz 8 test matrisi ve güvenli environment sonrasına taşındı. |
| Kanıt | SEC-001, OPS-001; DLQ-001, QUEUE-001, WORKER-001 |


## DEC-008 — Faz 8 collection ve coverage çalıştırma kararı

| Alan | Karar |
|---|---|
| Durum | Kabul edildi |
| Bağlam | Faz 1.5 `SEC-001`/`OPS-001` açık; test importları dotenv/global env/storage ve ML/provider bağımlılıkları taşıyabilir. |
| Karar | Test dosyaları, CI, config ve fixture'lar statik incelendi; pytest collection, test ve coverage çalıştırılmadı. |
| Sonuç | 819 sayısı source-level `test_*` sayımıdır, pytest collection sonucu değildir. Dynamic sonuçlar TEST-CAND-001 olarak ertelendi. |


## DEC-009 — Faz 9 DLQ remediation sınırı

| Alan | Karar |
|---|---|
| Bağlam | DLQ-001 doğrulanmış P0 kayıp yoluydu; Faz 1.5 gate runtime queue testini, Faz 9 kapsamı büyük queue yeniden tasarımını sınırlıyor. |
| Karar | Destructive clear kaldırıldı, tenant context ve selected-item acknowledgement eklendi. Cross-process lease/outcome/migration tasarımı bu dalgada yapılmadı. |
| Gerekçe | Kayıt işlenmeden silinmesini önleyen geri alınabilir küçük değişiklik; API/schema/migration değişikliği yok. |
| Sonuç | DLQ-001 Mitigated, Resolved değildir; production blocker açık kalır. |
## DEC-010 — Faz 10 performans kanıtı statik analiz ve ölçüm planı ile sınırlandı

| Alan | Karar |
|---|---|
| Durum | Kabul edildi |
| Bağlam | Faz 1.5 `SEC-001`/`OPS-001` açık; kullanıcı benchmark, load, soak, stres, model indirme ve Ollama müdahalesini yasaklıyor. |
| Karar | Dinamik performans testi çalıştırılmadı. Kapasite/SLO sonuçları kesin değer olarak raporlanmadı; sadece statik ölçeklenme riskleri ve izole ölçüm planı kaydedildi. |
| Sonuç | PERF-002 ve PERF-003 yüksek release blocker, PERF-004 orta N+1 bulgusu olarak eklendi. |
| Geri alma | İzole sentetik storage, mock adapter, tek/düşük concurrency ve açık RAM/disk bütçesiyle ölçüm yapıldığında bulgular sayısal kanıtla güncellenir. |

## DEC-011 — Faz 13 static-only ve audit persistence kararı

| Alan | Karar |
|---|---|
| Durum | Kabul edildi |
| Bağlam | SEC-002, DATA-005 ve DLQ-001 P0’ları; `.env` izolasyonu ve runtime baseline blocker’ları açıktır. Docker yok, artifact yok, API entry point worker’ları otomatik başlatır. |
| Karar | Dinamik rehearsal, API/worker/Docker/test/migration/backup/restore çalıştırılmadı. Faz 13 sonucu `STATIC_PLAN_ONLY` olarak kaydedildi. |
| Audit persistence | İlk `apply_patch` namespace hatası sonrasında, kullanıcı yetkisiyle mevcut içerik okunarak yalnız izinli audit dosyalarına kontrollü yazım yapıldı. |
| Sonuç | Faz 14’e başlanmadı; open P0’lar kapanana kadar dinamik rehearsal kapalıdır. |

## Pre-Phase-14 Audit Integrity Review — Faz 13.5 (tarihsel, Faz 11/12 persistence öncesi)

### Branch, commit ve karar kapsamı

- Branch: `audit/production-readiness`.
- HEAD: `c69d1f9c18844c393c26291db6c67628d82167f1`.
- Kaynak durumu: yalnız `mesa_memory/consolidation/loop.py` Faz 9 remediation diff’i; source-diff SHA-256 `a850a4ba450d16280347c26493f812c021542412ac245b1e94608703abbe621d`.
- Audit dokümanları commit edilmemiştir; karar bu HEAD + belirtilen kaynak diff’i + mevcut audit çalışma ağacına aittir. Korunan untracked kullanıcı yolları kapsama dahil değildir.

### Audit dosyası bütünlük tablosu

| Dosya | Var/boş | Son faz izi | Yapısal durum | Sonuç |
|---|---|---|---|---|
| README | Var / değil | Sistem faz sırası | Sağlam | Valid |
| CURRENT_PHASE | Var / değil | 13.5 | Faz 11/12 boşluğu artık açık | Incomplete |
| BASELINE | Var / değil | 1.5 | Tarihsel baseline; güncel dirty state değildir | Stale |
| INVENTORY | Var / değil | 8 | Faz 11/12 formal eki yok | Incomplete |
| SYSTEM_MAP | Var / değil | 10 | Faz 11/12 formal eki yok | Incomplete |
| DATA_FLOWS | Var / değil | 10 | Faz 11/12 formal eki yok | Incomplete |
| FINDINGS | Var / değil | 13.5 | DLQ-001 duplicate heading; Faz 11/12 yok | Incomplete |
| BUGS | Var / değil | 9 | DLQ doğru biçimde partial | Valid |
| BLOCKERS | Var / değil | 13.5 | Faz 11/12 teknik blocker kapsamı eksik | Incomplete |
| FIX_PLAN | Var / değil | 13.5 | Faz 11/12 planı eksik | Incomplete |
| TEST_MATRIX | Var / değil | 13.5 | Yanlış Passed sınıfları düzeltildi | Incomplete |
| COMMAND_LOG | Var / değil | 13.5 | Faz 11/12 komut izi yok | Incomplete |
| CHANGELOG_AUDIT | Var / değil | 13 | Faz 2–8, 10–12 ayrıntılı değişiklik izi eksik | Incomplete |
| DECISIONS | Var / değil | 13.5 | Bütünlük sonucu eklendi | Valid |
| DEFERRED | Var / değil | 13 | Ertelenen staging grubu mevcut | Valid |
| PRODUCTION_READINESS | Var / değil | 13.5 | Faz 11/12 alanları değerlendirilmemiş | Incomplete |

Hiçbir dosya missing, empty veya unreadable değildir. `git diff --check` temizdir; anormal küçülme/overwrite kanıtı yoktur.

### Faz kayıt, çıkış kriteri ve doğruluk matrisi

Puanlar production readiness puanı değil, audit yürütme kalitesidir. Zorunlu kriter sayıları Faz 13.5 promptundaki doğrulama boyutları üzerinden sayılmıştır.

| Faz | Zorunlu | Karşılanan | Karşılanmayan | Kanıtsız | Kapsam | Kanıt | Kayıt | Kural | Çıkış | Toplam | Doğru durum |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 0 | 6 | 6 | 0 | 0 | 2 | 2 | 2 | 2 | 2 | 10 | Tamamlandı / Güvenilir |
| 1 (1.5 dahil) | 7 | 5 | 2 | 0 | 2 | 1 | 2 | 1 | 1 | 7 | Kısmen tamamlandı |
| 2 | 6 | 6 | 0 | 0 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı; geç bulunan coverage gap sonucu bozmaz |
| 3 | 6 | 6 | 0 | 0 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı |
| 4 | 4 | 4 | 0 | 0 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı |
| 5 | 9 | 9 | 0 | 0 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı (statik) |
| 6 | 9 | 9 | 0 | 0 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı (statik) |
| 7 | 8 | 8 | 0 | 0 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı (statik) |
| 8 | 7 | 6 | 0 | 1 | 2 | 1 | 1 | 2 | 2 | 8 | Tamamlandı; gerçek collection/coverage gap belgeli |
| 9 | 7 | 4 | 2 | 1 | 1 | 1 | 2 | 1 | 1 | 6 | Kısmen tamamlandı; Fixed but not verified |
| 10 | 8 | 8 | 0 | 0 | 2 | 1 | 1 | 2 | 2 | 8 | Tamamlandı (statik); ölçüm deferred |
| 11 | 10 | 0 | 0 | 10 | 1 | 1 | 0 | 2 | 0 | 4 | Evidence exists, record missing |
| 12 | 9 | 0 | 0 | 9 | 1 | 1 | 0 | 2 | 0 | 4 | Evidence exists, record missing |
| 13 | 24 | 18 | 0 | 6 | 2 | 1 | 2 | 2 | 1 | 8 | Kısmen tamamlandı / `STATIC_PLAN_ONLY` |

Güvenilir (9–10): Faz 0, 2, 3, 4, 5, 6, 7. Kısmen güvenilir veya önemli boşluklu: Faz 1, 8, 9, 10, 13. Kayıt eksik/güvenilmez: Faz 11 ve Faz 12.

### Kritik kanıt seviyesi

| Bulgu | Seviye | Mevcut kanıt | Yeterlilik / eksik |
|---|---|---|---|
| SEC-002 | E1 | API key → caller-controlled agent session grant kod zinciri | Blocker için yeterli static kanıt; iki-principal E3 eksik |
| DATA-002 | E1 | DAO graph failure catch ve SQLite/vector devamı | Blocker için yeterli static kanıt; fault integration eksik |
| DATA-005 | E1 | migration boolean, transform/promotion ve WAL flush kodu | Blocker için yeterli static kanıt; crash/concurrency E3 eksik |
| DLQ-001 | E1 | Önceki kod kaydı + mevcut partial remediation diff’i | Kalan durable claim/outcome riski açık; runtime doğrulama eksik |
| TEST-001 | E1 | Test/CI kaynak eşlemesi ve eksik E2/E3 gate’ler | Release-gate blocker olarak yeterli; testlerin kendisi eksik |

### Çapraz dosya ve yanlış temsil sonucu

- FINDINGS ↔ BLOCKERS: bilinen beş P0 blocker’da görünür ve açık; DLQ-001 resolved değildir. Faz 11/12 eksikliği nedeniyle kapsam tam değildir.
- FINDINGS ↔ FIX_PLAN/TEST_MATRIX: mevcut P0/P1’lerin çoğu plan/test satırı taşır; Faz 11/12 için formal karşılık yoktur.
- TEST_MATRIX ↔ COMMAND_LOG: Faz 1’de 70 test iddiası log ile destekli; Faz 8 sayıları static ve collected olmadığı açık. Faz 9 “passing” artefaktı kayıp; sınıflandırma düzeltildi. Faz 13 Docker satırı `Static-only` yapıldı.
- CURRENT_PHASE ↔ CHANGELOG: Faz 13 uyumlu; Faz 11/12 her ikisinde de yok. CHANGELOG diğer bazı statik fazlarda da ayrıntı eksiktir.
- Duplicate/stale: DLQ-001 ikinci başlığı durum güncellemesidir; teknik duplicate değildir ama heading duplicate’tir. DLQ’nin pre-fix gerçek davranış metni tarihsel olup Faz 9 güncellemesiyle superseded edilmiştir. BASELINE clean-state kaydı tarihsel ancestor `8798abc`/başlangıç kaydıdır; güncel HEAD `c69d1f9c` onun descendant’ıdır.
- Çalıştırılmayan staging, Docker, backup/restore veya migration işlemi Passed olarak bırakılmadı.

### Faz 9 ve Faz 13 özel sonucu

- Faz 9: remediation diff’i mevcut; failing/passing source invariant ve `py_compile` loglanmış, fakat kalıcı invariant artefaktı ve pytest/runtime testi yok. Sonuç `Partially fixed / Fixed but not verified`.
- Faz 13: persistence recovery başarılı; STAGE-001 ve CONFIG-002 kayıtlı; sonuç `STATIC_PLAN_ONLY`, giriş `BLOCKED`, dinamik testler `Blocked/Not tested`; runtime kalıntısı yok.

### Fazlar arası geriye etki

- STAGE-001, Faz 2’deki ARCH-001 process modeli ve Faz 7 worker envanteriyle uyumludur; önceki fazları geçersiz kılmaz, Faz 12’nin eksik formal kaydını büyütür.
- CONFIG-002, Faz 1.5 SEC-001’i yeniden doğrular; Faz 1 güvenli config iddiası Phase 1.5 ile superseded edilmiştir.
- Worker-unaware `/health/init`, WORKER-001 ile uyumludur; Faz 7 kapsamını geçersiz kılmaz, Faz 12 readiness kaydının eksikliğini gösterir.
- `.env` izolasyon açığı Faz 1.5 ve Faz 13’te tutarlı biçimde blocker’dır.

### Faz 14 giriş kriteri ve karar

Faz 13 sonucu disktedir ve kritik kod kayıtları HEAD ile uyumludur. Ancak Faz 11/12 zorunlu kayıtları yoktur, comprehensive blocker/P1 sayısı güvenilir değildir ve Faz 9 yalnız partially verified durumdadır. Bu nedenle tek giriş sonucu:

`NOT_READY_FOR_PHASE_14`

Bu, production readiness kararı değildir. Faz 11/12 persistence ve canonical durum sayımı tamamlandıktan sonra Faz 13.5 yeniden doğrulanmalıdır.


## DEC-012 — Eksik Faz 11/12 kayıtlarının static-only olarak canonicallaştırılması

| Alan | Karar |
|---|---|
| Tarih | 2026-07-19 |
| Bağlam | Faz 11/12 analiz sonuçları daha önce audit dosyalarına formal olarak persist edilmemişti; Faz 13.5 bunu audit bütünlüğü riski olarak kaydetti. |
| Karar | Mevcut statik kanıtlar Faz 11/12 formal kayıtlarına işlendi; runtime yapılmamış alanlar `Static-only`, `Not tested`, `Blocked` veya `Evidence missing` olarak kaldı. |
| Sayım | Canonical teknik açık kayıt: 9 P0, 40 P1, 43 release blocker. DLQ-001 ikinci heading’i noncanonical duplicate’tir. |
| Sınır | Bu kayıt Faz 14 giriş kararı veya production readiness kararı değildir; Faz 13.5 tekrar doğrulaması gerekir. |
| Geri alma | Audit ekleri tarihçeli olduğundan silinmez; yanlış sınıflandırma yeni düzeltme kaydıyla supersede edilir. |


## DEC-013 — Faz 13.5 audit bütünlüğü yeniden doğrulama sonucu

### Kapsam ve çalışma ağacı

- Branch: `audit/production-readiness`; HEAD: `c69d1f9c18844c393c26291db6c67628d82167f1`.
- Karar kapsamı: bu HEAD, Faz 9 `mesa_memory/consolidation/loop.py` çalışma ağacı diff’i (SHA-256 `a850a4ba450d16280347c26493f812c021542412ac245b1e94608703abbe621d`) ve uncommitted audit kayıtları. `AGENTS.md`, `cold_path_trace.txt`, `dummy.txt` ve `results/mesa_client/…` korunmuş kullanıcı dosyaları kapsam dışıdır.
- 16 audit dosyasının tamamı mevcut, okunabilir ve boş değildir. `git diff --check` geçti; corruption, yarım yazım veya anormal küçülme kanıtı yoktur.

### Audit dosyası durumu

| Dosya grubu | Durum | Not |
|---|---|---|
| README, BASELINE, INVENTORY, SYSTEM_MAP, DATA_FLOWS, BUGS, DEFERRED | Valid | Tarihsel faz odaklı dosyalarda sonraki faz başlığı olmaması tek başına eksiklik değildir |
| CURRENT_PHASE, FINDINGS, BLOCKERS, FIX_PLAN, TEST_MATRIX, COMMAND_LOG, CHANGELOG_AUDIT, DECISIONS, PRODUCTION_READINESS | Valid after revalidation | Tarihsel Faz 11/12 eksik sonucu superseded edildi; güncel canonical durum eklendi |

### Faz doğruluk ve çıkış kriteri matrisi

Puanlar production-readiness puanı değildir. `Kanıt` dinamik başarıyı değil, fazın kendi kapsamına uygun kanıt kalitesini ölçer.

| Faz | Kapsam | Kanıt | Kayıt | Kural | Çıkış | Toplam | Doğru durum / gerekçe |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 2 | 2 | 2 | 2 | 2 | 10 | Tamamlandı; repo/entry-point/stack/CI envanteri kayıtlı |
| 1 (1.5 dahil) | 2 | 1 | 2 | 1 | 1 | 7 | Kısmen tamamlandı; ENV/BOOT/SEC-001/OPS-001 kapısı açık |
| 2 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı; STAGE-001 sonradan bulunan coverage gap, process modeliyle uyumlu |
| 3 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı; P0 akış bağlantıları kayıtlı |
| 4 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı; sembollü business-invariant kanıtı var |
| 5 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı (static); SEC-002 açık ve blocker’da |
| 6 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı (static); DATA-005 açık ve blocker’da |
| 7 | 2 | 2 | 1 | 2 | 2 | 9 | Tamamlandı (static); DLQ/health/worker kapsamı kayıtlı |
| 8 | 2 | 1 | 1 | 2 | 2 | 8 | Tamamlandı; collection/coverage dynamic değil, doğru sınıflı |
| 9 | 1 | 1 | 2 | 1 | 1 | 6 | Kısmen tamamlandı; DLQ diff var, runtime/pytest kanıtı yok |
| 10 | 2 | 1 | 1 | 2 | 2 | 8 | Tamamlandı (static); ölçümler deferred |
| 11 | 2 | 1 | 2 | 2 | 1 | 8 | Kısmen tamamlandı / Static-only / Blocked |
| 12 | 2 | 1 | 2 | 2 | 1 | 8 | Kısmen tamamlandı / Static-only / Blocked |
| 13 | 2 | 1 | 2 | 2 | 1 | 8 | Kısmen tamamlandı / `STATIC_PLAN_ONLY` |

Güvenilir fazlar: 0, 2–7. Kısmen güvenilir: 1, 8, 10–13. Kanıtı önemli ölçüde yetersiz faz: 9. Yanlış “tamamlandı” olarak bırakılmış faz yoktur.

### Kritik iddia ve kanıt seviyesi

| Bulgu / iddia | Seviye | Hedefli kanıt | Sonuç |
|---|---|---|---|
| SEC-002 | E1 | `server.py` global API key; `router.py:start_session` caller payload agent için grant | Audit kaydıyla uyumlu, açık P0 |
| DATA-005 | E1 | `MemoryDAO.align_memory_space` boolean flag, external WAL flush ve unlock akışı | Audit kaydıyla uyumlu, açık P0 |
| DLQ-001 | E1 | Faz 9 diff: no-clear, `agent_id`, selected atomic acknowledgement; claim/outcome eksikleri sürüyor | Partially fixed / Fixed but not verified doğru |
| STAGE-001 / CONFIG-002 | E1 | `server.py` unconditional worker taskleri; `config.py` module-level `load_dotenv()` | Açık P1 kayıtlarıyla uyumlu |
| `/health/init` | E1 | `is_ready` + DAO health kontrolü; worker liveness/lag kontrolü yok | HEALTH-001/WORKER-001 ile uyumlu |
| API-only worker-disable profile | E1 | Hedefli config/source taramasında etkin flag bulunmadı | STAGE-001 ile uyumlu |

Açık P0’ların tümü E1 veya üstü kanıt taşır; yalnız dokümantasyon iddiasına dayalı kritik blocker yoktur.

### Tutarlılık, yanlış temsil ve geriye etki

- `FINDINGS` canonical P0’ların tümü `BLOCKERS` içinde; canonical sayım 9 P0, 40 P1, 43 teknik release blocker. `DLQ-001` ikinci heading’i noncanonical duplicate’tir.
- `FIX_PLAN` ve `TEST_MATRIX`, açık teknik riskler için remediation/verification yollarını taşır; `TEST-001` Faz 8 minimum production gate kapsamına açıkça bağlandı.
- Tarihsel Faz 13.5 satırlarındaki 5/30, Faz 11/12 missing ve `NOT_READY_FOR_PHASE_14` ifadeleri persistence öncesi durumu anlatır; bu DEC-013 ile superseded edilmiştir. Çalıştırılmayan test/Docker/migration/backup/restore runtime başarı olarak sınıflandırılmamıştır.
- STAGE-001 Faz 2/7 process modelini, CONFIG-002 Faz 1.5’i, worker-aware health boşluğu Faz 7/12’yi ve dotenv açığı Faz 1.5/13’ü destekler; önceki fazların statik sonuçlarını geçersiz kılmaz, coverage gap olarak kaydedilir.

### Faz 14 audit giriş sonucu

Kayıtlar tam, Faz 13 diskte, canonical açık sayılar güvenilir ve karar çalışma ağacı durumuna bağlanabilir. Tek açık audit bütünlüğü riski EVIDENCE-001’dir; Faz 9’un doğrulanmadığı zaten `Fixed but not verified` olarak gösterildiğinden Faz 14 kararını yanıltmaz. Bu nedenle tek giriş sonucu:

`READY_FOR_PHASE_14_WITH_DOCUMENTED_GAPS`

Bu Faz 14’ün kendisi veya production-readiness kararı değildir. Faz 14 öncesinde ek zorunlu audit kaydı düzeltmesi yoktur.


## DEC-014 — Nihai Production Readiness Kararı

| Alan | Değer |
|---|---|
| Karar | `NO_GO` |
| Tarih | 2026-07-19 |
| Branch | `audit/production-readiness` |
| Commit | `c69d1f9c18844c393c26291db6c67628d82167f1` + Faz 9 source-diff SHA-256 `a850a4ba450d16280347c26493f812c021542412ac245b1e94608703abbe621d` |
| Rehearsal sonucu | `STATIC_PLAN_ONLY` / Blocked |
| Açık Kritik | 9 |
| Açık Yüksek | 40 |
| Açık blocker | 43 teknik release blocker |
| Temel gerekçe | Açık tenant authorization, triple-store/data-loss, worker/DLQ, migration/DR, Docker persistence ve kritik test/staging blocker’ları |
| Zorunlu sonraki adım | Kontrollü remediation; E2/E3 regression; migration/DR ve Docker rehearsal; Faz 13 tekrarı |
| Kararı değiştirecek kanıt | 9 P0 ve release-blocking P1 kök nedenlerinin passing regression ile kapanması; iki-tenant E3 smoke; crash/restart integrity; full restore/reconcile; immutable artifact Docker rehearsal; en az `REHEARSAL_PASS_WITH_LIMITATIONS` |

## DEC-AUDIT-DOC — Canonical audit schema and historical record policy

| Alan | Değer |
|---|---|
| Bağlam | Faz 14 final `NO_GO` kararından sonra audit kayıtlarında şema, kanıt seviyesi, sahiplik ve historical/canonical ayrımı tutarlı gösterilmelidir. |
| Karar | Kanonik kaynak Faz 14 final bölümleri ve Faz 13.5 canonical indeksidir. Historical bölümler silinmez; `Superseded`/historical olarak açıkça etiketlenir. |
| Korunan gerçekler | Branch `audit/production-readiness`; HEAD `c69d1f9c18844c393c26291db6c67628d82167f1`; kapsam HEAD + uncommitted Faz 9 source diff + audit worktree; `NO_GO`; 9 P0, 40 P1, 43 teknik release blocker. |
| Kanıt standardı | E1 static call/import veya deterministic source invariant; E2 unit/component; E3 integration/runtime; E4 staging/rehearsal. E1 runtime doğrulaması değildir. |
| Sahiplik | `AGENTS.md` önceden mevcut untracked kullanıcı dosyasıdır; audit-owned değildir ve bu kayıtlar onun değiştirildiğini ima etmez. |
| Sonuç | Faz 9 `Partially fixed / Fixed but not verified`, Faz 13 `STATIC_PLAN_ONLY` / `BLOCKED` / Kısmen tamamlandı ve Faz 14 `NO_GO` değişmeden korunur. Duplicate upload canonical repository dosyası sayılmaz. |

## DEC-REM-001 — Persistent wave-based remediation orchestration

| Alan | Karar |
|---|---|
| Orkestrasyon | Remediation wave tabanlı yürütülür. |
| Varsayılan mod | `supervised`; her wave sonunda kullanıcı/runner checkpoint’inde durur. |
| Persistence | State ve evidence atomik persist edilir. |
| Kanıt | Static fix `Verified resolved` sayılmaz. |
| Karar sınırı | GO zorlanmaz; public/schema/security kararlarında otomasyon durur ve karar ister. |
| Kurulum etkisi | Bu yalnız altyapı kurulumudur; hiçbir wave çalıştırılmaz ve Faz 14 `NO_GO` değişmez. |

## DEC-REM-002 — WAVE-000 canonical identity ve tenant contract

- Tarih: 2026-07-19
- Run ID: `rem-20260719-030742`
- Karar: Principal türleri `USER`, `SERVICE`, `ADMIN`, `INTERNAL_WORKER` olacaktır. Authorization server-side principal-to-agent mapping ile yapılır: credential → principal_id → principal_type → principal status → explicit agent allowlist → permission set. Request'teki `agent_id`, `session_id`, `tenant_id`, `role` veya `principal_type` yetki üretmez.
- İzinler: `READ`, `WRITE`, `SESSION_CREATE`, `SESSION_READ`, `SESSION_UPDATE`, `STATUS_READ`, `PURGE`, `ADMIN`; hiçbiri diğerini örtük vermez. Varsayılan fail-closed, explicit allowlist ve least privilege'dır.
- Session ownership: Server `session_id`, `agent_id`, `owner_principal_id`, `created_by_principal_id`, timestamps ve status saklar; request identifiers erişim vermez.
- Özel principal'ler: ADMIN explicit privileged permission ister; SERVICE default tek-agent scope ve explicit multi-agent allowlist kullanır; INTERNAL_WORKER dış HTTP caller tarafından seçilemez ve queue/job agent context'iyle çalışır.
- Legacy/SDK/MCP: observe → principal provisioning → mapped dual acceptance → deprecation → removal; legacy key sınırsız agent erişimi vermez. SDK/MCP agent ID yalnız hedeftir, MCP argümanı tenant yetkisi oluşturmaz.
- Sonuç: WAVE-000 `VERIFIED_COMPLETE — DECISION RECORDED`; WAVE-001 bu sözleşmenin fail-closed implementation ve E2/E3 kanıtı için açılır. Canonical sayılar ve `NO_GO` değişmez.

## DEC-REM-003 — WAVE-001 clean restart checkpoint classification

| Alan | Karar |
|---|---|
| Run | `rem-20260719-144002-W001-restart` |
| Temel | Önceki WAVE-001 evidence tarihsel kabul edildi; mevcut source/test hashleri yeniden kontrol edildi ve yeni E2 test kanıtı üretildi. |
| Sonuç | `/session/start` principal mapping gate’i E2’de doğrulandı; `SEC-002` `Fixed but not verified` olarak açık kalır. |
| Neden kapanmadı | E3 isolated runtime, SDK/MCP contract, lifecycle/provisioning ve cross-endpoint principal authorization kanıtı yoktur. |
| Etki | Açık P0=9, P1=40, teknik release blocker=43; Faz 14 `NO_GO` değişmez. |
| Sonraki adım | WAVE-002 başlatılmadan önce WAVE-001 verification gap’leri açık şekilde korunur; no GO inference. |

## DEC-REM-004 — DATA-001 SQLite canonical purge coordinator

- Bağlam: Purge SQLite/vector ile sınırlıydı; Kuzu retention ayrışıyor ve partial failure başarı gibi görünebiliyordu.
- Onaylanan seçim: SQLite canonical mutation coordinator’dır. `purge_journal` exact `purge_id`, principal/agent/session scope, target IDs, state, per-store sonuç, retry, error ve idempotency key saklar. Kuzu/vector canonical değildir.
- Lifecycle: `PREPARED → TOMBSTONED → KUZU_APPLIED → VECTOR_APPLIED → VERIFIED → FINALIZED`; hata `RETRY_PENDING`, `COMPENSATION_REQUIRED`, `BLOCKED`, `FAILED_SAFE` ile görünürdür.
- Güvenlik: router active principal ve explicit target-agent `PURGE` izni ister; empty/wildcard scope reddedilir.
- Geri alma: yalnız downstream uygulanmadan önce, journal scope ve SQLite canonical payload ile; finalization sonrası otomatik restore yasaktır. DR restore operator-controlled purge-ledger reconciliation gerektirir.
- Kanıt: WAVE-002 E2 synthetic migration/lifecycle suite geçti. E3 yoktur; `DATA-001` kapanmaz.

## DEC-REM-005 — WAVE-003 durable claim and WAL protocol

- Karar: `raw_logs` ve `lancedb_wal` durable work kayıtları SQLite’da claim token, owner, expiry ve attempt state ile sahiplenilir. Terminal/ACK işlemleri owner+token ile fence edilir; expired lease yalnız non-terminal işi tekrar alınabilir yapar.
- WAL: external vector I/O SQLite write transaction dışında yapılır; idempotent upsert başarıdan sonra tekil ACK verilir, hata kaydı PENDING’e görünür biçimde bırakır. ACKED kayıt yeniden claim edilmez.
- Alignment: VectorEngine mutation lock snapshot, transform, verify ve promotion boyunca tutulur.
- Sınır: Bu WAVE-003 E2 tasarım/implementation kararıdır; multi-process alignment owner lease, real-store crash/restart ve worker side-effect exact-once E3 ile ayrıca doğrulanmalıdır.

## DEC-REM-006 — WAVE-004 DLQ file queue boundary

Configured JSONL DLQ korunur; external broker eklenmez. File-lock protected durable owner/token/lease/attempt state uygulanır. Opaque `run_batch` dönüşü başarı kabul edilmez; per-record completion receipt olmadan item ACK edilmez. Raw-log dispatcher, admission policy ve worker readiness ayrı açık implementation kararlarıdır.

## DEC-REM-007 — WAVE-004 bounded sub-wave execution

WAVE-004 material scope user-approved olarak WAVE-004A (durable dispatch), 004B (admission/backpressure), 004C (supervision/readiness) ve 004D (completion/DLQ E3) şeklinde sıralı ayrıldı. Coordinator mevcut SQLite katmanıdır; external broker yoktur. Bir alt wave E2 checkpointinden önce sonraki başlamaz; E3 eksikliği 004D’de açık planlanırsa FBNV ile devam edebilir.

## DEC-REM-008 — Queue admission, capacity and overload contract

| Alan | Karar |
|---|---|
| Bağlam | WAVE-004B için public admission/backpressure politikası kullanıcı tarafından onaylandı. |
| Karar | Bounded, server-side ve fail-closed admission; global/per-tenant kayıt ve byte bütçeleri ile in-flight/retry bütçeleri tek typed policy nesnesinden uygulanır. |
| Overload | Geçici kapasite aşımı `503` + 1..60 saniye `Retry-After` (varsayılan 5); oversized record `413`; queue coordinator unavailable `503 BLOCKED`. |
| Dayanıklılık | SQLite canonical coordinator mevcut basınç ölçümü, limit kontrolü ve durable enqueue işlemini tek transaction’da yapar. Bellek veya sınırsız dosya fallback’i yoktur. |
| Sınır | `DEFERRED` yalnız raw-log + dispatch intent + queue kaydı + receipt durable ise döner. Authz admission’dan önce değerlendirilir. |
| Kanıt hedefi | E2 count/byte/tenant/in-flight/retry/concurrency/HTTP; profile izolasyonu yoksa E3 WAVE-004D/WAVE-005’e bağlı kalır. |
| Sayım etkisi | Canonical 9 P0, 40 P1, 43 teknik blocker ve `NO_GO` değişmez. |

## DEC-REM-009 — WAVE-005 explicit runtime profile boundary

Test-isolated profile production dotenv search, model/provider use and non-lab storage is fail-closed. API-only does not start workers; worker-only uses an explicit non-listening process boundary. Evidence supports safe verification only, not `GO`.


## DEC-REM-010 — WAVE-001-V session lifecycle surface applicability

| Alan | Karar |
|---|---|
| Kanıt | README API tablosu ve OpenAPI yalnız `POST start`, `GET context`, `POST end` verir; SDK lifecycle method sunmaz; MCP yalnız memory insert/search/purge tool’ları sunar. |
| Session status/list/update/finalize | `ABSENT_BY_DESIGN`, release matrixinde NOT_APPLICABLE. `/status/{log_id}` raw-log cold-path durumudur, session status değildir. |
| End semantics | Public contract final consolidation vaat eder; implementation yalnız log yazdığından `FLOW-002` REQUIRED_BUT_MISSING olarak açık kalır. Yeni duplicate finalize endpoint tasarlanmaz. |
| Sonuç | W1 route yüzeyi için yeni endpoint/ADR gerekmez; `FLOW-002` için ayrı finalization tasarım kararı gereklidir. `NO_GO` değişmez. |


## DEC-REM-011 — Downstream failure, stale-fence, consumer receipt and trusted-root policy

- Bağlam: `DEC-REM-009` zaten WAVE-005 runtime profile sınırı için kullanılmıştır. WAVE-003 E2 WAL claim/ACK modeli gerçek downstream hata, stale-fence ve reconciliation matrisini kapsamaz; WAVE-004 E2 SQLite receipt ise JSONL consumer/root containment E3’ünü kapsamaz.
- Onaylanan seçim: SQLite/WAL canonical coordinator; LanceDB ve Kùzu ayrı downstream projection’dır. Her mutation stable kimlik/idempotency, ayrı durable projection durumu ve claim-fence taşır. ACK yalnız reconciliation sonrası verilir; retry yalnız eksik/doğrulanmamış adıma ve bounded bütçeye uygulanır.
- Fence: Lease sonrası yeni claim yeni token üretir. Eski token ACK/NACK/projection/receipt/terminal state yazamaz; conflict/fenced-out döner ve restart sonrası da geçerlidir.
- Reconciliation: `ALIGNED`, missing/extra projection, payload/version, scope mismatch ve unknown sonuçları durable kaydedilir. Scope mismatch, extra veya unknown otomatik başarı/onarım değildir.
- Queue: Per-record durable completion receipt side effect doğrulamasından sonra, fenced ACK’den önce yazılır. Receipt/ACK ayrışması restart reconciliation ile açıkça işlenir; batch sonucu per-record failure’ı maskelemez.
- Root policy: JSONL queue, quarantine ve receipt path’leri explicit trusted root içinde fail-closed containment ile çalışır; `/`, home, repo, relative traversal, symlink root/parent/target ve caller-controlled absolute path reddedilir. Test/runtime root `/storage/mesa-lab`; production root yalnız explicit validated config ile seçilebilir.
- Sınırlar: MCP core API/SDK için optionaldır (`NOT_APPLICABLE_FOR_CORE_RELEASE` veya `OPTIONAL_FEATURE_BLOCKED`); `FLOW-002` ayrı açık kalır. Docker, provider/model, destructive migration ve kullanıcı verisi kapsam dışıdır.
- Kabul kapısı: W3 gerçek LanceDB/Kùzu failure + stale-fence + reconciliation + restart; W4 receipt/ACK crash + stale fence + poison/partial tail + root policy E3’leri geçmeden canonical finding kapanmaz.

## DEC-REM-012 — Master closure final classification

- Campaign A–E implementation closure tamamlandı; Docker daemon, harici CI ve clean full-suite bağımsız doğrulama kapıları uygulama tamamlanmasından ayrı tutulur.
- `FIXED_NOT_VERIFIED` finding açık risk ve blocker olarak sayılır; kanıtsız kapanış yapılmaz.
- Faz 13 tarihsel/canonical `STATIC_PLAN_ONLY` sonucu geriye dönük değiştirilmez. Master closure model-disabled lab runtime rehearsal ayrı E3 kanıtıdır.
- Final canonical teknik set: 56 finding; 28 resolved, 28 açık; açık P0=4, P1=20, P2=4; 21 release blocker; Faz 14 `NO_GO`.
- Program sonucu `IMPLEMENTATION_COMPLETE_WITH_EXTERNAL_VERIFICATION_PENDING`; bu bir release `GO` kararı değildir.
