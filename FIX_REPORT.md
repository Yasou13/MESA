# MESA FIX REPORT

This document tracks the resolution of the 49 findings identified in the MESA audit.

## FAZ R-1 — Kritik

### [R-01] Vektör silmede `agent_id` yok (IDOR)
- **Kaynak Rapor / Orijinal ID:** REPORT (3-1)
- **Önem:** Kritik
- **Konum:** `vector_engine.py`
- **Uygulanan Fix:** `soft_delete` ve `hard_delete` metodlarına `agent_id` parametresi eklendi ve `WHERE` koşulunda doğrulandı. `dao.py` ve `ingestion_worker.py` vb. çağrı noktaları güncellendi.
- **Adım 1 Sonucu (Orijinal Hata Çözüldü mü?):** [KANIT: repro_r01_v2.py çıktısı "After wrong delete: 1", "After correct delete: 0"] — ✅ Doğrulandı
- **Adım 2 Sonucu (Regresyon Var mı?):** [Kontrol edilen call site'lar: dao.py, ingestion_worker.py, barerag_adapter.py ve test suite update edildi] — ✅ Regresyon Yok
- **Durum:** ÇÖZÜLDÜ

### [R-02] Soft-delete ASCII tarih kıyas hatası
- **Kaynak Rapor / Orijinal ID:** REPORT (4-2)
- **Önem:** Kritik
- **Konum:** `maintenance.py`
- **Uygulanan Fix:** `_purge_sqlite_records` metodunda SQLite `DELETE` sorgusundaki `invalid_at < ?` koşulu `datetime(invalid_at) < datetime(?)` olarak düzeltildi.
- **Adım 1 Sonucu (Orijinal Hata Çözüldü mü?):** [KANIT: repro_r02.py çıktısı] — ✅ Doğrulandı. Gelecekteki veya farklı formattaki tarihler yanlış silinmiyor.
- **Adım 2 Sonucu (Regresyon Var mı?):** [Kontrol edilen test: `test_maintenance_worker.py`] — ✅ Regresyon Yok. 27 test başarıyla geçti.
- **Durum:** ÇÖZÜLDÜ

### [R-03] Dual-Write Saga erken commit ihlali
- **Kaynak Rapor / Orijinal ID:** REPORT (1-1) & (U-1.2) - Dedup Grubu A
- **Önem:** Kritik
- **Konum:** `dao.py` (`update_entity_description` ve `invalidate_node`)
- **Uygulanan Fix:** Merkezi `_atomic_saga_commit` fonksiyonu yazıldı. `db.commit()` asenkron dual write çağrıları (VectorEngine, GraphEngine) başarıyla tamamlandıktan SONRA çağrılacak şekilde refactor edildi. Hata durumunda SQLite'ın soft-delete/update işlemleri `rollback` ediliyor.
- **Adım 1 Sonucu (Orijinal Hata Çözüldü mü?):** [KANIT: repro_r03.py çıktısı] — ✅ Doğrulandı. Vector Engine hatası yakalandığında SQLite işlemi `commit` edilmiyor.
- **Adım 2 Sonucu (Regresyon Var mı?):** [Kontrol edilen test: `test_chaos.py`] — ✅ Regresyon Yok. Chaos testleri başarılı.
- **Durum:** ÇÖZÜLDÜ

### [R-04] /v3/health uç noktasına authentication/rate limit eklenmemiş (DOS zafiyeti)
- **Kaynak Rapor / Orijinal ID:** REPORT (4-4)
- **Önem:** Kritik
- **Konum:** `server.py` (`health_v3`)
- **Uygulanan Fix:** `/v3/health` endpoint'ine `dependencies=[Depends(get_api_key)]` decorator'ı eklendi.
- **Adım 1 Sonucu (Orijinal Hata Çözüldü mü?):** ✅ Doğrulandı. Artık yetkisiz istekler reddediliyor.
- **Adım 2 Sonucu (Regresyon Var mı?):** ✅ Regresyon Yok.
- **Durum:** ÇÖZÜLDÜ

### [R-05] `sanitize_cmb_content` hiç çağrılmıyor (IDOR ve XSS riski)
- **Kaynak Rapor / Orijinal ID:** REPORT
- **Önem:** Kritik
- **Konum:** `dao.py` ve `rbac.py`
- **Uygulanan Fix:** `dao.py` içine `_sanitize_payload` eklendi. Bütün okuma operasyonlarında (`get_memories`, `get_nodes_by_ids_batch`, `get_memory_by_id`, `search_memory_fts`, `get_epistemic_data_for_nodes`, `get_raw_log`, `get_recent_logs`) dönen kayıtların `content` ve `content_payload` alanları `sanitize_cmb_content` fonksiyonundan geçirilerek döndürüldü.
- **Adım 1 Sonucu (Orijinal Hata Çözüldü mü?):** ✅ Doğrulandı. DAO katmanından çıkan bütün veriler sanitize edilmiş oluyor.
- **Adım 2 Sonucu (Regresyon Var mı?):** [Kontrol edilen test: `test_chaos.py` ve `test_maintenance_worker.py`] — ✅ Regresyon Yok. Tüm testler geçti.
- **Durum:** ÇÖZÜLDÜ

### [R-06] `server.py` - Retention Worker Devre Dışı Bırakılmış
- **Kaynak Rapor / Orijinal ID:** REPORT (Faz 1)
- **Önem:** Kritik
- **Konum:** `server.py`
- **Uygulanan Fix:** Kontrol edildi. `maintenance_worker.start()` satırı halihazırda aktifti. (Eksik veya yorum satırı durumu yoktu, düzeltilmiş kabul edildi).
- **Adım 1 Sonucu:** ✅ Doğrulandı.
- **Durum:** ÇÖZÜLDÜ

### [R-07] KùzuDB provider - Read-only queries connection leak
- **Kaynak Rapor / Orijinal ID:** REPORT
- **Önem:** Kritik
- **Konum:** `kuzu_provider.py` (`_sync_execute` ve `_sync_execute_write` metotları)
- **Uygulanan Fix:** KuzuDB `execute` işlemi sonucu dönen `QueryResult` nesneleri için `try/finally` bloğu eklendi ve `result.close()` çağrısı garanti altına alındı. Bu sayede lock veya memory sızıntısının önüne geçildi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. `QueryResult` nesneleri sızıntı yapmayacak şekilde kapatılıyor.
- **Adım 2 Sonucu (Regresyon Var mı?):** [Kontrol edilen test: `test_kuzu_isolation.py` ve `test_kuzu_performance.py`] — ✅ Regresyon Yok.
- **Durum:** ÇÖZÜLDÜ

### [R-08] RLS queries - LIKE based agent matching -> strict ==
- **Kaynak Rapor / Orijinal ID:** REPORT (Faz 2)
- **Önem:** Kritik
- **Konum:** `mesa_storage` ve `mesa_memory` geneli (Tüm sorgular)
- **Uygulanan Fix:** Kontrol edildi. Zaten SQLite (`dao.py`), Kuzu (`kuzu_provider.py`) ve LanceDB (`vector_engine.py`) üzerinden sadece `agent_id = ?` şeklinde strict equality kontrolü yapılıyor.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Zafiyet önceden çözülmüş.
- **Durum:** ÇÖZÜLDÜ

### [R-09] mesa_client/client.py - SDK Bearer header vs X-API-Key
- **Kaynak Rapor / Orijinal ID:** REPORT
- **Önem:** Kritik
- **Konum:** `mesa_client/client.py` (Satır 127 civarı)
- **Uygulanan Fix:** İstemcideki `headers["Authorization"] = f"Bearer {api_key}"` yapısı sunucunun beklediği standarda uyumlu şekilde `headers["X-API-Key"] = api_key` olarak düzeltildi.
- **Adım 1 Sonucu:** ✅ Doğrulandı.
- **Durum:** ÇÖZÜLDÜ

### [R-10] mesa_mcp/server.py - agent_id LLM tool arg
- **Kaynak Rapor / Orijinal ID:** REPORT
- **Önem:** Kritik
- **Konum:** `mesa_mcp/server.py`
- **Uygulanan Fix:** `agent_id` parametresi MCP tool argümanları arasından (prompt injection / impersonation açığını kapatmak için) kaldırıldı. Yerine, MCP sunucusunun çevresel değişkenlerinden (`MESA_AGENT_ID`) zorunlu olarak okunması sağlandı.
- **Adım 1 Sonucu:** ✅ Doğrulandı. LLM artık başka bir `agent_id` uydurarak sistemin başka bir alanına sızamaz.
- **Durum:** ÇÖZÜLDÜ

### [R-11] scripts/run_demo_rag.py - /v3/demo/chat unprotected
- **Kaynak Rapor / Orijinal ID:** REPORT
- **Önem:** Kritik
- **Konum:** `scripts/run_demo_rag.py`
- **Uygulanan Fix:** Demo RAG uç noktasında (`/v3/demo/chat`) hiçbir kimlik doğrulama işlemi yapılmıyordu. `Depends(get_api_key)` bağımlılığı fastapi route'una eklenerek endpoint dış dünyaya kapatıldı ve güvenli hale getirildi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Yetkisiz istekler 401 Unauthorized alacaktır.
- **Durum:** ÇÖZÜLDÜ

### [R-12] vector_engine.py - ThreadPoolExecutor kapanış sızıntısı
- **Kaynak Rapor / Orijinal ID:** REPORT (2-1)
- **Önem:** Yüksek
- **Konum:** `mesa_storage/vector_engine.py`
- **Uygulanan Fix:** `close()` metodu içinde `ThreadPoolExecutor.shutdown` çağrısına `cancel_futures=True` parametresi eklendi (Python 3.9+). Böylece asenkron kapanış esnasında kuyrukta bekleyen ama henüz çalışmayan threadlerin de anında iptal edilmesi sağlanarak sızıntı/kilitlenme riski engellendi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Kapanışta (graceful shutdown) Executor sızıntısı olmayacak.
- **Durum:** ÇÖZÜLDÜ

### [R-13] reranker.py - CrossEncoder unbounded thread pool (OOM)
- **Kaynak Rapor / Orijinal ID:** REPORT (4-3)
- **Önem:** Yüksek
- **Konum:** `mesa_memory/retrieval/reranker.py`
- **Uygulanan Fix:** Varsayılan (unbounded) `asyncio` thread pool yerine, `max_workers=1` olan özel bir `ThreadPoolExecutor` sınıf içine entegre edildi. Bu sayede eşzamanlı istekler gelse bile GPU/CPU üzerinde CrossEncoder tahmin işlemlerinin seri çalışması sağlanarak OOM (Out-of-Memory) engellendi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Aşırı yükte thread şişmesi yaşanmayacaktır.
- **Durum:** ÇÖZÜLDÜ

### [R-14] llm_judge.py - Benchmark judge fallback sessiz doğrulama
- **Kaynak Rapor / Orijinal ID:** REPORT (6-2)
- **Önem:** Yüksek
- **Konum:** `mesa-benchmark/mesa_benchmark/evaluators/llm_judge.py` ve `multi_model_judge.py`
- **Uygulanan Fix:** LLM jürisi (API timeout, auth error vb. sebeplerle) yanıt üretemediğinde, benchmark'ın sessizce `gt in ans` (substring) kontrolüne düşüp "Başarılı (is_correct=True)" skorlaması yapması engellendi. Fallback yerine `RuntimeError` fırlatılması sağlandı. İlgili unit testleri de güncellendi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Benchmark artık LLM jürisinin hatalarında yanıltıcı istatistik üretmeyecektir.
- **Durum:** ÇÖZÜLDÜ

### [R-15] ingestion_worker.py - N+1 embedding (Dedup Grubu B)
- **Kaynak Rapor / Orijinal ID:** REPORT (7-1) + UNDOC (U-1.1)
- **Önem:** Yüksek
- **Konum:** `mesa_workers/ingestion_worker.py`
- **Uygulanan Fix:** REBEL triplet işlemi sırasında `head` ve `tail` embeddingleri döngü içerisinde tekil (N+1) olarak çağrılıyordu. Bu bölüm refactor edilerek döngü öncesinde `compute_embedding_batch` metodu ile toplu (batch) olarak hesaplanıp dictionary cache üzerinden eşleştirilmesi sağlandı.
- **Adım 1 Sonucu:** ✅ Doğrulandı. N+1 performasyon sorunu çözüldü. Ingestion hızı artırıldı.
- **Durum:** ÇÖZÜLDÜ

### [R-16] mesa_evals/dataset.py vs benchmark - Golden dataset schema conflict
- **Kaynak Rapor / Orijinal ID:** UNDOC (U-2.1)
- **Önem:** Yüksek
- **Konum:** `mesa-benchmark/mesa_benchmark/datasets/loader.py`
- **Uygulanan Fix:** İki ayrı proje (evals ve benchmark) için iki ayrı schema bulunması (ve `convert_mesa_evals.py` betiği ile kopya veri oluşturulması) karmaşa yaratıyordu. `convert_mesa_evals.py` ve `synthetic_dataset.json` (kopya json) silindi. Yerine `mesa-benchmark`'ın `DatasetLoader` sınıfına `mesa_evals` şemasını on-the-fly (`_convert_mesa_evals_to_scenarios` üzerinden) yükleyebilecek yetenek eklendi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Single Source of Truth (`mesa_evals.dataset`) sağlandı.
- **Durum:** ÇÖZÜLDÜ

### [R-17] tracer.py (yok) / routing_telemetry - LLM tracing (LangFuse/LangSmith) entegrasyonu eksik
- **Kaynak Rapor / Orijinal ID:** UNDOC (U-4.2)
- **Önem:** Yüksek
- **Konum:** `mesa_memory/observability/tracer.py` ve `mesa_memory/api/server.py`
- **Uygulanan Fix:** LLM çağrılarını (litellm üzerinden) gözlemleyebilmek için `mesa_memory/observability/tracer.py` oluşturuldu. `setup_telemetry_tracing()` fonksiyonu `LANGFUSE_*` ve `LANGCHAIN_*` env değişkenlerini kontrol ederek litellm callbacklerine `langfuse` ve `langsmith` entegrasyonunu dinamik olarak ekliyor. Bu fonksiyon FastAPI lifespan başlangıcında (`server.py`) çağrılacak şekilde yapılandırıldı.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Çevresel değişkenler sağlandığında LLM çağrıları harici telemetri platformlarına (LangFuse, LangSmith) akacaktır.
- **Durum:** ÇÖZÜLDÜ

### [R-18] mesa_client/client.py:81 - Idempotent olmayan POST'ta güvensiz retry
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.2)
- **Önem:** Yüksek
- **Konum:** `mesa_client/client.py`
- **Uygulanan Fix:** İstemcideki senkron ve asenkron HTTP retry mekanizmalarında (`_sync_retry`, `_async_retry`) idempotent olmayan (GET/HEAD/OPTIONS/PUT/DELETE dışındaki) metodlarda ve güvenli olmayan hatalarda retry yapılmasını engelleyen bir koruma eklendi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Zaman aşımı durumunda POST isteklerinin tekrarlanarak veri duplikasyonuna veya çifte kayda neden olması engellendi.
- **Durum:** ÇÖZÜLDÜ

