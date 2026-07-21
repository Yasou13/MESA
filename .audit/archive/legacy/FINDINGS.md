# Teknik Bulgular

Kesin hata kabulü için somut kanıt gerekir. Kanıtlanmamış konular `Şüpheli` veya uygun başka kanıt durumuyla kaydedilir. Bulgu ID’leri tekrar kullanılmaz.

## Önem tanımları

| Önem | Tanım |
|---|---|
| Kritik | Güvenlik, veri kaybı, erişilemezlik veya release’i doğrudan engelleyen yüksek etkili doğrulanmış sorun |
| Yüksek | Ana işlevi, veri doğruluğunu veya güvenilirliği ciddi etkileyen sorun |
| Orta | Sınırlı kapsamlı ancak düzeltilmesi gereken risk veya kusur |
| Düşük | Düşük etkili iyileştirme veya kenar durum |
| Bilgi | Karar veya izleme için kayıt; doğrudan kusur değildir |

## Kanıt durumları

`Doğrulandı`, `Kısmen doğrulandı`, `Şüpheli`, `Tekrar üretilemedi`, `Yetersiz kanıt`, `Yanlış alarm`, `Düzeltildi`, `Doğrulandı ve kapatıldı`. Kanonik operational durumlar için aşağıdaki sözlük kullanılır.

## Bulgu şablonu

### XXX-001 — Kısa başlık

| Alan | Değer |
|---|---|
| Durum | Henüz kaydedilmedi |
| Önem | — |
| Öncelik | — |
| Kategori | — |
| Release blocker | — |
| Dosya yolu | — |
| Satır veya sembol referansı | — |
| Beklenen davranış | — |
| Gerçek davranış | — |
| Somut kanıt | — |
| Tekrar üretme adımları | — |
| Etki | — |
| Kök neden | — |
| Önerilen düzeltme | — |
| Gerekli regresyon testi | — |
| Tahmini efor | — |
| Bağımlılıklar | — |

## Kayıtlar

Faz 1 doğrulanmış bulguları aşağıdadır.


### ENV-001 — Mevcut virtual environment kullanılabilir değil

| Alan | Değer |
|---|---|
| Durum | Doğrulandı |
| Önem | Yüksek |
| Öncelik | P1 |
| Kategori | Ortam / dependency |
| Release blocker | Evet — bu makinede güvenilir kurulum ve API baseline’ını engelliyor |
| Dosya yolu | venv/bin/python; venv/lib/python3.10/ |
| Satır veya sembol referansı | Komut ortamı |
| Beklenen davranış | Mevcut venv, pip ve proje core bağımlılıklarını sağlamalıdır |
| Gerçek davranış | venv/bin/python Python 3.13.11 çalıştırıyor; pip, FastAPI ve diğer core modüller bulunamadı |
| Somut kanıt | venv/bin/python -m pip ve FastAPI importu exit 1 verdi |
| Tekrar üretme adımları | venv/bin/python -m pip --version; venv/bin/python -c 'import fastapi' |
| Etki | Mevcut repo venv’iyle install/test/startup baseline alınamıyor |
| Kök neden | Kısmen doğrulandı: venv içeriği Python 3.10 ağacı iken executable Python 3.13; oluşum/geçiş geçmişi bilinmiyor |
| Önerilen düzeltme | Sonraki yetkili aşamada, yeterli disk alanlı ve desteklenen Python sürümlü temiz yerel venv kurulum yolunu doğrula |
| Gerekli regresyon testi | CI ile eşdeğer Python sürümünde pip check, core import, güvenli startup smoke |
| Tahmini efor | Küçük-Orta |
| Bağımlılıklar | Yerel disk, Python runtime, paket indeksi erişimi |

### BOOT-001 — İzole API readiness baseline’ı tamamlanamadı

| Alan | Değer |
|---|---|
| Durum | Doğrulandı |
| Önem | Yüksek |
| Öncelik | P1 |
| Kategori | Boot / dependency |
| Release blocker | Evet — mevcut Faz 1 ortamında health ve smoke testi yapılamıyor |
| Dosya yolu | mesa_memory/api/server.py; /tmp/mesa_phase1_venv |
| Satır veya sembol referansı | FastAPI lifespan; VectorEngine başlatma zinciri |
| Beklenen davranış | Sentetik key/mock provider ve izole storage ile API ready durumuna gelmeli |
| Gerçek davranış | Uvicorn process başladı, SQLite migration’ları çalıştı, fakat ready olmadan exit code 3 ile kapandı |
| Somut kanıt | İzole uvicorn komutu exit 3; lancedb importu PackageNotFoundError ile exit 1 |
| Tekrar üretme adımları | env -i + mock provider + /tmp storage ile uvicorn; ardından import lancedb |
| Etki | Health, SDK smoke, worker ve restart kontrolü alınamadı |
| Kök neden | Doğrulandı: kesintiye uğramış izole kurulumda LanceDB distribution metadata’sı yok. /tmp kurulum sırasında %99 doldu; disk baskısı kurulum kesintisinin güçlü adayıdır |
| Önerilen düzeltme | Yeterli alanı olan izole ortamda doğrulanmış kurulum komutunu tamamla; ardından aynı sentetik/mock startup kontrolünü yeniden çalıştır |
| Gerekli regresyon testi | İzole API startup → /health/init → kontrollü shutdown/restart |
| Tahmini efor | Orta |
| Bağımlılıklar | ENV-001, yeterli geçici disk alanı, LanceDB’nin tam kurulumu |


### SEC-001 — Faz 1 işlemleri gerçek `.env` dosyasından izole değildi

| Alan | Değer |
|---|---|
| Durum | Doğrulandı |
| Önem / öncelik / kategori | Yüksek / P1 / Güvenlik-izolasyon |
| Release blocker | Evet — güvenli baseline’ın gerçek credential yapılandırmasından ayrıldığı doğrulanamıyor |
| Dosya yolu / sembol | `mesa_memory/config.py` modül seviyesi `load_dotenv()`; `mesa_memory/adapter/factory.py`; `tests/test_config.py`; `tests/test_config_edge_cases.py` |
| Beklenen / gerçek davranış | Subprocess yalnız sentetik environment kullanmalıydı; config importu koşulsuz dotenv yüklemesi yaptı ve root `.env` mevcuttu |
| Somut kanıt / tekrar üretme | API → `AdapterFactory` → config ve test import zinciri; yetkili sonraki aşamada secret içermeyen fixture ile ayrı subprocess’te dotenv etkisini doğrula |
| Etki / kök neden | Eksik environment anahtarları gerçek `.env` içinden proses ortamına yüklenebilir; `env -i` modül seviyesindeki dotenv yüklemesini engellemez |
| Önerilen düzeltme / regresyon testi | Otomatik dotenv yüklemesini kaldır veya açık yükleme sınırı getir; sentinel fixture ile root `.env` değerlerinin environment’a eklenmediğini doğrula |
| Efor / bağımlılıklar | Küçük-Orta / config tasarımı için kullanıcı onayı ve güvenli sandbox |

### OPS-001 — Beklenen güvenli dependency kurulum manifesti yok ve Faz 1 yöntemi kaynak sınırını aştı

| Alan | Değer |
|---|---|
| Durum | Doğrulandı |
| Önem / öncelik / kategori | Yüksek / P1 / Operasyon-dependency-kaynak sınırı |
| Release blocker | Evet — güvenli, tekrarlanabilir Faz 1 dependency baseline’ı oluşturulamıyor |
| Dosya yolu / sembol | Repository kökü; `pyproject.toml`; `.audit/COMMAND_LOG.md` Faz 1 kurulum kaydı |
| Beklenen / gerçek davranış | `pip install -r requirements-core.txt` bekleniyordu; dosya yok ve bunun yerine `pip install -e '.[dev,adapters]'` denendi |
| Somut kanıt / tekrar üretme | `git ls-files` yalnız `pyproject.toml` ile benchmark alt-projesi requirements dosyalarını döndürür; manifest yoksa kurulum başlatma |
| Etki / kök neden | `/tmp` %99 doldu, metadata eksik kaldı, health/smoke tamamlanamadı; onaylı core manifest yok ve alternatif dev/adapters setini kapsadı |
| Önerilen düzeltme / regresyon testi | Minimum-core manifest/yol ve disk bütçesi onaylanmalı; temiz ortamda `pip check`, core import, kontrollü health smoke çalışmalı |
| Efor / bağımlılıklar | Orta / manifest kararı, yeterli geçici disk, kullanıcı onayı |

### OPS-002 — Faz 1 çalışma kanıtı bağımsız tekrar üretim için yeterince ayrıntılı değil

| Alan | Değer |
|---|---|
| Durum | Doğrulandı |
| Önem / öncelik / kategori | Orta / P2 / Audit-operasyonel izlenebilirlik |
| Release blocker | Hayır — ürün davranışını değiştirmez, ancak Faz 1 güvenlik kanıtını zayıflatır |
| Dosya yolu / sembol | `.audit/COMMAND_LOG.md`; `/tmp/mesa_phase1_venv`; `/tmp/mesa_phase1_storage` |
| Beklenen / gerçek davranış | Tam maskeli komut, env sınırı, çalışma dizini ve sonuç kaydı bekleniyordu; birden çok kayıt özet ad içeriyor ve geçici yollar artık yok |
| Somut kanıt / tekrar üretme | Komut günlüğü ve iki yolun yokluğu; audit kaydından bağımsız aynı ortamı yeniden kurmaya çalış |
| Etki / kök neden | Mock/storage/shutdown ayrıntıları yeniden doğrulanamaz; command-log şablonu ve artefakt yaşam döngüsü uygulanmamış |
| Önerilen düzeltme / regresyon testi | Sonraki çalıştırmada tam maskeli komut, env allowlist, storage yolu, PID/timeout ve cleanup sonucu kaydedilmeli; audit prosedür kontrolü uygulanmalı |
| Efor / bağımlılıklar | Küçük / audit prosedürü disiplini |


### ARCH-001 — Dokümante edilen worker process izolasyonu koddaki process modeliyle uyuşmuyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı / Yüksek / P1 |
| Kategori / release blocker | Mimari-dokümantasyon / Hayır — gerçek izolasyon davranışı Faz 7’de runtime doğrulama ister |
| Dosya ve sembol | `ARCHITECTURE.md:12`; `mesa_memory/api/server.py:174-324`; `Dockerfile:CMD` |
| Doküman iddiası | API request handling ile background maintenance arasında strict process-level isolation |
| Kod gerçeği | Tüm worker’lar API lifespan içinde `asyncio.create_task` veya aynı process task olarak başlar; container tek uvicorn process başlatır |
| Etki | Arıza, kaynak ve shutdown sınırları process yerine shared event loop üzerinden değerlendirilmelidir |
| Sonraki doğrulama | Faz 7 task lifecycle, cancellation, starvation ve instance davranışı |

### ARCH-002 — Production lifecycle tüm başlatılan kaynakları simetrik kapatmıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı / Orta / P2 |
| Kategori / release blocker | Mimari lifecycle / Hayır |
| Dosya ve sembol | `mesa_memory/api/server.py:215-257,334-407`; `mesa_storage/vector_engine.py:995-1011` |
| Doküman iddiası | Ready sonrası worker/storage lifecycle yönetimi |
| Kod gerçeği | `VectorEngine.close()` shutdown’da çağrılmıyor; `consolidation_task`, `tier3_task`, `dlq_task` oluşturulup açıkça cancel/await edilmiyor |
| Etki | Executor/task kaynaklarının controlled shutdown’da kalması runtime ile ölçülmelidir |
| Sonraki doğrulama | Faz 7 shutdown leak/cancellation; Faz 12 container termination |

### ARCH-003 — Hot/cold path configured storage sınırı dışında CWD debug dosyalarına yazıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı / Yüksek / P1 |
| Kategori / release blocker | Mimari persistence sınırı / Evet — API çalışma yoluna kontrolsüz runtime yazımı beklenen storage sahipliğini bozar |
| Dosya ve sembol | `mesa_api/router.py:insert_memory` `dummy_task`; `mesa_workers/ingestion_worker.py:process_cold_path` |
| Doküman iddiası | Hot path raw-log/DAO storage, cold path configured persistence üzerinden ilerler |
| Kod gerçeği | Router `dummy.txt` yazar; cold path `cold_path_trace.txt` dosyasına tekrar eden append yapar |
| Etki | API request/cold-path çalışma dizininde runtime state üretir; deployment ve veri sahipliği modeliyle çelişir |
| Sonraki doğrulama | Faz 4 iş mantığı ve Faz 12 container filesystem davranışı; güvenli regresyon testi |

### ARCH-004 — MCP `get_stats` tool’u SDK/REST zincirini ve storage sınırını atlıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı / Yüksek / P1 |
| Kategori / release blocker | Katman ihlali / Hayır — tenant ve lifecycle etkisi Faz 5/7’de doğrulanmalı |
| Dosya ve sembol | `mesa_mcp/server.py:call_tool`, `get_stats` kolu |
| Doküman iddiası | MCP → SDK → REST API |
| Kod gerçeği | record/search/forget SDK kullanır; get_stats doğrudan AsyncEngine, MemoryDAO ve KuzuGraphProvider açar |
| Etki | MCP ikinci bir storage composition/lifecycle yolu oluşturur; graph count sorgusu agent predicate taşımıyor |
| Sonraki doğrulama | Faz 5 tenant isolation, Faz 7 connection lifecycle |

### DOC-001 — README geliştirme entry point’i için worker/Kuzu iddiası yalanlandı

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı / Orta / P2 |
| Kategori / release blocker | Dokümantasyon / Hayır |
| Dosya ve sembol | `README.md:321`; `scripts/run_server.py:146-217` |
| Doküman iddiası | `make dev` workers veya KuzuDB olmadan hafif server çalıştırır |
| Kod gerçeği | Dev lifespan Kuzu schema/provider ve consolidation, maintenance, REM task’lerini başlatır |
| Etki | Geliştirici kaynak/operasyon beklentisi yanlış yönlenir |
| Sonraki doğrulama | Faz 12 dev/production parity |

### DOC-002 — Docker Compose mount yolları API’nin gerçek storage yollarıyla eşleşmiyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı / Yüksek / P1 |
| Kategori / release blocker | Deployment mimarisi / Hayır — gerçek persistence etkisi container runtime ile doğrulanmalı |
| Dosya ve sembol | `docker-compose.yml:15-17`; `mesa_memory/api/server.py:91-95`; `Dockerfile:VOLUME` |
| Doküman iddiası | Persistent storage `/app/storage` altında production parity ile korunur |
| Kod gerçeği | Server `mesa.db` ve `vector.lance` doğrudan `/app/storage` altında kullanır; Compose SQLite/Lance bind mount’larını `/app/storage/sqlite` ve `/app/storage/lancedb` altına bağlar |
| Etki | Storage sahipliği ve restart persistence dokümantasyon iddiası statik olarak tutarsızdır |
| Sonraki doğrulama | Faz 12/13 izole container volume ve restart kontrolü |