### [R-19] langchain.py & mesa_mcp - SearchResultItem missing content_payload
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.3) + K-2.2
- **Önem:** Yüksek
- **Konum:** `mesa_api/schemas.py`, `mesa_api/router.py`, `mesa_client/langchain.py`, `mesa_mcp/server.py`
- **Uygulanan Fix:** MESA API'nin search sonuçlarında dönen `SearchResultItem` objesinde `content_payload` alanı eksikti, bu da entegrasyonlarda (LangChain ve MCP) sadece isim (entity_name) ile yetinilmesine ve asıl raw_log verisinin kaybedilmesine (Dedup Grubu C) neden oluyordu. `SearchResultItem` şemasına ve `mesa_api/router.py` yanıtlarına `content_payload` eklendi; istemcilerde `item.content_payload` kullanılarak haritalama yapılması sağlandı.
- **Adım 1 Sonucu:** ✅ Doğrulandı. LangChain ve MCP entegrasyonları artık tam doküman içeriğine erişebilmektedir.
- **Durum:** ÇÖZÜLDÜ

### [R-20] router.py - _hydrate_embeddings boşa düşüyor
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.4)
- **Önem:** Orta
- **Konum:** `mesa_memory/consolidation/router.py`, `mesa_storage/dao.py`, `mesa_storage/vector_engine.py`
- **Uygulanan Fix:** `ValenceMotor` nesnesi `router.py` içinde `storage` parametresi olmadan başlatıldığı için bellek hidratasyonu (`_hydrate_embeddings`) boş liste dönüyordu. `VectorEngine`'e senkron `_sync_get_all_embeddings` eklendi, `MemoryDAO` üzerinden `get_all_embeddings` ile dışarı açıldı ve `router.py` içinde `ValenceMotor`'a `storage=self.dao` geçilerek hafızanın kalıcı depolamadan başarıyla yüklenmesi sağlandı.
- **Adım 1 Sonucu:** ✅ Doğrulandı. ValenceMotor cold-start durumlarında vector engine üzerindeki tüm mevcut embeddingleri başarıyla okuyabiliyor.
- **Durum:** ÇÖZÜLDÜ

### [R-21] core.py - compute_memory_valence (Novelty + Utility) logic error
- **Kaynak Rapor / Orijinal ID:** UNDOC (U-2.2)
- **Önem:** Orta
- **Konum:** `mesa_memory/valence/core.py`
- **Uygulanan Fix:** `ValenceMotor.evaluate` metodunda `calculate_fitness_score` (utility) tamamen göz ardı ediliyor ve sadece novelty baz alınarak "ADMIT" veya "DEFERRED" (Tier 3) kararı veriliyordu. Yeni yapı ile novelty skoru (1.0 veya 0.0) utility (yoğunluk + kelime sayısı verimliliği) ile birleştirilerek kombine "fitness" hesaplanıyor. Eğer toplam fitness skoru 0.3'ün altındaysa sistem bunu direkt "DISCARD" diyerek reddediyor (kötü utility'ye sahip verilerin bellek kirliliği yaratması önlendi).
- **Adım 1 Sonucu:** ✅ Doğrulandı. Bellek girişlerinde novelty (yenilik) yanında verinin içeriği (utility) de dikkate alınarak çöplük oluşumu engellendi.
- **Durum:** ÇÖZÜLDÜ

### [R-22] entity_consolidation_worker.py - get_neighbors kuzu schema mismatch
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Orta
- **Konum:** `mesa_workers/entity_consolidation_worker.py` (Raporda `loop.py` olarak hatalı not düşülmüş)
- **Uygulanan Fix:** Entity consolidation worker, `get_neighbors` metodunun dönüş değerini eski SQLite relational şemasına göre (`source_id`, `target_id`) okumaya çalışıyordu ancak KùzuDB tabanlı graph sağlayıcısı artık standardize edilmiş `[{"id": "...", "name": "...", "hops": "..."}]` listesi dönüyor. Worker içindeki neighbor parsing mantığı KùzuDB'nin dönüş yapısına uygun şekilde düzeltildi ve her node için N+1 `get_memory_by_id` çağrısına gerek kalmadan Kùzu'dan gelen `name` alanı kullanıldı.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Entity consolidation arka plan işleyicisi (worker) patlamadan entity komşularını çekip bağlam (context) oluşturabiliyor.
- **Durum:** ÇÖZÜLDÜ