### FLOW-001 — Kabul edilmiş cold-path kaydı için restart-safe teslimat yok

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Veri akışı-worker dayanıklılığı / Evet |
| Dosya ve sembol | mesa_api/router.py:242-255; mesa_workers/ingestion_worker.py:96-278; mesa_storage/schemas.py:68-83; mesa_memory/api/server.py:145-329 |
| Beklenen / gerçek | HTTP 202 ile kabul edilen kayıt restart sonrası yeniden teslim edilmelidir. Route yalnız in-process BackgroundTask ekler; startup status'u DEFERRED yapar, fakat raw_log replay consumer başlatmaz. |
| Somut kanıt / tekrar üretme | insert_raw_log commit → BackgroundTasks zinciri, initialize_schema recovery UPDATE'i ve server lifespan worker listesi incelendi. Yetkili sonraki aşamada 202 sonrası kontrollü process kesintisi/restart ve status polling ile doğrula. |
| Etki / kök neden | Kabul edilen kayıt otomatik işlenmeyebilir; memory kaybı/gecikmesi ve belirsiz terminal status oluşur. Durable staging ile durable dispatch/replay ayrıdır. |
| Önerilen düzeltme / regresyon testi | Transactional outbox/queue veya claim+lease consumer; idempotent write ile restart recovery E2E. |
| Tahmini efor / bağımlılıklar | Orta-Yüksek / Faz 6 transaction, Faz 7 worker tasarımı |

### DATA-001 — Purge üçlü store lifecycle'ını Kuzu'ya uygulamıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Fixed but not verified (WAVE-002 E2) / Yüksek / P1 |
| Kategori / release blocker | Veri bütünlüğü-retention / Evet |
| Dosya ve sembol | `mesa_storage/dao.py:purge_memory/resume_purge`; `mesa_storage/kuzu_provider.py:delete_nodes/verify_nodes_absent`; `mesa_storage/alembic/versions/c4f1a8e2d9b0_add_purge_journal.py` |
| Beklenen / gerçek | SQLite canonical coordinator exact target scope’u journal’a yazar ve tombstone eder; Kuzu doğrulanır, sonra vector doğrulanır, ardından finalization yapılır. E2 fake-store kanıtı vardır; gerçek-store E3 yoktur. |
| Somut kanıt / tekrar üretme | Purge mutasyonları vector/SQLite ile sınırlı; maintenance graph provider almaz; get_neighbors Kuzu'dan döner. Sonraki aşamada node+edge seed → purge → üç store negatif görünürlük testi çalıştır. |
| Etki / kök neden | Silinmiş/retention dışı graph node-edge'leri kalabilir; graph traversal/hydration phantom sonuç ve erasure riski doğar. Global transaction veya graph compensation yoktur. |
| Önerilen düzeltme / regresyon testi | Kuzu invalidation/hard-delete adımını saga'ya ekle; partial vector failure telafisi ve üç-store purge/retention integration testi. |
| Tahmini efor / bağımlılıklar | Orta / Faz 6 ve Faz 11 politikası |