### [R-23] loop.py - DLQ yeniden işleme yok
- **Kaynak Rapor / Orijinal ID:** REPORT (5-1)
- **Önem:** Orta
- **Konum:** `mesa_memory/consolidation/loop.py` & `mesa_memory/api/server.py`
- **Uygulanan Fix:** `loop.py` içinde Dead Letter Queue (DLQ) re-processing worker fonksiyonu (`start_dlq_worker`) yazıldı. Ayrıca `start_tier3_deferred_worker` ve yeni yazılan `start_dlq_worker` arka plan processleri olarak `server.py` lifespan'ine dahil edildi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Hata alan kayıtlar sadece dosyaya (DLQ) yazılıp kalmıyor, periyodik worker aracılığıyla tekrar alınarak ConsolidationLoop'a yeniden gönderiliyor.
- **Durum:** ÇÖZÜLDÜ

### [R-24] tests/ - WAL recovery senaryoları test edilmemiş
- **Kaynak Rapor / Orijinal ID:** REPORT (6-1)
- **Önem:** Orta
- **Konum:** `tests/test_wal_recovery.py`
- **Uygulanan Fix:** SQLite WAL (Write-Ahead Logging) journal modunun crash recovery yeteneklerini simüle eden yeni bir test yazıldı (`test_wal_recovery_and_checkpoint`). Test; senkron ve izole bir bağlantıyla WAL'a veri yazıp commit etmeden/checkpoint yapmadan bırakıyor, sonrasında asenkron `AsyncEngine` bağlantısının bu durumu crash sonrası otomatik recovery (`PRAGMA journal_mode=WAL`) yaparak şeffaf şekilde okuyabildiğini ve manuel checkpoint atabildiğini sınıyor.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Test başarılı bir şekilde çalışıyor ve engine'in WAL'ı hatasız recover ettiği teyit edildi.
- **Durum:** ÇÖZÜLDÜ

### [R-25] hybrid.py - FTS sorgusu senkron bekletiliyor
- **Kaynak Rapor / Orijinal ID:** REPORT (7-2)
- **Önem:** Orta
- **Konum:** `mesa_memory/retrieval/hybrid.py`
- **Uygulanan Fix:** Hybrid arama (vector + graph + FTS) akışında, FTS5 sözcüksel arama (lexical search) sorgusu vector ve graph aramaları bitene kadar asenkron olarak bekletilip sonrasında ardışık (sequential) çağrılıyordu. Bu durum genel yanıt süresini (latency) artırıyordu. `search_memory_fts` çağrısı, diğer aramalarla (vector ve graph) birlikte `asyncio.gather` içerisine alındı ve tam paralelleştirme sağlandı. Ayrıca FTS5 sorgusunun ölçümü için anonim asenkron fonksiyon yazılarak gecikme değerleri doğru ölçüldü.
- **Adım 1 Sonucu:** ✅ Doğrulandı. FTS sorgusu paralelleştirildi.
- **Durum:** ÇÖZÜLDÜ

### [R-26] llm_judge.py - Judge self-consistency/ensemble yok
- **Kaynak Rapor / Orijinal ID:** UNDOC (U-2.2)
- **Önem:** Orta
- **Konum:** `mesa-benchmark/mesa_benchmark/evaluators/llm_judge.py`
- **Uygulanan Fix:** LLM Judge (değerlendirici) sınıfına `ensemble_size` (varsayılan: 3) eklendi. `evaluate` metodu, tek bir LLM çağrısı yapmak yerine belirtilen `ensemble_size` kadar eşzamanlı (`ThreadPoolExecutor` ile) çağrı yapacak şekilde güncellendi. Nihai `is_correct` kararı majority vote (çoğunluk oyu), `score` ise ortalama alınarak belirleniyor. Ayrıca variance sağlanabilmesi için `temperature` varsayılan olarak 0.7'ye çekildi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Benchmark değerlendirmelerinde LLM Judge artık daha tutarlı sonuçlar (self-consistency) üretebiliyor.
- **Durum:** ÇÖZÜLDÜ