### SDK-001 — MCP varsayılan base URL'si SDK yoluyla çifte /v3 üretir

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | SDK-MCP API sözleşmesi / Evet |
| Dosya ve sembol | mesa_mcp/server.py:16,109-110; mesa_client/client.py:197-208,212-238,301-342 |
| Beklenen / gerçek | MCP varsayılanı /v3/memory/* API rotasına gitmelidir. MCP http://localhost:8000/v3 verir, client tekrar /v3/memory ekler; sonuç /v3/v3/memory/... olur. |
| Somut kanıt / tekrar üretme | String birleşimi kaynakta doğrulandı; servis çalıştırılmadı. HTTP mock/ASGI transport ile default MCP route assertion'ı eklenmeli. |
| Etki / kök neden | Varsayılan konfigürasyonla MCP REST tool'ları beklenen API'ye ulaşmaz. Version prefix iki katmanda sahiplenilmiştir. |
| Önerilen düzeltme / regresyon testi | Base URL veya client path'inden yalnız birinde /v3 bırak; SDK/MCP URL contract testleri. |
| Tahmini efor / bağımlılıklar | Küçük / API versioning kararı |

### SDK-002 — Purge route yanıtı SDK/MCP response modelini karşılamıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | API-SDK-MCP sözleşmesi / Evet |
| Dosya ve sembol | mesa_api/router.py:508-511; mesa_api/schemas.py:446-460; mesa_client/client.py:227-240,331-344; mesa_mcp/server.py:194-208 |
| Beklenen / gerçek | Başarılı purge SDK modelince parse edilmelidir. API purged/deleted_records_count, SDK PURGED/scope/scope_id/records_affected bekler; MCP parse hatasını genel hata yapar. |
| Somut kanıt / tekrar üretme | Router payloadı ile Pydantic alanları karşılaştırıldı; MesaClient.purge doğrudan model parse eder. Gerçek request çalıştırılmadı. |
| Etki / kök neden | Purge sunucuda gerçekleşse bile SDK/MCP başarıyı işleyemez; kullanıcı hata görüp tekrar istek gönderebilir. Route contract'ı ortak şemadan sapmıştır. |
| Önerilen düzeltme / regresyon testi | Route response_model/payloadı ortak modelle eşleştir; SDK sync/async ve MCP forget HTTP contract testi. |
| Tahmini efor / bağımlılıklar | Küçük / API compatibility politikası |

### FLOW-002 — Session end final consolidation tetiklemiyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Orta / P2 |
| Kategori / release blocker | Session iş akışı / Hayır |
| Dosya ve sembol | mesa_api/router.py:629-660; tests/test_router_coverage.py:178-190 |
| Beklenen / gerçek | Endpoint açıklamasındaki gibi session bitişi final consolidation tetiklemelidir. WRITE RBAC sonrası yalnız log ve ended yanıtı döner; task, queue veya consolidation_loop kullanılmaz. |
| Somut kanıt / tekrar üretme | Method gövdesi yalnız would enqueue yorumunu içerir; mevcut test sadece HTTP ended yanıtını assert eder. |
| Etki / kök neden | Session finalizasyonuna bağlı consolidation beklentisi karşılanmaz. Endpoint sözleşmesi tamamlanmamıştır. |
| Önerilen düzeltme / regresyon testi | Finalization semantics'ini belirle; enqueue/işleme sonucunu doğrulayan contract testi. |
| Tahmini efor / bağımlılıklar | Küçük-Orta / Faz 4 iş kuralı, Faz 7 queue kararı |

## Faz 4 — Modül ve iş mantığı statik analizi (2026-07-17)

### SEC-002 — Agent kimliği, doğrulanmış çağırana bağlanmıyor; session oluşturma yetkiyi kendisi veriyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Kritik / P0 |
| Kategori / release blocker | Güvenlik-tenant izolasyonu / Evet |
| Modül; dosya ve sembol | API/RBAC; `mesa_memory/api/server.py:get_api_key`; `mesa_api/router.py:start_session`; `mesa_memory/security/rbac.py:AccessControl.grant_access` |
| Beklenen / gerçek davranış | Doğrulanan principal yalnız yetkili agent için session açmalıdır. Server tek global API key doğrular; `start_session`, istemci gövdesindeki herhangi bir `agent_id` için session üretir ve doğrudan WRITE grant verir. Principal-agent eşlemesi veya ön izin kontrolü yoktur. |
| Kod kanıtı | `get_api_key` yalnız header değerini global sırla karşılaştırır; `start_session` `payload.agent_id` ile `grant_access(..., "WRITE")` çağırır; `grant_access` çağıranın agent yetkisini doğrulamaz. |
| Test kanıtı | `tests/test_rbac.py` policy kombinasyonlarını kapsar; HTTP principal → agent bağlama veya başka agent adına session açmayı reddetme testi bulunamadı. |
| Etki / kök neden | Aynı global key’i kullanan ayrı tenant/istemcilerde caller-controlled agent identity, başka agent adına session ve yazma yetkisi üretebilir. Kök neden authentication principal’ının RBAC subject’inden ayrık olmasıdır. |
| Gerekli regresyon / düzeltme yönü | İki principal/agent ile negatif endpoint testi; API key/claim → agent binding ve session öncesi authorization. |
| Tahmini efor / sonraki faz | Orta-Yüksek / Faz 5, ardından kontrollü düzeltme |

### SEC-003 — Daily-limit tablosu API credential değerini tenant anahtarı olarak kalıcılaştırıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Güvenlik-secret lifecycle / Evet |
| Modül; dosya ve sembol | API middleware/storage; `mesa_memory/api/middleware.py:check_daily_limit`; `mesa_storage/dao.py:increment_and_check_daily_limit` |
| Beklenen / gerçek davranış | Limit hesabı credential’ın kendisini persist etmemelidir. Middleware header’daki API key veya Bearer değerini `agent_id` değişkenine koyar; DAO bunu `daily_limits.agent_id` alanına INSERT/UPDATE eder. |
| Kod kanıtı | Header değeri yalnız `Bearer` öneki çıkarılarak DAO’ya verilir ve SQL parametresi olarak saklanır. Secret değeri okunmadı veya raporlanmadı. |
| Test kanıtı | Raw credential’ın persistence’a hiç ulaşmadığını doğrulayan test bulunamadı. |
| Etki / kök neden | Uygulama DB/backup/export kapsamına credential materyali eklenir. Kök neden rate-limit subject ile auth credential’ın aynı değer olmasıdır. |
| Gerekli regresyon / düzeltme yönü | Non-reversible hash/HMAC subject; raw credential’ın SQLite’a yazılmadığını doğrulayan test ve güvenli retention/migration planı. |
| Tahmini efor / sonraki faz | Orta / Faz 5 ve Faz 11 |

### SDK-003 — Async SDK authentication header’ı server sözleşmesiyle uyumsuz

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | SDK/API contract / Evet |
| Modül; dosya ve sembol | `mesa_client/client.py:AsyncMesaClient.__init__`; `mesa_memory/api/server.py:get_api_key` |
| Beklenen / gerçek davranış | Sync/async client aynı server auth sözleşmesini kullanmalıdır. Sync `X-API-Key` gönderir; async yalnız `Authorization: Bearer …` gönderir; server yalnız `X-API-Key` okur. |
| Kod kanıtı | İki constructor ve `APIKeyHeader(name="X-API-Key")` doğrudan karşılaştırıldı. |
| Test kanıtı | Async SDK/MCP ile gerçek authenticated request contract testi bulunamadı. |
| Etki / kök neden | API key ile yapılandırılmış async SDK ve MCP REST tool’ları 401 alır; davranış client türüne göre değişir. |
| Gerekli regresyon / düzeltme yönü | Aynı sentetik key ile sync/async/MCP contract testi; tek header standardı. |
| Tahmini efor / sonraki faz | Küçük / Faz 8 contract testi |

### DATA-002 — Graph yazım hatası yutuluyor, DAO üç-store başarı döndürüyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Fixed but not verified / Kritik / P0 |
| Kategori / release blocker | Veri bütünlüğü-split brain / Evet |
| Modül; dosya ve sembol | `mesa_storage/dao.py:MemoryDAO.insert_memory`, `bulk_insert_memory`; `mesa_storage/kuzu_provider.py:KuzuGraphProvider.insert_node` |
| Beklenen / gerçek davranış | Başarısız graph mutation hata/geri alma veya açık pending state vermelidir. WAVE-002 sonrası graph hata loglanır, yeni vector soft-delete ile telafi edilir ve exception SQLite transaction’dan önce yükseltilir. |
| Kod kanıtı | WAVE-002 exact-anchor diff: graph exception block’u fail-closed; single ve bulk yolunda vector compensation + re-raise bulunur. |
| Test kanıtı | WAVE-002 `tests/test_triple_store_mutation_contract.py` graph failure senaryosu önce failure, sonra pass verdi: exception yükseldi, yeni vector telafi edildi ve SQLite transaction’a girilmedi. |
| Etki / kök neden | Tarihsel sessiz split-brain yolu kapatıldı; ancak SQLite commit failure, bulk partial graph success ve restart/recovery üzerinde graph/vector orphan riski sürer. |
| Gerekli regresyon / düzeltme yönü | Gerçek Kuzu+SQLite+Lance component fault, commit/restart ve compensation/outbox veya retryable pending-state testi. |
| Tahmini efor / sonraki faz | Fixed but not verified / WAVE-002 E2 tamam; sonraki storage/recovery dalgası |

### DATA-003 — Embedding arızası zero/mock vektör olarak persist edilebiliyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Veri kalitesi-retrieval bütünlüğü / Hayır |
| Modül; dosya ve sembol | `mesa_storage/vector_engine.py:VectorEngine._sync_compute_embedding`; `mesa_memory/consolidation/writer.py:GraphWriter._write_triplet` |
| Beklenen / gerçek davranış | Provider arızası retryable failure/DLQ veya açık degraded status üretmelidir. Vector engine LiteLLM hatasında zero, dependency yoksa deterministic mock; writer embed hatasında zero vektörle yazar. |
| Kod kanıtı | Exception blokları doğrudan `[0.0] * dimension` döndürür/atar. |
| Test kanıtı | Gerçek provider failure sonrası zero-vector write’ın reddedildiği veya retrieval kalitesi korunuyor testi yok. |
| Etki / kök neden | Başarılı kabul edilen anlamsız embedding’ler similarity, semantic conflict ve cold-start sıralamasını bozabilir. |
| Gerekli regresyon / düzeltme yönü | Provider error fixture’ında persistence yok/DLQ ve açık error; test embedder’ı üretim fallback’inden ayır. |
| Tahmini efor / sonraki faz | Orta / Faz 8 ve kontrollü düzeltme |

### DATA-004 — LanceDB upsert arızasında `add()` fallback’i idempotency garantisini kaldırıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Fixed but not verified / Yüksek / P1 |
| Kategori / release blocker | Storage-idempotency / Hayır |
| Modül; dosya ve sembol | `mesa_storage/vector_engine.py:VectorEngine._sync_upsert`, `bulk_upsert` |
| Beklenen / gerçek davranış | Aynı `node_id` için tek vector kayıt kalmalıdır. `merge_insert` RuntimeError/OSError verince kod `table.add()` ile devam eder; bu işlem tekilleştirme yapmaz. |
| Kod kanıtı | Fallback branch doğrudan `table.add([record])` çağırır. |
| Test kanıtı | WAVE-002 `tests/test_triple_store_mutation_contract.py` single/bulk deterministic merge failure testleri önce 2 failure, sonra 2 pass verdi. |
| Etki / kök neden | Retry/transient hatada duplicate vector kayıtları ve belirsiz search/soft-delete kapsamı oluşabilirdi; `add()` fallback’i kaldırıldı. |
| Gerekli regresyon / düzeltme yönü | Gerçek LanceDB/retry/WAL replay component testi ve repair/restart kanıtı gerekir. |
| Tahmini efor / sonraki faz | Fixed but not verified / WAVE-002 E2 tamam; recovery kanıtı sonraki storage dalgası |

### LOGIC-001 — Cold-path status endpoint’i session-start akışıyla erişilemez olabiliyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | API/RBAC iş mantığı / Hayır |
| Modül; dosya ve sembol | `mesa_api/router.py:start_session`, `get_status`; `mesa_memory/security/rbac.py:AccessControl.check_access` |
| Beklenen / gerçek davranış | Session başlatan yetkili caller own cold-path durumunu sorgulayabilmelidir. Start yalnız yeni session için WRITE grant verir; status sabit `"__any__"` session’ında READ grant arar. |
| Kod kanıtı | RBAC exact `(agent_id, session_id)` lookup ile iki endpoint zinciri karşılaştırıldı. |
| Test kanıtı | Router coverage status permission error’ı kapsar; start→insert→status success sözleşmesi yoktur. |
| Etki / kök neden | Normal session akışından sonra polling 403 verebilir; 202 kabulü takip edilemez. Kök neden endpoint’ler arasında session scope taşınmamasıdır. |
| Gerekli regresyon / düzeltme yönü | start sonrası status success/negative-agent API testi; status scope’unu raw-log ownership ile hizala. |
| Tahmini efor / sonraki faz | Küçük-Orta / Faz 5 ve Faz 8 |

### LOGIC-002 — Partial/bozuk extraction kaydı başarıyla consolidated kapanabiliyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Extraction/consolidation iş mantığı / Evet |
| Modül; dosya ve sembol | `mesa_memory/extraction/triplet_extractor.py:_extract_batch_with_retry`; `mesa_memory/consolidation/writer.py:GraphWriter.commit_batch` |
| Beklenen / gerçek davranış | Partial/bozuk LLM cevabında eksik kayıt retry/DLQ’ya gitmeli, başarıyla kapanmamalıdır. Parser `ValueError` sonrası bisection sonucu için `fb_missing_* = []` atar; karşı triplet yoksa writer `mark_consolidated` çağırır. |
| Kod kanıtı | Fallback exception blokları ve writer’ın boş-head branch’i birlikte izlendi. |
| Test kanıtı | Consolidation testleri mock happy path/ayrı resilience yollarını kapsar; partial bisection → consolidated → DLQ yok senaryosu bulunamadı. |
| Etki / kök neden | Raw log eksik extraction ile kalıcı işlenmiş görünür, yeniden deneme olmaz ve bilgi kaybolur. Coverage bilgisinin bisection sonrası discarded edilmesi ile empty extraction’ın başarı sayılması temel nedendir. |
| Gerekli regresyon / düzeltme yönü | Partial malformed batch fixture; her input için extract/DLQ/reject terminal kaydı; mark yalnız doğrulanmış terminal semantic karardan sonra. |
| Tahmini efor / sonraki faz | Orta / Faz 7 ve Faz 8 |

### LOGIC-003 — Cold-start retrieval quarantined node filtresini atlıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Retrieval iş mantığı / Evet |
| Modül; dosya ve sembol | `mesa_memory/retrieval/hybrid.py:HybridRetriever.retrieve`, `_apply_alpha_reranking` |
| Beklenen / gerçek davranış | Quarantined node hiçbir retrieval yolunda görünmemelidir. Alpha yolu epistemic data çekip karantinayı atar; `is_cold_start or not graph_results` yolu vector/FTS adaylarını doğrudan rerank’e verir. |
| Kod kanıtı | Aynı `retrieve` fonksiyonundaki iki candidate branch karşılaştırıldı. |
| Test kanıtı | PageRank testleri quarantine atamayı kapsar; cold/no-graph sonucu içinden quarantined node dışlama testi yoktur. |
| Etki / kök neden | Karantina politikası cold-start veya graph failure anında bypass olur; filtering ortak aşama yerine alpha branch’indedir. |
| Gerekli regresyon / düzeltme yönü | Quarantined vector/FTS hit ile cold/no-graph fixture; filtering’i tüm candidate path’lerden önce ortaklaştır. |
| Tahmini efor / sonraki faz | Küçük-Orta / Faz 5 ve Faz 8 |

### PERF-001 — HTTP metrics ham path label’ı sınırsız cardinality üretebilir

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Orta / P2 |
| Kategori / release blocker | Observability-kaynak yönetimi / Hayır |
| Modül; dosya ve sembol | `mesa_memory/api/server.py:add_api_version_header`; `mesa_memory/observability/metrics.py:PROM_HTTP_REQUESTS` |
| Beklenen / gerçek davranış | Metric labels düşük/sınırlı cardinality’de route template kullanmalıdır. Middleware ham `request.url.path` değerini endpoint label’ına koyar; log/session id içeren path’ler ayrı series oluşturur. |
| Kod ve test kanıtı | Middleware/Counter tanımı incelendi; bounded label testi bulunamadı. |
| Etki / kök neden | Uzun süreli trafikte Prometheus bellek/TSDB maliyeti artabilir. |
| Gerekli regresyon / düzeltme yönü | Parametreli path’lerin tek route label ürettiği test; route template/allowlist. |
| Tahmini efor / sonraki faz | Küçük / Faz 12 |

## Faz 5 — Güvenlik ve tenant izolasyonu derin denetimi (2026-07-17)

### RLS-001 — Valence/adaptive-routing state’i tenant-scoped değil; bir tenant diğerinin kabul eşiğini etkileyebiliyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Tenant izolasyonu-politika/state / Evet |
| Etkilenen katman; dosya ve sembol | Valence/consolidation; `mesa_memory/consolidation/router.py:AdaptiveRouter.__init__/update_dynamic_threshold`; `mesa_memory/valence/core.py:ValenceMotor._hydrate_embeddings/save_state/load_state`; `mesa_storage/dao.py:get_all_embeddings` |
| Güvenli saldırı senaryosu / önkoşul | Aynı API process’inde iki geçerli tenant işlenir. Bir tenant yoğun/atipik embedding veya routing telemetry üretir; diğer tenant’ın karar eşiği, novelty baseline’ı ve persisted `valence_core_state` bundan etkilenir. Aktif exploit çalıştırılmadı. |
| Beklenen / gerçek davranış | Tenant’a ait kabul/novelty/telemetry state başka tenant verisinden türememelidir. Server tek `ConsolidationLoop` ve tek `AdaptiveRouter` oluşturur; router `ValenceMotor(storage=self.dao)` ile `get_all_embeddings()` çağırır; DAO bu çağrıda agent filtresi kullanmaz. `update_dynamic_threshold(agent_id)` ise tek paylaşılan `self.t_route` alanını çağrılan tenantın telemetry’siyle değiştirir. Valence state DB anahtarı da tenant içermez. |
| Kanıt ve test | Çağrı zinciri ve fonksiyon gövdeleri statik izlendi. `test_valence_persistence.py` tek motor/state’i, `test_adaptive_router.py` tek tenant mock akışını kapsar; iki tenantın birbirinin policy state’ini etkilemediği test yoktur. |
| Etki / blast radius / kolaylık | Cross-tenant read response üretmeden admission/rejection ve LLM maliyeti davranışı etkilenebilir; aynı process’teki tüm tenantlar etkilenir. Geçerli tenant erişimi yeterlidir; tespit edilmesi zordur çünkü state globaldir. |
| Kök neden | Stateful policy bileşeni ve persistence şeması agent ownership modelinden bağımsız tasarlanmıştır. |
| Gerekli test / düzeltme yönü | İki tenant fixture’ı ile embedding, threshold ve persisted state izolasyon testi; state/telemetry cache’i agent-scoped yap veya tenantlar arasında açıkça tek güvenlik domaini ilan et. |
| Tahmini efor / sonraki faz | Orta-Yüksek / Faz 6 state-concurrency, Faz 8 regression |

### INPUT-001 — `metadata` doğrulaması toplam gövde, liste boyutu ve derinliği sınırlamadığı için payload sınırı aşılabiliyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Input validation-kaynak tüketimi / Evet |
| Etkilenen katman; dosya ve sembol | API input/hot path; `mesa_api/schemas.py:_validate_metadata`, `MemoryInsertRequest`; `mesa_api/router.py:insert_memory`; `mesa_storage/dao.py:insert_raw_log` |
| Güvenli saldırı senaryosu / önkoşul | Geçerli API key ve session WRITE izni olan istemci, az sayıda metadata anahtarında büyük veya iç içe liste değerleri gönderir. Aktif büyük payload gönderilmedi. |
| Beklenen / gerçek davranış | İçerik ve metadata birlikte sınırlı, flat ve bounded olmalıdır. Validator yalnız anahtar sayısını, doğrudan `dict` değeri ve doğrudan `str` uzunluğunu kontrol eder; `Any` listeleri, iç içe listeler ve liste içindeki uzun string/dict’ler sınırsızdır. Server-level request body sınırı bulunmaz; router validate edilmiş nesneyi raw_logs JSON’a yazar. |
| Kanıt ve test | `_validate_metadata` ve hot-path persistence zinciri okundu. `tests/test_api_schemas.py` direct nested dict, key sayısı ve doğrudan string sınırını kapsar; list/depth/toplam byte ve HTTP body-before-parse sınır testleri yoktur. |
| Etki / blast radius / kolaylık | API process RAM’i, SQLite raw log alanı ve cold-path/queue maliyeti gereksiz büyür; tüm tenantlar etkilenebilir. Geçerli bir düşük yetkili client yeterlidir; günlük limit tek başına byte bütçesi sağlamaz. |
| Kök neden | Alan-bazlı şema limiti genel request/JSON bütçesi ve recursive metadata doğrulamasının yerine geçirilmiştir. |
| Gerekli test / düzeltme yönü | Byte-bazlı toplam request limiti, recursive JSON depth/list-length/value-size kontrolü, `extra="forbid"` kararı; UTF-8, metadata listesi ve total body negatif API testleri. |
| Tahmini efor / sonraki faz | Orta / Faz 8 test, Faz 10 kapasite doğrulaması |

### CI-001 — Güvenlik taraması action’ı floating `@main` referansı kullanıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Orta / P2 |
| Kategori / release blocker | CI/supply-chain / Hayır |
| Etkilenen katman; dosya ve sembol | CI; `.github/workflows/ci.yml:security-and-audit/TruffleHog` |
| Güvenli saldırı senaryosu / önkoşul | Üçüncü taraf action’ın upstream branch’i değişir; workflow çalıştığında değişen action kodu runner bağlamında yürür. Ağ/CI çalıştırılmadı. |
| Beklenen / gerçek davranış | Güvenlik gate’i değişmez commit SHA veya kurum politikasıyla sabitlenmiş action kullanmalıdır. Workflow secret scanner’ı mutable `@main` ile çağırır. |
| Kanıt ve test | Workflow statik incelendi; action pin doğrulama testi/kurumsal allowlist bulunamadı. |
| Etki / blast radius / kolaylık | Supply-chain güveni workflow runner ve checkout içeriğini kapsar; exploit upstream compromise gerektirir. |
| Kök neden | Action sürüm sabitleme politikası uygulanmamış. |
| Gerekli test / düzeltme yönü | Tam SHA pin ve düzenli controlled update; action provenance/pinning policy kontrolü. |
| Tahmini efor / sonraki faz | Küçük / Faz 12 CI hardening |

### Faz 5 mevcut bulgu kanıt güncellemeleri

| Mevcut ID | Yeniden doğrulanan güvenlik kanıtı | Sonuç |
|---|---|---|
| SEC-002 | `get_api_key` yalnız tek global key’i doğrular; `start_session` client-controlled `agent_id` için doğrudan WRITE grant verir. | Kritik tenant authorization blocker olarak doğrulandı. |
| SEC-003 | `check_daily_limit` header credential’ını `agent_id` değişkeni olarak DAO’ya iletir; daily limit SQL tablosunda persist edilir. | Yüksek secret lifecycle blocker olarak doğrulandı. |
| ARCH-003 | `mesa_workers/ingestion_worker.py:process_cold_path` ham `raw_log` dict’ini CWD’deki `cold_path_trace.txt` dosyasına yazar; raw content/metadata bu dict içindedir. | Config-managed storage/retention dışı hassas içerik sızıntısı; mevcut blocker doğrulandı. |
| ARCH-004 | `mesa_mcp/server.py:get_stats` SQL node count’u agent-scoped iken Kùzu edge count için `MATCH ()-[r]->()` agentsiz doğrudan query kullanır. | MCP aracından cross-tenant aggregate metadata disclosure; P1 release blocker’a yükseltildi. |
| SDK-003 | Async client `Authorization` kullanır, server `X-API-Key` dependency’si kullanır. | MCP’nin REST yolları için auth drift doğrulandı. |


## Faz 6 — Veri bütünlüğü ve eşzamanlılık doğrulaması

### DATA-005 — Blue/Green alignment migration lock/WAL protokolü yazı kaybına açık

| Alan | Değer |
|---|---|
| Durum | Doğrulandı (statik) |
| Önem | Kritik |
| Öncelik | P0 |
| Kategori | Veri bütünlüğü / migration / concurrency |
| Release blocker | Evet |
| Dosya yolu | `mesa_storage/dao.py`; `mesa_storage/vector_engine.py` |
| Satır veya sembol referansı | `MemoryDAO.align_memory_space` (288-378); `VectorEngine.apply_procrustes_and_switch` (1032-1180), `_mutation_lock` (439, 503, 745, 793, 1149) |
| Beklenen davranış | Alignment süresince yeni vector yazıları tek sahipli, dayanıklı biçimde sıraya alınmalı; başarı/başarısızlık/restart sonrasında her kabul edilmiş yazı tam olarak bir kez görünür olmalıdır. |
| Gerçek davranış | SQL'deki `lancedb_is_migrating` boolean değeri compare-and-set/owner token olmadan yazılır. Vector engine tam copy/transform/verify süresince mutation lock almaz; lock yalnız promotion anındadır. Bir insert flag'i `false` iken okuyup eski aktif tabloya transform snapshot'ından sonra yazabilir ve promotion bu yazıyı düşürebilir. Flag `true` iken WAL'a alınan kayıtlar, SQLite transaction açıkken vector bulk-upsert ile flush edilir; flush hatasında WAL korunur fakat finally flag'i `false` yapar ve normal akışta WAL replay tüketicisi yoktur. |
| Somut kanıt | `align_memory_space` flag'i ayrı transaction ile açar, `apply_procrustes_and_switch` çağırır, ardından `lancedb_wal` satırlarını okuyup `await self._vec.bulk_upsert(...)` sonrasında siler (`dao.py:300-371`). Vector full alignment’da lock yalnız `_sync_promote_table` öncesindedir (`vector_engine.py:1149`). Normal insert/bulk insert flag'i ayrı okumaktadır (`dao.py:462,617`). |
| Tekrar üretme adımları | İki kontrollü task/barrier ile insert'in migration flag okumasını transform snapshot ile promotion arasına yerleştir; ikinci senaryoda WAL flush'a kısmi/başarısız bulk-upsert enjekte et, process restart sonrası WAL replay ve üç-store görünürlüğünü doğrula. Bu test Faz 1.5 kapısı nedeniyle çalıştırılmadı. |
| Etki | Kabul edilmiş memory kaydı vector retrieval'dan kaybolabilir; staged WAL kayıtları görünmez kalabilir veya kısmi retry'da tekrar yazılabilir. SQLite/vector/graph görünürlüğü ayrışır. |
| Kök neden | Boolean flag bir dağıtık/exclusive lock değildir; read-then-act yarışına açıktır. WAL flush, dış store I/O'sunu SQLite write transaction içinde yapar ve sahiplik, replay, atomic acknowledgement/idempotency sağlamaz. |
| Önerilen düzeltme | Tek-owner migration lease/fencing token, tüm vector mutasyonlarını kapsayan barrier, WAL row claim/ack ve idempotency key kullan; flush sonrası yalnız doğrulanmış satırları ack et; restart/startup repair consumer ekle. Dış vector I/O SQLite write lock dışında, dayanıklı outbox protokolüyle yürüsün. |
| Gerekli regresyon testi | Barrier tabanlı in-flight insert/promotion; flush partial failure; restart-before/after-ack; çift alignment; aynı node için exact-once görünürlük ve SQL/Lance/Kuzu repair assertions. |
| Tahmini efor | Yüksek |
| Bağımlılıklar | Storage/migration tasarımı, Faz 7 worker recovery, Faz 8 component/chaos testleri |

### CONC-002 — Raw-log claim ve terminal durum geçişi atomik değil

| Alan | Değer |
|---|---|
| Durum | Doğrulandı (statik) |
| Önem | Yüksek |
| Öncelik | P1 |
| Kategori | Concurrency / worker / idempotency |
| Release blocker | Evet |
| Dosya yolu | `mesa_workers/ingestion_worker.py`; `mesa_storage/dao.py` |
| Satır veya sembol referansı | `process_cold_path` (96-305), `_commit_triplets` (757-934), `update_raw_log_status` (1726-1758) |
| Beklenen davranış | Bir raw log yalnız bir worker tarafından claim edilmeli; izinli durum geçişleri current status predicate ile atomik uygulanmalı; commit başarısızsa kayıt `processed` olmamalıdır. |
| Gerçek davranış | Worker önce `get_raw_log` ile `DEFERRED` okur, sonra ayrı UPDATE ile `processing` yazar. UPDATE'in `WHERE` koşulunda önceki status/lease yoktur ve rowcount kontrol edilmez; iki task aynı kaydı işleyebilir. `_commit_triplets` hata ve yalnız vector soft-delete telafisini yutup normal döner; caller daha sonra raw log'u `processed` yapar. |
| Somut kanıt | `process_cold_path` status kontrolü ve update'i ayrı await noktalarıdır (`ingestion_worker.py:142-191`); DAO UPDATE'i yalnız `id AND agent_id` ile sınırlıdır (`dao.py:1750-1754`). Triplet catch bloğu exception'ı loglayıp re-raise etmez (`ingestion_worker.py:855-932`), ardından caller processed güncellemesi yapar (`ingestion_worker.py:278`). |
| Tekrar üretme adımları | Aynı `log_id` için iki task'ı status read sonrası barrier'da eşzamanla; her birinin side-effect sayısını ve tek claim sonucunu doğrula. Edge insert hatası enjekte edip raw log'un `processed` değil retryable failure/pending olduğunu doğrula. Çalıştırılmadı. |
| Etki | Çift LLM/REBEL maliyeti, duplicate node/edge veya çakışan soft-delete, yanlış `processed` terminal durumu ve restart/retry'da veri kaybı oluşabilir. |
| Kök neden | Kalıcı queue consumer için compare-and-set claim/lease, durum makinesi doğrulaması, idempotency anahtarı ve atomic completion sınırı yoktur. |
| Önerilen düzeltme | Tek SQL statement ile `DEFERRED → processing` claim (`WHERE status=...`, lease owner/expiry, rowcount); terminal transition guard ve attempt/idempotency key ekle. Commit sonucu başarısızsa exception/pending state'i koru; worker restart replay ile lease expiry'yi işle. |
| Gerekli regresyon testi | Aynı log için iki concurrent worker; crash/lease expiry/reclaim; Kuzu hata sonrası terminal state; aynı idempotency key ile duplicate delivery; raw-log→üç-store atomic görünürlük. |
| Tahmini efor | Orta-Yüksek |
| Bağımlılıklar | FLOW-001, DATA-002, Faz 7 worker/queue tasarımı, Faz 8 testleri |

### CONC-003 — Adaptive valence/routing mutable state eşzamanlı güncellemelerde kayıyor

| Alan | Değer |
|---|---|
| Durum | Doğrulandı (statik) |
| Önem | Yüksek |
| Öncelik | P1 |
| Kategori | Concurrency / karar tutarlılığı |
| Release blocker | Hayır — ancak RLS-001 ile birlikte tenant policy etkisini büyütür |
| Dosya yolu | `mesa_memory/valence/core.py`; `mesa_memory/consolidation/router.py` |
| Satır veya sembol referansı | `ValenceMotor.evaluate` (156-252), `_admit` (254-262), `AdaptiveRouter.update_dynamic_threshold` (126-163) |
| Beklenen davranış | Aynı process'teki eşzamanlı admission/threshold hesapları tutarlı snapshot ve seri state güncellemesi kullanmalıdır. |
| Gerçek davranış | `evaluate` novelty için await ettikten sonra kilitsiz olarak `memory_count`, embedding history ve recalibration counter'ını değiştirir. Birden çok task aynı eski history/threshold ile karar verip state'i yarışmalı güncelleyebilir. `update_dynamic_threshold` da `_last_update_time` ve global `t_route` değerini lock/agent scope olmadan değiştirir; shutdown save'i çalışan task'lerle eşzamanlı olabilir. |
| Somut kanıt | Valence instance'i shared `AdaptiveRouter` içinde tek kez oluşturulur; `evaluate` state snapshot'ından sonra `await calculate_novelty_score` çağırır ve `_admit` doğrudan mutable alanları artırır (`core.py:156-262`). Router cache/threshold alanlarını lock olmadan günceller (`router.py:123-163`). |
| Tekrar üretme adımları | Novelty coroutine'ini barrier ile iki admission arasında durdur; count/history/recalibration ve threshold sonucunu serial referansla karşılaştır. Shutdown save sırasında admission çalıştırıp persisted state'i kontrol et. Çalıştırılmadı. |
| Etki | Admission/LLM routing kararı deterministik olmayabilir; history/counter snapshot kayması RLS-001'in zaten doğrulanmış cross-tenant policy etkisini büyütebilir. |
| Kök neden | Shared mutable domain state için async lock, per-agent state partition ve atomic persistence yoktur. |
| Önerilen düzeltme | Agent-scoped state'e geç; admission/threshold update için dar bir async lock veya tek-writer actor kullan; shutdown'da producer task'lerini durdurup drain ettikten sonra snapshot kaydet. |
| Gerekli regresyon testi | İki/çok task admission barrier testi, per-agent isolation, recalibration boundary, shutdown-save race ve deterministic routing assertion. |
| Tahmini efor | Orta |
| Bağımlılıklar | RLS-001, lifecycle/worker tasarımı, Faz 8 testleri |

### Faz 6 yeniden doğrulanan mevcut bulgular

| Bulgu | Yeni statik kanıt | Sonuç |
|---|---|---|
| DATA-001 | `MaintenanceWorker` SQLite nodes ve LanceDB kayıtlarını ayrı adımlarda fiziksel siler; Kuzu provider/edge için mutation yoktur (`maintenance.py:328-626`). Vector purge/compact, `VectorEngine._mutation_lock` dışındaki default executor'da doğrudan engine internallerine erişir. | Üç-store retention ayrışması ve maintenance/API mutation yarışı riski sürer; çoklu process/idle-window testi yoktur. |
| DATA-002 | DAO secondary store başarılarından sonra SQLite commit hatasında telafi etmiyor; worker `_commit_triplets` edge hatasında yalnız vector soft-delete dener, SQLite/Kuzu node telafisi yapmaz ve exception'ı yutar. | Triple-store saga atomik değildir; CONC-002 ile false success etkisi güçlenir. |
| DATA-004 | DATA-005 altında başarısız/kısmi WAL flush sonrası replay/idempotency yoktur; mevcut `add()` fallback'i bu recovery akışında duplicate riskini artırır. | Idempotency kanıtı hâlâ yoktur. |
| ARCH-002 | Lifespan yalnız `state.consolidation_loop.stop()` çağırır; scheduled `consolidation_task`, `tier3_task`, `dlq_task` cancel/await edilmez. Ardından valence save ve storage close yapılır; VectorEngine `close()` çağrısı görülmez. | Shutdown drain/resource closure simetrisi doğrulanmamıştır; yeni bulgu açılmadan mevcut lifecycle kaydının kanıtı güçlendirildi. |


## Faz 7 — Worker, queue ve background işlem doğrulaması

### DLQ-001 — DLQ replay worker kaydı atomik claim olmadan silip tenant bağlamını kaybediyor

| Alan | Değer |
|---|---|
| Durum | Doğrulandı (statik) |
| Önem / öncelik | Kritik / P0 |
| Kategori | Worker / DLQ / veri kaybı |
| Release blocker | Evet |
| Worker/queue | `start_dlq_worker`; `PersistentQueue.dead_letter_queue` JSONL |
| Dosya ve sembol | `mesa_memory/consolidation/loop.py:start_dlq_worker` (608-700); `PersistentQueue.clear/agetitem/aappend` (99-128); `ConsolidationLoop.run_batch` DLQ append yolları (361-404, 469-489) |
| Beklenen davranış | Replay, item'ı tenant kimliği ve attempt bilgisiyle atomik claim etmeli; orijinal kayda erişim ve başarılı işlemden sonra ack/silme yapılmalıdır. |
| Gerçek davranış | Worker ilk batch'i belleğe okur, tüm JSONL'yi `clear()` ile siler; ardından kalan item'ları silinmiş dosyadan okumaya çalışır. DLQ item'ı yalnız `cmb_id` ve error taşır, `agent_id` taşımadığından default system agent ile lookup yapılır. Orijinal kayıt bulunamazsa veya process hata alırsa item durable biçimde yeniden eklenmez. |
| Failure senaryosu / kod kanıtı | `clear()` 644-646'da, leftovers'ın `agetitem()` çağrısı 648-657'de gelir; bu nedenle leftover zaten silinmiştir. Replay lookup 661-668'de default `agent_id` kullanır; append şeması 369-374, 383-388 ve 473-488'de agent/retry/timestamp içermez. |
| Test kanıtı | DLQ replay, clear-before-ack, tenant lookup, crash veya partial batch testi bulunamadı. |
| Etki / blast radius | Her extraction/Tier-3 hatası; kayıtların sessizce kaybolması, retry/forensics yapılamaması ve tenant kaydının yanlış bulunmaması. |
| Tekrar üretilebilirlik | Küçük sentetik JSONL ile statik olarak açık; güvenli runtime testi Faz 1.5 kapısı nedeniyle çalıştırılmadı. |
| Kök neden | Queue primitive'inde pop/claim/ack, durable attempt metadata ve tenant ownership yok; destructive clear tüketim işlemi yerine kullanılmış. |
| Önerilen düzeltme | SQLite/outbox tabanlı DLQ veya dosya için atomic rename+claim/ack; item'a agent_id, origin, attempt, timestamp ve idempotency key ekle; başarıdan sonra yalnız claimed item'ı sil. |
| Gerekli regresyon testi | >batch-size DLQ, lookup failure, process crash before/after ack, multi-worker claim, cross-agent replay ve poison-record max-attempt/DLQ retention. |
| Tahmini efor / sonraki faz | Orta-Yüksek / Faz 8 test sistemi, Faz 9 kontrollü düzeltme |

### QUEUE-001 — Kalıcı ingestion queue için backlog/backpressure ve disk bütçesi yok

| Alan | Değer |
|---|---|
| Durum | Doğrulandı (statik) |
| Önem / öncelik | Yüksek / P1 |
| Kategori | Queue / backpressure / availability |
| Release blocker | Evet |
| Worker/queue | SQLite `raw_logs` → FastAPI `BackgroundTasks.process_cold_path` |
| Dosya ve sembol | `mesa_api/router.py:insert_memory` (191-265); `mesa_storage/dao.py:insert_raw_log` (1638-1674); `mesa_workers/ingestion_worker.py:MAX_CONCURRENT_WORKERS` (58, 96-305) |
| Beklenen davranış | API kabulü, kalıcı queue depth/byte bütçesi ve worker throughput ile ilişkilendirilmeli; sınırda kontrollü reject/defer ve gözlenebilir backlog üretmelidir. |
| Gerçek davranış | Her yetkili request raw_logs'a commit edilip 202 döner ve in-process task eklenir. Raw_logs için depth/byte limiti, admission check, processing count, alert veya disk-pressure policy yoktur. Module-local semaphore yalnız aynı process'te eşzamanlı 10 cold-path task'ı sınırlar; backlog'u sınırlamaz. |
| Kod / test kanıtı | Route insert sonrası koşulsuz `BackgroundTasks.add_task` yapar; DAO INSERT doğrudan commit eder. Config'te raw-log queue limit/retention/worker backlog limiti yoktur; mevcut testler backlog, disk pressure veya reject davranışını kapsamaz. |
| Etki | Worker throughput altında kaldığında SQLite/storage büyür; restart sonrası FLOW-001 ile birlikte kabul edilmiş iş birikir veya kaybolur. `INPUT-001` büyük payload riski bu etkiyi büyütür. |
| Kök neden | Durable staging queue için kapasite/backpressure, producer-consumer rate yönetimi ve queue health metriği tasarlanmamıştır. |
| Önerilen düzeltme | Agent/global depth+byte limit, 429/503 veya kabul edilen iş için durable quota, bounded worker dispatch, backlog metrics/alert ve retention/recovery politikası ekle. |
| Gerekli regresyon testi | Küçük sentetik limitte limit aşımı reject/defer; worker yavaş/kapalı iken queue büyümesi; disk hata; restart backlog; metric/alert assertion. |
| Tahmini efor / sonraki faz | Orta / Faz 8-10 |

### WORKER-001 — Worker liveness/readiness bağımsız olarak izlenmiyor

| Alan | Değer |
|---|---|
| Durum | Doğrulandı (statik) |
| Önem / öncelik | Yüksek / P1 |
| Kategori | Worker health / operasyon |
| Release blocker | Evet |
| Worker/queue | PageRank, entity consolidation, Tier-3 deferred, DLQ, REM, maintenance, WAL checkpoint |
| Dosya ve sembol | `mesa_memory/api/server.py:lifespan` (212-326, 334-407); health init; worker loop'ları |
| Beklenen davranış | Readiness worker zorunluluğu, alive/last-success/last-error ve backlog durumunu göstermeli; task creation/scheduling failure görünür olmalıdır. |
| Gerçek davranış | Lifespan birçok worker başlatma hatasını loglayıp devam eder; `state.is_ready=True` yapılır. Health DAO/storage durumunu kontrol eder; worker task tamamlanması, exception, lag, DLQ/backlog veya last-success bu health kararına bağlı değildir. |
| Kod / test kanıtı | PageRank/consolidation/Tier3/DLQ/maintenance/REM schedule blokları exception yakalayıp devam eder; WAL worker yalnız loglar. Worker metrics bazı sınıflarda process-local snapshot'tır ve health endpoint'e bağlanmaz. Worker-dead/readiness negative test yoktur. |
| Etki | API accepted/ready görünürken kritik asenkron işler çalışmıyor veya geri kalıyor olabilir; operatör veri işleme kaybını zamanında göremez. |
| Kök neden | Worker supervision/heartbeat ve readiness contract'i tanımlanmamış; task referans seti liveness denetimi için kullanılmıyor. |
| Önerilen düzeltme | Worker registry, task done callback, heartbeat/last-success/lag/backlog metric ve policy-temelli readiness/degraded health ekle. |
| Gerekli regresyon testi | Worker start failure/task exception sonrası health; heartbeat timeout; backlog/DLQ threshold; recovery sonrası readiness. |
| Tahmini efor / sonraki faz | Orta / Faz 8-9 |

### Faz 7 yeniden doğrulanan worker bulguları

| Bulgu | Yeni kanıt | Sonuç |
|---|---|---|
| FLOW-001 / CONC-002 | `BackgroundTasks` request sonrası in-process çalışır; raw-log CAS claim/lease/replay consumer yoktur. | Duplicate, stale processing ve crash-recovery blocker'ı sürer. |
| LOGIC-002 | Tier-3 deferred worker `run_batch` sonucunu per-record teslim sonucu olmadan her batch kaydını `mark_consolidated` yapar (`loop.py:552-606`). | Extraction/DLQ başarısızlığında false terminal success kök nedeni güçlendi. |
| CONC-CAND-001 | PersistentQueue append/clear/read süreç veya task lock'u olmadan çalışır; DLQ replay destructive clear kullanır. | Aday DLQ-001 altında doğrulanmış kayıp davranışına dönüştü. |
| ARCH-001 / ARCH-002 | Uygulama tek Uvicorn/API process içindeki task'leri başlatır; `consolidation_loop.start()` task referansı tutulmaz, consolidation/Tier-3/DLQ shutdown await'i yoktur. | Multi-worker/reload duplicate ve shutdown drain riski sürer. |
| DATA-005 | Ayrı LanceDB WAL replay worker/entry point bulunmadı. | Alignment WAL flush başarısızlığında normal runtime replay yoktur. |
| DATA-001 | Maintenance SQLite/vector adımlarını Kuzu cleanup olmadan çalıştırır. | Retention üç-store eşitliği hâlâ yoktur. |


## Faz 8 — Test sistemi ve boşluk doğrulaması

### TEST-001 — P0 production akışları için izole, uçtan uca release test kapısı yok

| Alan | Değer |
|---|---|
| Durum | Doğrulandı (statik) |
| Önem / öncelik | Kritik / P0 |
| Kategori | Test sistemi / release gate |
| Release blocker | Evet |
| Etkilenen davranış | Principal→tenant authz, üç-store commit/recovery, raw-log claim/restart, DLQ replay, purge hard-delete, migration WAL ve worker-aware lifecycle |
| Test dosyası veya eksik alan | `tests/test_rbac_leak.py`, `test_chaos.py`, `test_api_router.py`, `test_rem_cycle.py`, `test_maintenance_worker.py` ayrı ve çoğunlukla fixture/mock seviyesinde; tek izole contract/recovery suite bulunamadı. |
| Kod / test kanıtı | CI `pytest tests/ --cov` ve iki dar security gate çalıştırır; mevcut testler normal DAO tenant read ve ilk vector-failure rollback'i kapsar. Ancak `SEC-002`, `DATA-002/005`, `FLOW-001`, `CONC-002`, `DLQ-001`, `QUEUE-001`, `WORKER-001` için principal-to-agent, Kuzu failure, two-worker claim, crash/restart, DLQ claim/ack, WAL replay ve health-lag E2E testi yoktur. |
| Risk | Mevcut P0/P1 hatalar regression ile tekrar üretilebilir veya yeni düzeltmelerde fark edilmeden kalabilir; happy-path/coverage başarısı production readiness kanıtı değildir. |
| False-positive / false-negative etkisi | Mock testler gerçek process/queue/storage hata semantiğini olumlu gösterebilir; gerçek fault-injection olmadan release gate false-positive üretir. |
| Gerekli test türü / seviye | Sentetik env ve tamamen ayrı storage ile component/integration, düşük concurrency barrier, controlled crash/fault-injection, HTTP contract/lifecycle. |
| Tahmini efor / sonraki faz | Yüksek / Faz 9 sonrası Faz 10-13 doğrulaması |

### COVERAGE-001 — Coverage gate SDK’yı kapsam dışı bırakıyor; contract drift görünmüyor

| Alan | Değer |
|---|---|
| Durum | Doğrulandı (statik) |
| Önem / öncelik | Yüksek / P1 |
| Kategori | Coverage / SDK contract |
| Release blocker | Evet — SDK/MCP public contract blocker'ları kapanana kadar |
| Etkilenen davranış | Sync/async SDK auth, `/v3` URL, purge response ve MCP mapping |
| Dosya / kanıt | `pyproject.toml:[tool.coverage.run]` source listesinde `mesa_client` var, fakat `omit = ... mesa_client/*` ile dışlanır. `mesa_client/client.py`, `mesa_mcp/server.py` için normal pytest contract testi bulunmadı; yalnız `tests/go_live_proofs/verify_r10_mcp_spoofing.py` mock scriptidir. |
| Risk | `%85` CI coverage gate'i client/public API davranışını ölçmeden geçebilir; doğrulanmış `SDK-001..003` driftleri coverage tarafından görünmez kalır. |
| Önerilen test | Gerçek HTTP olmayan transport mock ile sync/async common request/header/URL/schema contract; MCP→SDK mapping; ardından SDK coverage kapsam kararı açıkça test planına bağlanmalı. |
| Tahmini efor / sonraki faz | Orta / Faz 9 |

### Faz 8 yeniden doğrulanan test kanıtları

| Alan | Kanıt / sonuç |
|---|---|
| Envanter | `tests/` altında 66 Python test dosyası, statik olarak 819 `test_*` fonksiyonu; `mesa-benchmark/tests` 5 dosya/24 test fonksiyonu. Faz 1.5 gate nedeniyle collection çalıştırılmadı; bu sayı collected test sayısı değildir. |
| Organizasyon/marker | `testpaths=[tests]`; unit/component/integration/e2e dizin/marker ayrımı yok. 416 `asyncio` marker, bir `skipif` bulundu; ağır benchmark `tests/bench` CI pytest komutundan ignore edilir. |
| Fixture izolasyonu | Global `tests/conftest.py` repo altında `.test_storage_tmp` kullanır ve cleanup yapar; 20 dosya temp storage referansı taşır. Ancak conftest import anında dummy env değerleri ve global limiter değiştirir; gerçek dotenv izolasyonunun yerine geçmez. |
| Mock kapsamı | 38 test dosyası MagicMock/AsyncMock/patch kullanır. Router, ingestion, consolidation, adapter ve worker testlerinin önemli bölümü DAO/LLM/retriever mock'lar; timing, provider, queue claim ve process crash semantiğini maskeler. |
| Coverage/CI | Coverage source core paketleri kapsar, `mesa_client/*` omit edilir; fail-under 85. CI full `tests/` (bench hariç), RBAC ve chaos gate, seeded legal audit, Docker build ve dev-entry canary içerir; worker/DLQ/WAL replay/SDK-MCP contract gate'i yoktur. Codecov upload hatası bloklayıcı değildir. |
| Flaky adayları | 11 test dosyasında sleep/real clock/random patterni; polling/lifecycle testleri mocked sleep ile ilerler. CI geçmişi olmadığı için flaky olduğu kesin değildir. |
| Eval/benchmark | `mesa_evals` manuel kalite/load/soak araçlarıdır; `mesa-benchmark` seed taşısa da LLM judge/Ollama/dış provider ve sonuç dizini kullanır. Core pytest release regression yerine geçmez; bu makinede manuel/kaynak yoğun kabul edildi. |


## Faz 9 remediation güncellemesi

### DLQ-001 — Durum güncellemesi (Duplicate of DLQ-001 canonical heading)

| Alan | Değer |
|---|---|
| Önceki durum | Doğrulandı (statik), Açık blocker |
| Yeni durum | Partially fixed / Fixed but not verified |
| Değişiklik | `mesa_memory/consolidation/loop.py`: replay öncesi `clear()` kaldırıldı; yeni DLQ item'ları agent_id taşır; kayıtlar yalnız `run_batch` dönüşünden sonra selected-item atomic rewrite ile acknowledge edilir. |
| Doğrulama | Düzeltme öncesi static invariant fail; sonrası invariant ve `python -m py_compile` geçti. `ruff`/`black` bu ortamda bulunmadı. Faz 1.5 gate nedeniyle runtime queue/crash testi çalıştırılmadı. |
| Kalan risk | Processler arası atomic claim/lease yok; `run_batch` per-record başarı sonucu dönmüyor; legacy agent_id'siz item'lar güvenlik için queue'da tutulur; JSONL durable queue tasarımı P0 production çıkış kriterini tek başına karşılamaz. |
| Blocker durumu | Açık — Partially fixed; runtime/pytest/integration doğrulaması yok, Resolved değildir |
## Faz 10 — Performans, kaynak kullanımı ve ölçeklenebilirlik (2026-07-17)

Bu faz yalnız statik kanıtla yürütüldü. Faz 1.5 izolasyon kapısı açık olduğundan benchmark, yük, soak, stres, model veya servis testi çalıştırılmadı.

### PERF-002 — Retrieval cold-start kararı tenant’ın tüm belleğini request yolunda yüklüyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Retrieval performansı ve RAM gecikmesi / Evet |
| Modül; dosya ve sembol | `mesa_memory/retrieval/hybrid.py:HybridRetriever.retrieve`; `mesa_storage/dao.py:MemoryDAO.get_memories` |
| Beklenen / gerçek davranış | Cold-start kararı sabit maliyetli count/threshold sorgusuyla alınmalıdır. Her search isteğinde `dao.get_memories(agent_id)` limitsiz çağrılır; DAO tüm aktif node satırlarını `fetchall()` ile process belleğine taşır ve yalnız `len()` sonucu kullanılır. |
| Somut kanıt | `retrieve` içindeki `all_nodes = await self.dao.get_memories(agent_id)` ve hemen sonraki `len(all_nodes) < config.cold_start_min_nodes`; DAO `limit=None` iken `SELECT * ... ORDER BY created_at ASC` ve `fetchall()` uygular. |
| Etki / kök neden | Tenant büyüdükçe her arama O(N) SQLite okuması, payload deserializasyonu ve geçici RAM tahsisi üretir; eşzamanlı aramalarda 16 GB RAM sınırında gecikme/GC/OOM riski büyür. Kök neden, cardinality kararının count yerine entity listesinden türetilmesidir. |
| Gerekli regresyon / düzeltme yönü | Agent-scoped `COUNT`/`EXISTS` DAO metodu ve threshold testleri; büyük sentetik tenantta request başına okunan satır sayısı, p95 latency ve peak RSS ölçümü. |
| Tahmini efor / sonraki faz | Küçük-Orta / Faz 9 remediation sonrası, güvenli performans ortamı |

### PERF-003 — Periyodik işçiler tenant büyüklüğüyle sınırsız tarama ve ardışık dış çağrı üretiyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Worker kapasitesi, LLM maliyeti ve kaynak izolasyonu / Evet |
| Modül; dosya ve sembol | `mesa_workers/entity_consolidation_worker.py:run_consolidation_scan`; `mesa_workers/rem_cycle.py:REMCycleWorker._process_agent`; `mesa_workers/maintenance_pagerank.py:_extract_subgraph/_compute_damped_pagerank` |
| Beklenen / gerçek davranış | Periyodik işler bounded/paged claim mekanizması, tenant başına bütçe ve görünür lag ile ilerlemelidir. Entity worker tüm node’ları yükleyip her node için neighbor → LLM complete → embedding → update çağrılarını ardışık yürütür; REM yalnız queue depth için tüm unconsolidated kayıtları yükler ama en çok 100’ünü işler; PageRank tüm tenant graphını RAM’e alıp işlemci havuzunda hesaplar. |
| Somut kanıt | Entity worker `get_memories(...include_consolidated=True)` limitsiz ve `for entity`; REM `get_memories(...include_consolidated=False)` limitsiz, sonra `unconsolidated[:max_records_per_cycle]`; PageRank tüm node/edge satırlarını listeler ve sparse matris kurar. |
| Etki / kök neden | Büyük tenant veya backlog, API ile aynı process’te RAM/CPU/disk executor ve LLM kapasitesi için yarışır; tek bir periyodik tur uzar, sonraki turlar gecikir ve maliyet lineer/graph boyutuna bağlı büyür. Kök neden durable/paged work queue, per-tenant quota ve leader/lag bütçesi olmamasıdır. |
| Gerekli regresyon / düzeltme yönü | Cursor/claim tabanlı bounded batch, tenant/iş başına time-token-record bütçesi, PageRank node/edge eşiği veya ayrı worker; backlog/lag telemetry. İzole sentetik storage’da küçük kademeli kapasite ölçümü ve cancellation/restart testi gerekir. |
| Tahmini efor / sonraki faz | Yüksek / Faz 9 remediation tasarımı, Faz 10 güvenli ölçüm, Faz 12 topology |

### PERF-004 — Search response hydration sonuç başına DAO çağrısı yapıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Orta / P2 |
| Kategori / release blocker | API N+1 veritabanı erişimi / Hayır |
| Modül; dosya ve sembol | `mesa_api/router.py:search_memory`; `mesa_storage/dao.py:get_memory_by_id` |
| Beklenen / gerçek davranış | Retrieval sonucunun metadata hydration’ı batch sorgu ile yapılmalıdır. Router her `cmb_id` için ayrı `await dao.get_memory_by_id(...)` çağrısı yapar. Request schema sonucu 1–50 ile sınırlar; bu nedenle maliyet sınırsız değildir ancak gereksiz ardışık I/O’dur. |
| Somut kanıt | `for cmb_id in cmb_ids` içindeki DAO çağrısı; `MemorySearchRequest.limit` üst sınırı 50. Retriever’ın CrossEncoder yolu zaten `get_nodes_by_ids_batch` kullanır. |
| Etki / kök neden | Her search için en fazla 50 ek SQLite round-trip oluşur; yüksek RPS’de connection semaphore kuyruklanması ve p95 artışı yaratabilir. |
| Gerekli regresyon / düzeltme yönü | Sıra-koruyan agent-scoped batch hydration ve query-count contract testi; sentetik ölçümde p50/p95 ve SQL sorgu sayısı karşılaştırması. |
| Tahmini efor / sonraki faz | Küçük / Faz 9 remediation sonrası |

## Faz 13 — Güvenli staging ve deployment rehearsal (2026-07-19)

### STAGE-001 — Worker’lar ayrı ve güvenli deployment role olarak izole edilemiyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Staging-topoloji ve worker güvenliği / Evet |
| Dosya / sembol | `mesa_memory/api/server.py:99` çevresi `lifespan`; worker schedule/start çağrıları |
| Beklenen / gerçek davranış | API-only staging profili worker’ları kapalı tutmalıdır. Mevcut lifespan storage başlatıldıktan sonra ConsolidationLoop, PageRank, consolidation, Tier-3, DLQ, maintenance, REM ve WAL task’lerini otomatik başlatır; doğrulanmış worker-disable profili bulunmaz. |
| Kanıt / etki | Faz 13 static-only denetimi; worker güvenlik kapısı uygulanmadan API process’i background iş/queue/adapter zincirini tetikleyebilir. |
| Gerekli düzeltme / test | Ayrı API/worker deployment role veya explicit disable switch, worker-aware readiness; sentetik absolute storage ile API-only ve ayrı worker lifecycle regresyonu. |
| Faz 14 etkisi | Release blocker açık; dinamik rehearsal yapılmadı. |

### CONFIG-002 — Config fail-closed davranışı ve dotenv izolasyonu yetersiz

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı (statik) / Yüksek / P1 |
| Kategori / release blocker | Deployment config ve secret/path izolasyonu / Evet |
| Dosya / sembol | `mesa_memory/config.py` modül seviyesi `load_dotenv()`; `mesa_memory/api/server.py` storage/lifespan; adapter config zinciri |
| Beklenen / gerçek davranış | Staging yalnız explicit geçici config ve absolute/izole storage ile fail-closed başlamalıdır. Import zinciri koşulsuz `.env` yükler; production profile için kapsamlı preflight validation kanıtı yoktur. |
| Kanıt / etki | Faz 13 static-only denetimi; gerçek config etkisi veya host-relative path/partial initialization riski nedeniyle API başlatılmadı. |
| Gerekli düzeltme / test | Dotenv’i explicit development-only yap; provider/path/numeric/queue/backup separation preflight’ını storage write öncesine al; negative config suite ekle. |
| Faz 14 etkisi | Release blocker açık; izole runtime rehearsal öncesinde kapanmalıdır. |

## Faz 13.5 — Audit bütünlüğü bulguları (2026-07-19, tarihsel ilk kayıt)

### AUDIT-INT-001 — Faz 11 ve Faz 12 zorunlu audit kayıtları persist edilmemiş

| Alan | Değer |
|---|---|
| Durum / önem | Doğrulandı / Kritik |
| Etkilenen fazlar | Faz 11, Faz 12, Faz 13.5 ve Faz 14 giriş kapısı |
| Etkilenen audit dosyaları | `CURRENT_PHASE`, `COMMAND_LOG`, `CHANGELOG_AUDIT`, `FINDINGS`, `BLOCKERS`, `FIX_PLAN`, `TEST_MATRIX`, `PRODUCTION_READINESS` |
| Beklenen kayıt | Migration/backup/restore ile Docker/CI/CD/operasyon fazlarının kapsamı, kanıtı, çıkış durumu, bulgu/blocker/test/runbook kayıtları |
| Gerçek kayıt | Faz 10’dan Faz 13’e geçiliyor; Faz 11/12 başlıkları ve komut/tamamlama kayıtları yok; readiness alanları hâlâ “Henüz değerlendirilmedi”. |
| Kanıt | Tüm 16 audit dosyasında faz başlığı/komut izi taraması; `CURRENT_PHASE` ve `CHANGELOG_AUDIT` sırası; `PRODUCTION_READINESS` alanları |
| Faz 14’e etkisi | `NOT_READY_FOR_PHASE_14`; blocker kapsamı ve karar kanıt seti eksik |
| Gerekli düzeltme | Daha önceki Faz 11/12 statik çalışmalarını kaynak kanıtlarıyla audit dosyalarına persist et; durumları static-only/blocked olarak doğru sınıflandır; Faz 13.5’i tekrar çalıştır. |
| Düzeltildi mi | Fixed — bu Faz 13.5 revalidation ile formal kayıtlar, bütünlük ve çapraz eşleşmeler doğrulandı |

### EVIDENCE-001 — Faz 9 remediation çalışıyor olarak doğrulanmış değil

| Alan | Değer |
|---|---|
| Durum / önem | Doğrulandı / Yüksek |
| Etkilenen fazlar | Faz 9, Faz 13.5 |
| Etkilenen audit dosyaları | `BUGS`, `FINDINGS`, `TEST_MATRIX`, `COMMAND_LOG`, `CHANGELOG_AUDIT` |
| Beklenen kayıt | Failing regression → code change → passing target/related test artefaktı |
| Gerçek kayıt | Source invariant fail/pass ve `py_compile` çalıştırıldığı raporlanmış; `.audit/runtime/faz9/` içinde kanıt dosyası yok, normal pytest/runtime queue/crash testi yok. Kod diff’i remediation’ı içeriyor. |
| Kanıt | `git diff -- mesa_memory/consolidation/loop.py`; boş `.audit/runtime/faz9/`; command/test kayıtları |
| Faz 14’e etkisi | DLQ-001 yalnız `Partially fixed / Fixed but not verified`; blocker açık kalır. |
| Gerekli düzeltme | İzole durable queue fixture ile >batch, tenant, crash-before/after-ack, poison ve multi-process claim testleri; kalıcı test dosyası/CI kanıtı. |
| Düzeltildi mi | Hayır; bu görevde TEST_MATRIX sınıflandırması düzeltildi. |

### RECORD-001 — Açık P0/P1 toplamı kapsamlı ve tek kaynaktan güvenilir değil

| Alan | Değer |
|---|---|
| Durum / önem | Doğrulandı / Yüksek |
| Etkilenen fazlar | Faz 9–13.5 |
| Etkilenen audit dosyaları | `CURRENT_PHASE`, `FINDINGS`, `BLOCKERS` |
| Beklenen kayıt | Benzersiz açık bulguların güncel, yeniden üretilebilir toplamı |
| Gerçek kayıt | FINDINGS öncelik alanlarında 5 benzersiz P0 ve 30 benzersiz P1 var; tarihsel CURRENT_PHASE Faz 9 satırı 5/28 diyor. Faz 11/12 kayıtları eksik olduğundan bu sayılar yalnız bilinen minimumdur. |
| Faz 14’e etkisi | Kapsamlı blocker sayısı güvenilir değil; Faz 14 başlamamalı. |
| Gerekli düzeltme | Faz 11/12 persistence sonrası canonical status index/sayım tablosu oluştur. |
| Düzeltildi mi | Fixed — canonical indeks/sayım ve P0 blocker eşleşmesi bu revalidation ile doğrulandı. |

### RECORD-002 — DLQ-001 aynı ID ile ana bulgu ve durum güncellemesi başlığı olarak iki kez geçiyor

| Alan | Değer |
|---|---|
| Durum / önem | Doğrulandı / Orta |
| Etkilenen fazlar | Faz 7, Faz 9 |
| Etkilenen audit dosyaları | `FINDINGS.md` |
| Beklenen kayıt | Bir canonical finding başlığı ve tarihçeli durum güncellemesi |
| Gerçek kayıt | `### DLQ-001` iki başlıkta geçiyor; ikinci kayıt teknik olarak yeni bulgu değil, Faz 9 durum güncellemesidir. |
| Faz 14’e etkisi | Sayım araçları duplicate üretebilir; teknik sorun çoğaltılmamalıdır. |
| Gerekli düzeltme | Eski kaydı silmeden ikinci başlığı “DLQ-001 durum güncellemesi” olarak canonical olmayan tarihçe etiketiyle normalize et. |
| Düzeltildi mi | Fixed — ikinci heading silinmeden canonical olmayan duplicate olarak etiketlendi ve canonical sayımdan dışlandı. |


## Faz 11 — Migration, Backup, Restore ve Disaster Recovery formal bulguları (2026-07-19)

Bu kayıt yeni çalışma değildir. Daha önceki statik Faz 11 kanıtlarının formal persistence kaydıdır; migration, backup, restore veya test çalıştırılmadı.

### MIG-001 — Alembic başlangıç şeması legacy şema driftini tespit etmeden sürüm ilerletebilir

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Kritik / P0 |
| Kategori / release blocker | SQLite migration güvenliği / Evet |
| Kanıt seviyesi | Static-only; runtime migration testi çalıştırılmadı |
| Dosya / sembol | `alembic/versions/4933fb5fd0ea_initial_schema.py`; `mesa_storage/schemas.py:initialize_schema` |
| Kanıt | İlk migration `CREATE TABLE IF NOT EXISTS` kullanır; startup Alembic `upgrade head` çağırır. Legacy/pre-Alembic şema ile revision kaydının gerçek şemayı fingerprint etmesine dair kanıt yoktur. |
| Risk / gerekli doğrulama | Uyuşmayan mevcut şema version table ilerlese de eksik kalabilir. Önceki-release fixture, idempotency, failure/rollback ve schema-fingerprint postflight testi gerekir. |

### MIG-002 — Kùzu şema değişikliklerinin version, lock ve postflight protokolü yok

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Yüksek / P1 |
| Kategori / release blocker | Graph migration / Evet |
| Kanıt seviyesi | Static-only |
| Dosya / sembol | `mesa_storage/kuzu_setup.py` |
| Kanıt | Runtime create/alter akışı broad warning ile devam eder; version marker, migration lock, postflight doğrulama veya downgrade kaydı görülmedi. |
| Risk / gerekli doğrulama | Çoklu instance veya yarım şema değişiminde graph uyumluluğu belirsizdir. Kilitli, idempotent, restart-safe migration fixture’ı gerekir. |

### MIG-003 — Kùzu bulk migration tekrar çalıştırmada duplicate ve kesintide resume riski taşıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Yüksek / P1 |
| Kategori / release blocker | Bulk migration / Evet |
| Kanıt seviyesi | Static-only |
| Dosya / sembol | `scripts/migrate_to_kuzu.py` |
| Kanıt | Bulk aktarımın idempotency/progress/lock/resume/reconcile sınırı yoktur; `--wipe` hedef graph için yıkıcı seçenektir. |
| Risk / gerekli doğrulama | Retry veya kesinti duplicate/eksik graph üretebilir. İki kez çalıştırma, kesinti-resume, source-target count/checksum ve rollback testi gerekir. |

### MIG-004 — Raw-log agent backfill eksik payload’da tenant dışı sentinel yazıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Kritik / P0 |
| Kategori / release blocker | Tenant migration / Evet |
| Kanıt seviyesi | Static-only |
| Dosya / sembol | `scripts/migrate_raw_logs_agent_id.py`; `mesa_storage/dao.py` raw-log agent doğrulaması |
| Kanıt | Script tüm raw logları gezer, eksik payload için `__unset__` kullanır; DAO bu sentinel’i kabul etmez. Malformed kaydın skip edilmesi başarı/commit bütünlüğüyle bağlanmamıştır. |
| Risk / gerekli doğrulama | Tenant ownership backfill’i eksik veya geçersiz kalabilir. Sentetik legacy fixture, dry-run raporu, reject listesi ve resume/rollback testi gerekir. |

### BACKUP-001 — Production backup/restore runbook ve doğrulanmış bütünlük protokolü yok

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Kritik / P0 |
| Kategori / release blocker | Backup / disaster recovery / Evet |
| Kanıt seviyesi | Static-only; restore çalıştırılmadı |
| Dosya / sembol | `tests/go_live_proofs/test_backup_restore.py`; `scripts/down_migrate.py` |
| Kanıt | Test canlı storage kopyalayıp orijinali siler/geri yükler ve Kùzu node sayısını kontrol eder; LanceDB, raw logs/WAL, manifest, checksum, encryption, retention veya offsite copy doğrulaması yoktur. `down_migrate.py` backup başarısız olsa da graph drop yoluna sahiptir. |
| Risk / gerekli doğrulama | Geri yüklenebilir, tutarlı ve güvenli backup kanıtı yoktur. İzole snapshot/manifest/checksum/encryption/retention ve tam üç-store restore drill gerekir. |

### RESTORE-001 — Reconciliation ve repair kapsamı bounded/recent olduğu için tam restore eşitliği kanıtlanmıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Yüksek / P1 |
| Kategori / release blocker | Restore reconciliation / Evet |
| Kanıt seviyesi | Static-only |
| Dosya / sembol | Mevcut reconciliation/repair yolları; `tests/go_live_proofs/test_backup_restore.py` |
| Kanıt | Mevcut kontroller sınırlı/recent kapsamlıdır; Kùzu, queue/WAL, tombstone ve Lance/SQLite tam eşitliği için uçtan uca repair kanıtı yoktur. |
| Risk / gerekli doğrulama | Kısmi restore sessiz veri ayrışmasıyla tamamlanmış görünebilir. Tam inventory/reconcile/repair ve repeatable restore drill gerekir. |

### TEST-002 — Migration ve DR için release-grade fixture/idempotency/rollback testi yok

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Yüksek / P1 |
| Kategori / release blocker | Migration/DR test sistemi / Evet |
| Kanıt seviyesi | Static-only |
| Kanıt | Prior-version fixture, migration lock, idempotency, interruption/resume, rollback, backup manifest ve tam restore reconciliation testleri bulunamadı; bu görevde test çalıştırılmadı. |
| Risk / gerekli doğrulama | Migration ve DR değişiklikleri release kapısında kanıtlanamaz. |


## Faz 12 — Docker, CI/CD ve operasyonel production hazırlığı formal bulguları (2026-07-19)

Bu kayıt yeni Docker/CI çalışması değildir. Önceden elde edilmiş statik Faz 12 kanıtlarının formal persistence kaydıdır. Local Docker kurulu değildi; build, Compose ve runtime testi çalıştırılmadı.

### DOCKER-001 — Compose volume yolları API’nin gerçek SQLite/Lance yollarını kalıcılaştırmıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Kritik / P0 |
| Kategori / release blocker | Container persistence / Evet |
| Kanıt seviyesi | Static-only; Docker runtime Not tested |
| Dosya / sembol | `docker-compose.yml:15-17`; `mesa_memory/api/server.py:91-95`; `Dockerfile:VOLUME` |
| Kanıt | Compose SQLite/Lance mount’larını `/app/storage/sqlite` ve `/app/storage/lancedb` altına bağlar; server `mesa.db` ve `vector.lance` için doğrudan `/app/storage` kullanır. |
| Risk / gerekli doğrulama | SQLite/Lance/raw log state’i anonymous volume/container filesystem’de kalabilir. İzole up→write→down→up persistence drill gerekir. |

### DOCKER-002 — Build context `.env.*` ve audit/runtime artefaktlarını dışlamıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Yüksek / P1 |
| Kategori / release blocker | Container secret/context hijyeni / Evet |
| Kanıt seviyesi | Static-only |
| Dosya / sembol | `.dockerignore`; `Dockerfile` `COPY . .` |
| Kanıt | `.dockerignore` `.env.*`, `.audit` ve çalışma artefaktlarını kapsamlı dışlamaz; Dockerfile tüm build context’i kopyalar. |
| Risk / gerekli doğrulama | Secret veya gereksiz audit/sonuç dosyası image context’ine girebilir. Build context inspect ve image filesystem assertion gerekir. |

### DOCKER-003 — Image build dependency/model indirmeleri reproducible ve supply-chain kontrollü değil

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Yüksek / P1 |
| Kategori / release blocker | Image reproducibility / Evet |
| Kanıt seviyesi | Static-only; Docker build Not tested |
| Dosya / sembol | `Dockerfile` |
| Kanıt | Base image digest ile pinli değil; apt/pip seti pinli değil; `pip install ".[adapters,ml]"` ve build sırasında spaCy download checksum/provenance doğrulaması olmadan yapılır. |
| Risk / gerekli doğrulama | Aynı kaynak farklı image/dependency üretebilir. Digest/pin/SBOM/vulnerability ve offline/rebuild kanıtı gerekir. |

### CONFIG-001 — Compose varsayılan mock provider ve `.env` yüklemesi fail-closed değil

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Yüksek / P1 |
| Kategori / release blocker | Deployment configuration / Evet |
| Kanıt seviyesi | Static-only |
| Dosya / sembol | `docker-compose.yml`; config/adapter zinciri |
| Kanıt | Compose `env_file: .env` kullanır; mock provider varsayılanı kritik log dışında güvenli fail-closed davranışa bağlanmış görünmez. |
| Risk / gerekli doğrulama | Yanlış/eksik production config ile istenmeyen adapter veya secret kapsamı oluşabilir. Explicit profile/preflight negative suite gerekir. |

### HEALTH-001 — `/health/init` worker liveness, lag ve failure durumunu readiness’e katmıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Yüksek / P1 |
| Kategori / release blocker | Health/readiness / Evet |
| Kanıt seviyesi | Static-only |
| Dosya / sembol | `mesa_memory/api/server.py` lifespan; `/health/init` |
| Kanıt | Worker start/schedule hataları loglanıp devam edebilir; health DAO/storage durumunu bildirir fakat worker task done/error/lag/DLQ/backlog sinyallerini readiness kararına bağlamaz. |
| Risk / gerekli doğrulama | API ready görünürken kritik async iş kapalı/geride kalabilir. Worker fault-injection ve readiness recovery testi gerekir. |

### CI-002 — CI canary package artifact yerine checkout kaynaklarını doğruluyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Yüksek / P1 |
| Kategori / release blocker | CI package verification / Evet |
| Kanıt seviyesi | Static-only |
| Dosya / sembol | `.github/workflows/ci.yml` package/dev-entry canary adımları |
| Kanıt | Canary repo source checkout’ı üzerinde çalışır; wheel/sdist artifact bulunmadı ve install edilmiş dağıtımın içerik/entry-point doğrulamasına kanıt yoktur. |
| Risk / gerekli doğrulama | Paketlenmiş release ile kaynak ağacı farklı davranabilir. Clean env wheel+sdist install/smoke gerekir. |

### RELEASE-001 — Release/rollback otomasyonu güncel güvenli deployment kanıtı taşımıyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Confirmed / Yüksek / P1 |
| Kategori / release ve rollback / Evet |
| Kanıt seviyesi | Static-only |
| Dosya / sembol | Release scriptleri ve CI workflow’ları |
| Kanıt | Release scripti güncel deployment topolojisi/DR gate’leriyle hizalı değildir; rollback, migration compatibility, backup doğrulaması ve supply-chain release kanıtı yoktur. |
| Risk / gerekli doğrulama | Deploy/rollback sırasında schema veya storage uyumsuzluğu fark edilmeden ilerleyebilir. Immutable artifact, staged deploy, rollback rehearsal ve migration/backup gate gerekir. |


## Faz 13.5 — Canonical finding durum indeksi (2026-07-19)

Bu indeks sayımın tek kaynağıdır. `DLQ-001` ikinci heading’i tarihçe kaydıdır ve canonical değildir. Tarihsel toplamlar korunur; bu tablo onları değiştirmez. Kanıt seviyesi `S` = static-only, `A` = audit kayıt kanıtı; runtime test çalıştırılmadıkça `Verified` anlamına gelmez.

| ID | Başlık (kısa) | Önem | Öncelik | Canonical durum | Release blocker | Kanıt | Son faz |
|---|---|---:|---:|---|---|---|---:|
| ENV-001 | venv baseline | Yüksek | P1 | Confirmed open | Evet | S | 1 |
| BOOT-001 | API readiness baseline | Yüksek | P1 | Confirmed open | Evet | S | 1 |
| SEC-001 | dotenv izolasyonu | Yüksek | P1 | Confirmed open | Evet | S | 1 |
| OPS-001 | dependency manifest | Yüksek | P1 | Confirmed open | Evet | S | 1 |
| OPS-002 | command evidence ayrıntısı | Orta | P2 | Confirmed open | Hayır | A | 1 |
| ARCH-001 | worker process iddiası | Yüksek | P1 | Confirmed open | Hayır | S | 2 |
| ARCH-002 | shutdown lifecycle | Orta | P2 | Confirmed open | Hayır | S | 6 |
| ARCH-003 | CWD debug yazımı | Yüksek | P1 | Confirmed open | Evet | S | 5 |
| ARCH-004 | MCP storage bypass | Yüksek | P1 | Confirmed open | Evet | S | 5 |
| DOC-001 | dev entry-point iddiası | Orta | P2 | Confirmed open | Hayır | S | 2 |
| DOC-002 | Compose mount dokümantasyonu | Yüksek | P1 | Confirmed open | Hayır | S | 2 |
| FLOW-001 | restart-safe cold path | Yüksek | P1 | Confirmed open | Evet | S | 7 |
| DATA-001 | Kùzu purge lifecycle/journal | Yüksek | P1 | Fixed but not verified | Evet | E2 WAVE-002 | WAVE-002 |
| SDK-001 | MCP URL drift | Yüksek | P1 | Confirmed open | Evet | S | 4 |
| SDK-002 | purge response drift | Yüksek | P1 | Confirmed open | Evet | S | 4 |
| FLOW-002 | session finalization | Orta | P2 | Confirmed open | Hayır | S | 4 |
| SEC-002 | cross-tenant authorization | Kritik | P0 | Fixed but not verified | Evet | E1 + E2 clean restart | WAVE-001 |
| SEC-003 | credential persistence | Yüksek | P1 | Confirmed open | Evet | S | 5 |
| SDK-003 | async auth drift | Yüksek | P1 | Confirmed open | Evet | S | 5 |
| DATA-002 | graph failure split-brain | Kritik | P0 | Fixed but not verified | Evet | E2 WAVE-002 | WAVE-002 |
| DATA-003 | zero embedding persist | Yüksek | P1 | Confirmed open | Hayır | S | 4 |
| DATA-004 | vector fallback duplicate | Yüksek | P1 | Fixed but not verified | Hayır | E2 WAVE-002 | WAVE-002 |
| LOGIC-001 | cold status scope | Yüksek | P1 | Confirmed open | Hayır | S | 4 |
| LOGIC-002 | false consolidated | Yüksek | P1 | Confirmed open | Evet | S | 7 |
| LOGIC-003 | quarantine bypass | Yüksek | P1 | Confirmed open | Evet | S | 4 |
| PERF-001 | metrics cardinality | Orta | P2 | Confirmed open | Hayır | S | 4 |
| RLS-001 | cross-tenant policy state | Yüksek | P1 | Confirmed open | Evet | S | 5 |
| INPUT-001 | unbounded metadata | Yüksek | P1 | Confirmed open | Evet | S | 5 |
| CI-001 | floating security action | Orta | P2 | Confirmed open | Hayır | S | 5 |
| DATA-005 | alignment WAL write loss | Kritik | P0 | Confirmed open | Evet | S | 6 |
| CONC-002 | raw-log atomic claim | Yüksek | P1 | Confirmed open | Evet | S | 7 |
| CONC-003 | mutable routing race | Yüksek | P1 | Confirmed open | Hayır | S | 6 |
| DLQ-001 | DLQ claim/tenant context | Kritik | P0 | Partially fixed; fixed but not verified | Evet | S + source invariant | 9 |
| QUEUE-001 | queue backpressure | Yüksek | P1 | Confirmed open | Evet | S | 7 |
| WORKER-001 | worker health | Yüksek | P1 | Confirmed open | Evet | S | 7 |
| TEST-001 | P0 E2E release gate | Kritik | P0 | Confirmed open | Evet | S | 8 |
| COVERAGE-001 | SDK coverage omission | Yüksek | P1 | Confirmed open | Evet | S | 8 |
| PERF-002 | retrieval O(N) | Yüksek | P1 | Confirmed open | Evet | S | 10 |
| PERF-003 | unbounded worker scan | Yüksek | P1 | Confirmed open | Evet | S | 10 |
| PERF-004 | search N+1 | Orta | P2 | Confirmed open | Hayır | S | 10 |
| STAGE-001 | API/worker role isolation | Yüksek | P1 | Confirmed open | Evet | S | 13 |
| CONFIG-002 | fail-closed dotenv config | Yüksek | P1 | Confirmed open | Evet | S | 13 |
| MIG-001 | Alembic legacy drift | Kritik | P0 | Confirmed open | Evet | S | 11 |
| MIG-002 | Kùzu schema protocol | Yüksek | P1 | Confirmed open | Evet | S | 11 |
| MIG-003 | bulk migration resume | Yüksek | P1 | Confirmed open | Evet | S | 11 |
| MIG-004 | raw-log tenant backfill | Kritik | P0 | Confirmed open | Evet | S | 11 |
| BACKUP-001 | DR backup/restore protocol | Kritik | P0 | Confirmed open | Evet | S | 11 |
| RESTORE-001 | full reconciliation | Yüksek | P1 | Confirmed open | Evet | S | 11 |
| TEST-002 | migration/DR release tests | Yüksek | P1 | Confirmed open | Evet | S | 11 |
| DOCKER-001 | volume path persistence | Kritik | P0 | Confirmed open | Evet | S | 12 |
| DOCKER-002 | build context hygiene | Yüksek | P1 | Confirmed open | Evet | S | 12 |
| DOCKER-003 | image reproducibility | Yüksek | P1 | Confirmed open | Evet | S | 12 |
| CONFIG-001 | Compose fail-closed config | Yüksek | P1 | Confirmed open | Evet | S | 12 |
| HEALTH-001 | readiness omits workers | Yüksek | P1 | Confirmed open | Evet | S | 12 |
| CI-002 | artifact verification gap | Yüksek | P1 | Confirmed open | Evet | S | 12 |
| RELEASE-001 | release/rollback gap | Yüksek | P1 | Confirmed open | Evet | S | 12 |
| AUDIT-INT-001 | missing formal records | Kritik | Audit-P0 | Verified resolved | Hayır | A | 13.5 |
| EVIDENCE-001 | Faz 9 runtime evidence | Yüksek | Audit-P1 | Confirmed open | Evet | A | 13.5 |
| RECORD-001 | canonical count gap | Yüksek | Audit-P1 | Verified resolved | Hayır | A | 13.5 |
| RECORD-002 | DLQ heading duplication | Orta | Audit-P2 | Verified resolved | Hayır | A | 13.5 |

### Canonical sayım — yalnız teknik canonical finding kayıtları

| Ölçüt | Sayı | Not |
|---|---:|---|
| Açık Kritik | 9 | Tümü P0; `DLQ-001` kısmi düzeltmeye rağmen runtime doğrulamasız açık risk |
| Açık Yüksek | 40 | P1 canonical teknik kayıtlar |
| Açık P0 | 9 | Duplicate olmayan canonical teknik kayıtlar |
| Açık P1 | 40 | Duplicate olmayan canonical teknik kayıtlar |
| Release blocker | 43 | 9 P0 + 34 P1 teknik blocker; audit-bütünlüğü blocker’ı ayrı tutulur |
| Fixed but not verified | 2 | `DLQ-001` ve `SEC-002`; ikisi de açık risk sayılır |
| Duplicate | 1 | `DLQ-001` Faz 9 durum heading’i; teknik finding değildir |
| False positive | 0 | — |
| Resolved and verified | 0 | — |

Audit-bütünlüğü kayıtları (`AUDIT-INT-001`, `EVIDENCE-001`, `RECORD-001`, `RECORD-002`) ürün teknik P0/P1 toplamına dahil edilmemiştir. Historical minimum count — superseded: Tarihsel Faz 13.5 toplamı 5 P0/30 P1, formal Faz 11/12 kayıtları eksik olduğu zamanki non-canonical minimumdur.


### Faz 13.5 revalidation durum güncellemesi (2026-07-19)

| ID | Canonical durum | Revalidation kanıtı | Güncel etki |
|---|---|---|---|
| AUDIT-INT-001 | Verified resolved | Faz 11/12 formal kayıtları, command/changelog, findings/blockers/plan/test/readiness izleri diskte; 16 audit dosyası okunabilir ve `git diff --check` temiz | Kritik audit blocker kapandı |
| RECORD-001 | Verified resolved | Canonical indeks 9 P0, 40 P1, 43 teknik release blocker üretir; P0 blocker eşleşmesi kontrol edildi | Tarihsel 5/30 minimumu noncanonical olarak korunur |
| RECORD-002 | Verified resolved | İkinci DLQ heading’i `Duplicate of DLQ-001 canonical heading` olarak işaretli; canonical sayımda tek kez alınır | Duplicate yalnız tarihçe kaydıdır |
| EVIDENCE-001 | Confirmed open | Faz 9 diff ve static invariant vardır; kalıcı failing/passing pytest ve runtime/integration kanıtı yoktur | DLQ-001 `Partially fixed / Fixed but not verified` olarak açık kalır |

Bu güncelleme teknik bug durumlarını değiştirmez; yalnız audit bütünlüğü durumlarını günceller.


## Faz 14 — Finding karar etkisi (2026-07-19)

Canonical teknik durum değişmedi: 9 P0, 40 P1 ve 7 P2 açıktır; 43 kayıt release blocker’dır. `DLQ-001` ve `SEC-002` fixed-but-not-verified teknik bulgulardır; verified-resolved teknik bulgu ve false positive yoktur. Audit bütünlüğü kayıtları ürün teknik sayılarına dahil değildir. Bu set `NO_GO` kararının finding kaynağıdır.

## Kanonik operational durum kullanımı (2026-07-19)

Bu dosyanın Faz 13.5 canonical finding durum indeksi için durum sözlüğü `.audit/README.md` içindedir. `Confirmed open` açık doğrulanmış teknik kayıt; `Partially fixed / Fixed but not verified` yalnız DLQ-001 remediation durumudur. `Verified resolved` durumundaki audit-bütünlüğü kayıtları teknik P0/P1 veya teknik release blocker toplamına dahil değildir. Tarihsel `5 P0 / 30 P1` minimumu superseded/non-canonical olarak korunur.

#### WAVE-001 reconciliation — 2026-07-19T03:40:13+03:00

`SEC-002` was deterministically reproduced at E2 (`/session/start` returned 200 for an authenticated unmapped principal) and received a minimal source remediation: API-key authentication attaches a configured server-side principal, RBAC holds explicit principal→agent permissions, and `start_session` requires `SESSION_CREATE` before granting the new session access. The target test and 30 focused RBAC/session/router tests pass. Status is `Fixed but not verified`, not closed: E3 two-principal HTTP/runtime evidence, provisioning/legacy migration, all endpoint coverage, and SDK/MCP contract proof remain absent. It stays a P0 release blocker; P0/P1/release-blocker totals do not decrease. `LOGIC-001` is not remediated by this wave.

#### WAVE-001 direct caller reconciliation — 2026-07-19T03:42:50+03:00

The alternate `scripts/run_server.py` composition root was also updated after proof that it mounted the memory router with API-key authentication but without principal context. Its normal authenticated path now supplies the same configured active principal; direct valid/invalid synthetic-key middleware checks pass. The explicit `--no-auth` development mode remains non-production and does not contribute E3 evidence.

## WAVE-001 clean restart remediation update (2026-07-19)

| Alan | Sonuç |
|---|---|
| Finding | `SEC-002` |
| Canonical durum | Fixed but not verified — açık P0/release blocker |
| Clean-restart kanıtı | Mevcut uncommitted authorization source hashleri tarihsel recorded after-hashlerle eşleşti; bu run’da 5 hedef ve 33 ilgili E2 test geçti. |
| Doğrulanan davranış | Unmapped principal `/session/start` için 403 alır; mapped active principal başarılıdır; inactive principal 401 alır; READ-only mapping `SESSION_CREATE` vermez. |
| Kapanmama gerekçesi | Pre-fix 200 bu clean run’da gözlenmedi; E3 isolated HTTP runtime, SDK/MCP contract, provisioning/principal lifecycle ve diğer session/status/purge yolları tamamlanmadı. |
| Sayım etkisi | Açık P0=9, açık P1=40, teknik release blocker=43 değişmedi; fixed-but-not-verified=2. |

## WAVE-002 remediation reconciliation

- `DATA-002` and `DATA-004` have E2 deterministic fault-injection evidence and are `Fixed but not verified`; neither is closed. Missing evidence includes real SQLite/Kuzu/Lance mutation, SQLite-commit failure, partial-bulk compensation, restart/recovery and E3 runtime proof.
- `DATA-001` remains `Confirmed open`. No new finding ID was created: a Kuzu purge lifecycle must preserve stated retention/audit semantics and needs a compensable lifecycle/outbox/repair decision.
- Canonical P0/P1 totals, technical blocker count and final `NO_GO` do not change.

## WAVE-002 DATA-001 approved-design reconciliation

Canonical ADR: SQLite is the purge mutation coordinator; Kuzu and vector are downstream non-canonical projections. The implemented state machine is `PREPARED → TOMBSTONED → KUZU_APPLIED → VECTOR_APPLIED → VERIFIED → FINALIZED`, with bounded retry to `RETRY_PENDING`/`BLOCKED`. Exact scope, tombstone visibility, graph-before-vector ordering, idempotent recovery, finalization duplicate rejection and pre-downstream rollback boundary are E2-tested. Real Kuzu/Lance semantics, process crash/restart, backup-restore ledger reconciliation and E3 are absent, so DATA-001 is not closed.

## WAVE-003 remediation reconciliation

- `DATA-005` ve `CONC-002` için E2 deterministic SQLite claim/WAL contractı eklendi. Pre-fix 2 failure; post-fix 2 passed. Raw-log ve WAL kayıtları artık claim token + owner + expiry ile guarded transition/ack kullanır; alignment mutasyon bariyeri snapshot→promotion aralığını kapsar.
- `DATA-005`: bulk `DELETE FROM lancedb_wal` kaldırıldı; external vector I/O SQLite write transaction dışında, success sonrası per-row ACK ile yürür. `CONC-002`: gerçek DAO çağrısı atomic claim kullanır ve terminal transition owner/token ile fence edilir.
- E3 real-store/process crash/restart, caller side-effect exact-once, worker dispatcher ve cross-process alignment lease kanıtı yoktur. Bu yüzden iki finding `Fixed but not verified` olarak açık kalır; canonical P0=9, P1=40, blocker=43 ve `NO_GO` değişmez.

## WAVE-004 remediation reconciliation

`DLQ-001` için E2 JSONL durable claim/lease/ACK/NACK/poison contractı eklendi ve 52 isolated test geçti; tenant metadata korunur, raw exception metni normalize edilmez ve opaque batch sonucu ACK edilmez. `QUEUE-001`, `WORKER-001` ve `FLOW-001` için raw-log dispatcher, admission/backpressure ve readiness/supervision uygulanmadığından WAVE-004 `PARTIALLY_COMPLETE` kalır. Canonical sayılar ve `NO_GO` değişmez.

## WAVE-004A reconciliation

FLOW-001 için E2 SQLite dispatch intent→queue record→receipt/recovery zinciri eklendi. `DEFERRED` raw-log receipt yoksa tenant scope ile recovery edilir; idempotency source record üzerinden enforced edilir. Runtime dispatcher/E3 yoktur; FLOW-001 açık `Fixed but not verified` kalır. Canonical sayılar değişmez.

## WAVE-004B reconciliation

`QUEUE-001` için `DEC-REM-008` altında merkezi typed admission policy, deterministic UTF-8 envelope byte accounting, global/per-tenant count+byte, in-flight/retry limits ve SQLite transaction içinde durable enqueue eklendi. E2’de 9 admission/HTTP/restart senaryosu, izole component E3’te concurrent limit/finalize sonrası reopen/restart accounting geçti. API/worker runtime E3 profile/dotenv gate nedeniyle çalıştırılmadı; finding `Fixed but not verified` ve release blocker olarak açık kalır. Canonical sayılar değişmez.

## WAVE-004C/D reconciliation

`WORKER-001` için bounded `WorkerSupervisor`, crash görünürlüğü, restart budget, controlled shutdown ve worker-aware `/health/init` E2 ile eklendi; API-only/worker-only profile WAVE-005 bağımlılığı nedeniyle `Fixed but not verified` kalır. `DLQ-001` remaining scope için durable SQLite dispatch completion receipt/fenced ACK E2 ile eklendi; gerçek JSONL DLQ process restart/lease E3 yoktur ve finding açık kalır. 9 P0, 40 P1, 43 teknik blocker ve `NO_GO` değişmez.

## WAVE-005 and verification-wave reconciliation

`SEC-001` implicit dotenv discovery kaldırıldı; test-isolated profile explicit dotenv/model/provider kapalı ve lab-root-bound E2/E3 ile doğrulandı. CONFIG/STAGE/BOOT/HEALTH scoped evidence aldı; combined/deployment matrix eksik. WAVE-001-V HTTP, WAVE-003-V lease/fence restart ve WAVE-004-V dispatch/admission/completion restart geçti; eksik scenario’lar nedeniyle canonical findings kapanmaz. Canonical 9 P0, 40 P1, 43 blocker ve `NO_GO` değişmez.

## Continuation matrix update — WAVE-001-V/WAVE-005

API-only `/health/init` profile-aware hale getirildi; intended workerless API `ready`, worker-required profiles degraded/blocked gate’ini korur. WAVE-001-V gerçek route matrix mapped=200, unmapped/READ-only=403, invalid/inactive=401 kanıtıyla genişledi. Foreign-session/tenant status-purge coverage, W3 WAL/alignment ve W4 JSONL DLQ process-crash matrixi hâlâ eksik; canonical sayılar değişmez.


## Continuation E3 matrix update — 2026-07-19

Yeni finding ID açılmadı. `SEC-002`, `DATA-005`, `CONC-002` ve `DLQ-001` mevcut kayıtları için kanıt genişletildi; hiçbiri `Verified resolved` değildir. Principal-session binding, malformed-tail quarantine ve duplicate `queue_id` reddi minimal remediasyonlardır. Canonical P0/P1/release-blocker sayımı değişmez.


## Continuation contract/alignment/crash update — 2026-07-19

Yeni canonical finding ID açılmadı. Async SDK API-key header ve purge response type uyumsuzluğu gerçek route reproducer ile düzeltildi; bu `SEC-002` cross-boundary kanıtını genişletir. `FLOW-002` mevcut bulgu olarak confirmed open kalır. W3 real-store ve W4 injected-crash evidence, `DATA-005`/`CONC-002`/`DLQ-001` için FBNV durumunu kaldırmaya yeterli değildir.


### WAVE-003-V / WAVE-004-V continuation — 2026-07-19

`DATA-005` / `CONC-002` için WAL’a durable mutation/idempotency, vector/graph projection state, fence epoch, bounded retry ve reconciliation gate eklendi. Vector failure, graph failure, stale fence ve crash-after-side-effect unit sözleşmesi geçti. Gerçek LanceDB/Kùzu E3’de Kùzu composite-id gözlemi önce `GRAPH_MISSING` verdi; provider exact-scope doğrulamasına düzeltildi ancak son E3 tekrar edilmedi. Findingler açık ve `FIXED_NOT_VERIFIED` kalır.

`DLQ-001` için configured JSONL queue path’leri trusted root altında validate edilir; root/symlink/escape testleri geçti. Consumer side-effect → receipt → JSONL ACK restart reconciliation henüz source’da tam entegre değildir; finding açık, canonical sayılar değişmez.


### W3/W4 final E3 evidence — 2026-07-19

Gerçek W3 store E3 core matrixi geçti; full reconciliation classification eksik olduğundan `DATA-005`/`CONC-002` açık. W4 mevcut JSONL ve SQLite coordinator’larıyla E3 harnessi geçti, fakat bridge production consumer akışında otomatik değil; `DLQ-001` açık.

## Master closure canonical reconciliation — 2026-07-20

Bu bölüm önceki durum güncellemelerini supersede eden final remediation indeksidir; finding ayrıntıları silinmemiştir. Satır-bazlı 56 teknik kayıt `.audit/remediation/FINAL_FINDING_MATRIX.md` içindedir.

| Ölçüm | Final |
|---|---:|
| Unique teknik finding | 56 |
| Verified resolved | 28 |
| Açık teknik finding | 28 |
| Açık P0 / P1 / P2 | 4 / 20 / 4 |
| Fixed but not verified | 7 |
| Açık teknik release blocker | 21 |

`SEC-002`, `DATA-002`, `DATA-005`, `DLQ-001` ve `BACKUP-001` P0 kayıtları kanıtla `VERIFIED_RESOLVED` oldu. `TEST-001`, `MIG-001`, `MIG-004` ve `DOCKER-001` açık P0’dır. `EVIDENCE-001`, `RECORD-001`, `RECORD-002` audit-only ve `VERIFIED_RESOLVED`; teknik sayım dışıdır. Faz 14 `NO_GO`.
# Fast zero-closure disposition — 2026-07-20

Independent Audit'in 30 `OPEN`/`FIXED_NOT_VERIFIED` teknik kaydı `.audit/remediation/FINAL_FINDING_MATRIX.md` içinde satır bazında yeniden sınıflandırıldı: 22 `VERIFIED_RESOLVED`, 7 `IMPLEMENTED_EXTERNAL_VERIFICATION_PENDING`, 1 `N/A`. Source/config açık finding kalmadı; independent audit tarihçesi korunur.