### [R-27] legal_generator.py + router.py:268 - Legal mode maliyet/latency uyarısı yok, legal_audit.py gatekeeper'a bağlı değil
- **Kaynak Rapor / Orijinal ID:** UNDOC (U-3.2)
- **Önem:** Orta
- **Konum:** `mesa_memory/consolidation/router.py`, `mesa_evals/gatekeeper.py`
- **Uygulanan Fix:** 
  1. `router.py` dosyasında `legal_domain_mode` aktif edildiğinde çalışacak şekilde belirgin bir maliyet/latency (cost/latency) uyarı logu eklendi.
  2. `mesa_evals/gatekeeper.py` içine "Rule 3: Legal Graph Poisoning" kuralı eklendi; `MESA_LEGAL_DOMAIN_MODE=1` olduğunda `legal_audit.py` import edilerek graph zehirlenmesi (graph poisoning) var mı diye kontrol edilmesi ve varsa CI/CD gate'ini ihlal (violation) olarak düşürmesi sağlandı.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Artık Legal Mode etkinleştiğinde kullanıcı maliyet/gecikme konusunda uyarılıyor ve audit mantığı CI/CD (gatekeeper) sürecine dahil edildi.
- **Durum:** ÇÖZÜLDÜ

### [R-28] scripts/ (doküman temizliği) - Hayalet script referansları
- **Kaynak Rapor / Orijinal ID:** UNDOC (U-5.1)
- **Önem:** Orta
- **Konum:** `mesa-benchmark/README.md`, `mesa-benchmark/USAGE_GUIDE.md`
- **Uygulanan Fix:** Repoda bulunmayan ancak dokümantasyonda geçmekte olan hayalet (phantom) script referansları (`download_locomo.py`, `generate_comprehensive_dataset.py`, `publish_to_hf.py`) tüm README ve dokümantasyon dosyalarından temizlendi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Yanıltıcı komutlar kaldırıldı.
- **Durum:** ÇÖZÜLDÜ

### [R-29] mesa_client/client.py - SDK/API versiyon uyumluluk kontrolü yok
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.4)
- **Önem:** Orta
- **Konum:** `mesa_memory/api/server.py`, `mesa_client/client.py`
- **Uygulanan Fix:** Sunucu (server) tarafında tüm API yanıtlarına `X-API-Version` header'ını ekleyen bir middleware yazıldı. İstemci (MesaClient ve AsyncMesaClient) tarafında ise API'den dönen bu versiyon kontrol edilerek, istemcinin beklediği major.minor versiyon ailesiyle uyuşmazlık (mismatch) tespit edildiğinde kullanıcıyı uyaran (`logger.warning`) version compatibility check (sürüm uyumluluk kontrolü) mekanizması eklendi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. SDK ile sunucu API'si arasında uyumsuz bir sürüm durumu oluştuğunda client net bir uyarı vermektedir.
- **Durum:** ÇÖZÜLDÜ

### [R-30] test_pagerank_coverage.py vb. - asyncio.sleep tabanlı flaky test riski
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `tests/test_pagerank_coverage.py`, `tests/test_maintenance_worker.py`
- **Uygulanan Fix:** Testler içerisindeki `await asyncio.sleep(0.1)` veya `0.01` şeklindeki zamana bağlı, deterministik olmayan (flaky) beklemeler (sleeps) kaldırıldı. Yerlerine `await asyncio.sleep(0)` kullanılarak asenkron task geçişlerinin (context switch) doğal yoldan tamamlanması sağlandı ya da bir döngü ile mock nesnelerinin çağrılıp çağrılmadığı kontrol edildi. 
- **Adım 1 Sonucu:** ✅ Doğrulandı. İlgili test dosyaları çalıştırıldı ve başarıyla, flaky davranışlar sergilemeden sonuçlandı.
- **Durum:** ÇÖZÜLDÜ

### [R-31] install.sh:68 - curl | sh güvensiz kurulum
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-4.1)
- **Önem:** Orta
- **Konum:** `install.sh`
- **Uygulanan Fix:** Doğrudan `curl -sSL ... | sh` kullanımı kaldırılarak script'in önce `/tmp/` dizinine indirilip, ardından güvenli şekilde çalıştırılması (ve sonrasında silinmesi) sağlandı. Böylece bağlantı kopması anında yarım çalıştırılan shell scripti zafiyetinin (partial execution vulnerability) önüne geçildi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Script daha güvenli hale getirildi.
- **Durum:** ÇÖZÜLDÜ

### [R-32] ARCHITECTURE.md - RBAC flow param tipi tutarsızlığı
- **Kaynak Rapor / Orijinal ID:** REPORT (1-2)
- **Önem:** Düşük
- **Konum:** `ARCHITECTURE.md`
- **Uygulanan Fix:** Mimari dokümantasyondaki `agent_id`'nin "keyword-only argument" olduğu hatalı beyanı "first positional argument" olarak düzeltildi. Ayrıca `MemoryDAO->>RBAC` şeklinde çizilmiş hatalı Mermaid diyagramı, güncel koda (yani API/Router seviyesinde yetkilendirme) uygun şekilde `Router->>RBAC` olarak güncellendi ve ilgili açıklama metinleri kodun fiili davranışı ile senkronize edildi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Dokümantasyon ile kod arasındaki zıtlık giderildi.
- **Durum:** ÇÖZÜLDÜ

### [R-33] schemas.py - Eski StorageFacade fonksiyonları (ölü kod)
- **Kaynak Rapor / Orijinal ID:** REPORT (2-2)
- **Önem:** Düşük
- **Konum:** `mesa_storage/schemas.py:170-365`
- **Uygulanan Fix:** Üretim kodunda çağrılmayan (`insert_node`, `bulk_insert_nodes`, `soft_delete_node`, `mark_consolidated`, vb.) "dead code" fonksiyonlar `mesa_storage/schemas.py` dosyasından çıkartılıp sadece testlerde kullanıldığı için `tests/utils/storage_helpers.py` konumuna taşındı. İlgili tüm test modüllerinin import'ları düzenlendi ve testler başarıyla çalıştırıldı.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Eski/kullanılmayan kod üretim katmanından izole edildi.
- **Durum:** ÇÖZÜLDÜ

### [R-34] server.py - /health/init yüzeysel kontrol
- **Kaynak Rapor / Orijinal ID:** REPORT (5-2)
- **Önem:** Düşük
- **Konum:** `mesa_memory/api/server.py:441` ve `mesa_storage/dao.py:1795`
- **Uygulanan Fix:** `/health/init` rotası artık yalnızca `state.is_ready` statik değerine güvenmek yerine, arka planda tüm veritabanı sürücülerini (`SQLite`, `LanceDB` ve özellikle `KùzuDB` için gerçek bir `MATCH (n) RETURN COUNT(n)` sorgusu atan `health_check`) kontrol etmektedir. Herhangi bir storage adaptörü ayakta değilse, 503 HTTP koduyla "Backend services degraded" yanıtı dönmektedir.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Kapsamlı health check devops güvenliğini arttırdı.
- **Durum:** ÇÖZÜLDÜ

### [R-35] maintenance.py - VACUUM saatleri hardcoded
- **Kaynak Rapor / Orijinal ID:** REPORT (7-3)
- **Önem:** Düşük
- **Konum:** `mesa_memory/api/server.py:257`
- **Uygulanan Fix:** `MaintenanceWorker` başlatılırken `schedule_hours` statik listesini almak yerine `MESA_VACUUM_HOURS` çevre değişkeni dinlenir hale getirildi. Artık sistem yöneticileri `MESA_VACUUM_HOURS=1,13` gibi formatlarla VACUUM/maintenance operasyonlarını istenen saatlere dinamik ayarlayabilmektedir.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Bakım saatleri esnek/devops-friendly hale getirildi.
- **Durum:** ÇÖZÜLDÜ

### [R-36] docs/api-reference.md - StorageFacade Legacy Referanslar
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `docs/api-reference.md`
- **Uygulanan Fix:** API referans dokümanında `StorageFacade`'den bahseden eski referanslar, yeni `MemoryDAO` mimarisine göre güncellenmiştir.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Dokümantasyon kod ile eşzamanlı hale getirildi.
- **Durum:** ÇÖZÜLDÜ

### [R-37] Sürüm Numarası
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `pyproject.toml`
- **Uygulanan Fix:** `pyproject.toml` içindeki versiyon numarası `0.6.0` olarak güncellenmiştir.
- **Adım 1 Sonucu:** ✅ Doğrulandı.
- **Durum:** ÇÖZÜLDÜ

### [R-38] test_maintenance.py / test_retrieval_edge_cases.py - StorageFacade Mockları
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `tests/test_retrieval_edge_cases.py`
- **Uygulanan Fix:** Testler içerisindeki `StorageFacade` referansları ve mockları silinerek güncel yapıya göre adapte edilmiştir. 
- **Adım 1 Sonucu:** ✅ Doğrulandı. Test suite içerisinde eski referanslar bulunmuyor.
- **Durum:** ÇÖZÜLDÜ

### [R-39] test_retrieval.py - Eski StorageFacade kullanımları
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `tests/test_retrieval.py`, `tests/test_storage_unification.py`
- **Uygulanan Fix:** Eski testlerde StorageFacade yerine MemoryDAO kullanılması sağlandı. Bu işlem storage unification sırasında zaten yapılmış ve güncel `test_storage_unification.py` testleri geçiyor.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Testler StorageFacade kullanımını raporlamıyor.
- **Durum:** ÇÖZÜLDÜ

### [R-40] test_dao.py - KùzuDB mockları eksik
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Orta
- **Konum:** `tests/test_dao.py`
- **Uygulanan Fix:** `test_dao.py` içerisinde her test case için diske `.kuzu` dosyası oluşturan `KuzuGraphProvider` yerine `MagicMock(AsyncMock)` kullanıldı. Bu sayede testler hızlandı ve timeout olasılığı ortadan kalktı.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Testler daha hızlı ve mocks problemsiz çalışıyor.
- **Durum:** ÇÖZÜLDÜ

### [R-41] api/server.py - Pydantic model uyuşmazlığı
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Orta
- **Konum:** `mesa_api/schemas.py`, `mesa_api/router.py`
- **Uygulanan Fix:** `MemorySearchResponse` pydantic modelinde yaşanan schema mismatch (beklenen: `context`, `retrieved_nodes`, `metrics`; gönderilen: `results`, vb.) çözüldü. Model canonical formatına getirildi ve testler (`test_api_schemas.py`) güncellendi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. API doğru response model üzerinden çalışıyor.
- **Durum:** ÇÖZÜLDÜ

### [R-42] api/server.py - import Request eksikliği
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `mesa_memory/api/server.py`
- **Uygulanan Fix:** Middleware tanımlanırken ihtiyaç duyulan `Request` import'unun yapıldığı kontrol edilerek doğrulandı.
- **Adım 1 Sonucu:** ✅ Doğrulandı.
- **Durum:** ÇÖZÜLDÜ

### [R-43] workers/maintenance.py - StorageFacade referansları
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `mesa_workers/maintenance.py`
- **Uygulanan Fix:** İlgili dosyada `StorageFacade` yerine `MemoryDAO` kullanıldığı teyit edildi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Eski yapıya referans kalmadı.
- **Durum:** ÇÖZÜLDÜ

### [R-44] tests/test_p0a_batch.py - StorageFacade test mocku
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `tests/test_p0a_batch.py`
- **Uygulanan Fix:** Mock olarak `StorageFacade` referansı içeren bölümlerin tamamen MemoryDAO odaklı çalıştığı test edildi ve yorum satırlarında ilgili güncellemeler teyit edildi.
- **Adım 1 Sonucu:** ✅ Doğrulandı.
- **Durum:** ÇÖZÜLDÜ

### [R-45] pytest.ini - Deprecation warning: asyncio_mode = strict
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `pyproject.toml`
- **Uygulanan Fix:** `pytest-asyncio` pluginine ait warning loglarını önlemek adına `asyncio_mode = "strict"` yapılandırması `asyncio_mode = "auto"` olarak güncellendi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Warning giderildi.
- **Durum:** ÇÖZÜLDÜ

### [R-46] README.md - StorageFacade diagramları
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `README.md`
- **Uygulanan Fix:** README.md üzerinde yapılan grep sonuçlarında `StorageFacade`'in geçmediği ve mimari değişikliklerin doğru dokümante edildiği doğrulandı.
- **Adım 1 Sonucu:** ✅ Doğrulandı.
- **Durum:** ÇÖZÜLDÜ

### [R-47] mesa_memory/__init__.py - Eski storage sınıfları exportu
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `mesa_storage/__init__.py`
- **Uygulanan Fix:** Dışa aktarılan kullanılmayan ve eski storage engine sınıfları kaldırılarak export listesi temizlendi.
- **Adım 1 Sonucu:** ✅ Doğrulandı.
- **Durum:** ÇÖZÜLDÜ

### [R-48] mesa_memory/storage/ - Klasör silinme kontrolü
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `mesa_memory/storage/`
- **Uygulanan Fix:** Dizin kontrolü sağlandı ve önceki phase'lerde başarılı bir şekilde tamamen silindiği teyit edildi.
- **Adım 1 Sonucu:** ✅ Doğrulandı. Dosya sistemi temizliği tamam.
- **Durum:** ÇÖZÜLDÜ

### [R-49] tests/utils/ - Eski StorageFacade helperları
- **Kaynak Rapor / Orijinal ID:** CLOSING (K-1.5)
- **Önem:** Düşük
- **Konum:** `tests/utils/`
- **Uygulanan Fix:** `tests/utils/` dizinindeki tüm utils/helper dosyaları kontrol edildi. Hiçbir dosyada `StorageFacade` referansı kalmadığı kesinleşti.
- **Adım 1 Sonucu:** ✅ Doğrulandı.
- **Durum:** ÇÖZÜLDÜ
