# MESA Kademeli Derin Analiz Raporu (REPORT.md)

## FAZ 0 — Envanter ve Harita Çıkarma

**Tarih / Saat:** 2026-07-14
**Yöntem:** Kod tabanı analizi (`pyproject.toml`, dizin ağacı taraması, giriş noktası incelemesi ve `ARCHITECTURE.md` karşılaştırması).

### 1. Dizin Ağacı ve Dosya Dağılımı

MESA reposu, API-first ("headless FastAPI daemon") mimariye sahip, modüler bir Python projesidir. Çekirdek dizinler 2-3 seviyeli mimari ayrımına göre organize edilmiştir:

```text
MESA (Root)
├── mesa_api/                  # API katmanı (FastAPI router & Pydantic şemaları)
│   ├── router.py              # /v3/memory/* uç noktaları
│   └── schemas.py             # İstek/Yanıt doğrulama şemaları
├── mesa_memory/               # Çekirdek bilişsel hafıza iş mantığı ve motorları
│   ├── adapter/               # LLM sağlayıcı adaptörleri (OpenAI, Claude, Ollama, Live vb.)
│   ├── api/                   # Ana uvicorn uygulama sunucusu (server.py)
│   ├── consolidation/         # REM cycle / bilginin graf yapısına konsolidasyonu
│   ├── extraction/            # Üçlü (triplet) ve REBEL çıkarım boru hattı
│   ├── observability/         # Prometheus metrikleri ve yapılandırılmış loglama
│   ├── retrieval/             # Hibrit arama (FTS5 + LanceDB + KùzuDB) & CrossEncoder Reranker
│   ├── schema/                # Temel veri tipleri
│   ├── security/              # RBAC (Rol tabanlı erişim kontrolü) ve izolasyon
│   └── valence/               # Valence Motor (novelty/EWMAD değerlendirme motoru)
├── mesa_storage/              # Veri erişim ve fiziksel depolama katmanı
│   ├── dao.py                 # MemoryDAO (Epistemic Isolation ve Dual-Write Saga)
│   ├── sqlite_engine.py       # aiosqlite asenkron ilişkisel veritabanı motoru
│   ├── vector_engine.py       # LanceDB vektör depolama motoru
│   └── kuzu_provider.py       # KùzuDB graf veritabanı sağlayıcısı
├── mesa_workers/              # Arka plan bakım ve asenkron işçiler (maintenance, rem_cycle, ingestion)
├── mesa_client/               # Python SDK istemcisi ve LangChain entegrasyonu
├── mesa_mcp/                  # Model Context Protocol (MCP) sunucusu
├── mesa_evals/                # Değerlendirme, kalite geçitleri (gatekeeper) ve sentetik veri üretimi
├── mesa-benchmark/            # Benchmark otomasyon ve kıyaslama araçları
├── tests/                     # Birim, entegrasyon ve performans testleri (55+ dosya)
├── scripts/                   # Yardımcı CLI ve bakım betikleri (health_check, migration vb.)
├── demo/                      # Statik web arayüz demosu (index.html, script.js, style.css)
├── docs/                      # Mimari ve kurulum dokümantasyonları
└── Dockerfile & docker-compose.yml
```

#### Dile Göre Dosya Dağılımı (Özet)
- **Python (`.py`)**: ~135 dosya (Çekirdek modüller: 50+, Testler: 56+, Evals/Benchmark/Scripts: 30+)
- **Markdown (`.md`)**: 12 dosya (`ARCHITECTURE.md`, `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `DEPRECATION_NOTICE.md` vb.)
- **Yapılandırma (`.toml`, `.yaml`, `.yml`, `.ini`, `.env`)**: 12+ dosya (`pyproject.toml`, `docker-compose.yml`, `alembic.ini`, benchmark `config_*.yaml` dosyaları)
- **Web/Demo (`.html`, `.css`, `.js`)**: 3 dosya (`demo/index.html`, `demo/style.css`, `demo/script.js`)
- **Kabuk Betikleri (`.sh`)**: 3 dosya (`install.sh`, `scripts/release_v0.4.2.sh`, `scripts/release_v0.5.2.sh`)

---

### 2. Giriş Noktaları (Entry Points)

| Giriş Noktası | Konum / Tanım | Görev |
|---|---|---|
| **FastAPI App Factory** | `mesa_memory/api/server.py` (`create_app()` / `app`) | Headless FastAPI sunucusunu oluşturur, HTTP uç noktalarını bağlar, `startup`/`shutdown` olaylarını (`ValenceMotor.load_state`, `MemoryDAO.init_db`) yönetir. |
| **Docker CMD** | `Dockerfile` (satır 60) | `uvicorn mesa_memory.api.server:app --host 0.0.0.0 --port 8000` komutuyla üretim sunucusunu başlatır. |
| **Docker Readiness Probe** | `Dockerfile` (satır 57) | `/health/init` endpoint'ini kontrol ederek tüm asenkron motorların ve önbelleklerin (`state.is_ready`) hazır olup olmadığını doğrular. |
| **CLI / Script Entry Point** | `scripts/run_server.py` | Geliştirme/doğrudan çalıştırma için FastAPI sunucusunu uvicorn ile tetikler. |
| **MCP Server Entry Point** | `mesa_mcp/server.py` | Model Context Protocol sunucusunu çalıştırarak dış ajanların doğrudan MESA hafıza motoruyla etkileşmesini sağlar. |
| **CI / Eval Gatekeeper** | `mesa_evals/gatekeeper.py` | CI/CD süreçlerinde kalite denetimlerini (Recall > 0.344, TTFT gecikme sınırları) zorunlu kılan komut satırı giriş noktası. |

---

### 3. Bağımlılık Dosyaları ve Versiyon Kısıtları

#### `pyproject.toml` (Çekirdek - Versiyon 0.5.2)
- **Python Gereksinimi:** `>=3.10`
- **Ana Bağımlılıklar (Asenkron & Web & Depolama):**
  - `aiosqlite >= 0.22.0`, `anyio >= 4.0.0`, `httpx >= 0.28.0`
  - `fastapi >= 0.111.0`, `uvicorn >= 0.29.0`, `pydantic >= 2.13.0`, `pydantic-settings >= 2.14.0`
  - `lancedb >= 0.30.0`, `pyarrow >= 24.0.0`, `kuzu >= 0.0.11`, `rocksdict >= 0.3.0`
  - `tenacity >= 9.0.0`, `prometheus_client >= 20.0.0`, `structlog >= 24.0.0`, `uuid7 >= 0.1.0`
- **Opsiyonel Bağımlılık Paketleri (`[project.optional-dependencies]`):**
  - `adapters`: `anthropic >= 0.100.0`, `openai >= 2.36.0`, `ollama >= 0.6.0`, `groq >= 0.9.0`, `litellm >= 1.40.0`
  - `ml`: `torch >= 2.11.0`, `transformers >= 5.8.0`, `sentence-transformers >= 3.0.0`, `spacy >= 3.8.0`
  - `mcp`: `mcp >= 1.0.0`
  - `langchain`: `langchain-core >= 0.2.0`
- **Strict Typing Kısıtları:** Tip güvenliği (mypy) için strict bayraklar etkinleştirilmiş, henüz tam remide edilmemiş modüller için progressive override (`disallow_untyped_defs = false` vb.) listeleri tanımlanmıştır.

#### `mesa-benchmark/requirements.txt`
- `pydantic >= 2.0`, `pyyaml >= 6.0`, `python-dotenv >= 1.0`, `ollama >= 0.4.0`
*(Ayrıca `requirements-lock.txt` içinde sabitlenmiş tam sürüm kilit dosyamız mevcuttur).*

---

### 4 & 5. Mimari Modül Envanteri ve Gerçek Kod Karşılaştırması (`ARCHITECTURE.md` vs Kod)

| Modül | Dokümanda İddia Edilen Görev/Yapı | Kodda Bulundu mu? Konum | Notlar ve Durum |
|---|---|---|---|
| **MemoryDAO** | StorageFacade yerine geçen, aiosqlite (`SQLite`) ve LanceDB (`VectorEngine`) işlemlerini tek çatı altında toplayıp her sorguda zorunlu `agent_id` (RLS) uygulayan asenkron DAO. | **EVET**<br>`mesa_storage/dao.py`<br>(`class MemoryDAO`) | `MemoryDAO` tam olarak uygulanmış ve tüm alt sorgularda `agent_id` parametresini zorunlu ilk argüman olarak alıyor. |
| **VectorEngine** | LanceDB üzerinde çok boyutlu tablo yönlendirmesi (`mesa_vectors_384` vb.) yapan, thread pool ile çalışan ve WAL kuyruğunu destekleyen vektör motoru. | **EVET**<br>`mesa_storage/vector_engine.py`<br>(`class VectorEngine`) | İddia edildiği gibi `ThreadPoolExecutor` üzerinden çalışıyor. WAL yönlendirmesini `MemoryDAO` koordine ediyor. |
| **KuzuGraphProvider** | `agent_id::node_id` bileşik anahtarı (composite key) ile KùzuDB üzerinde out-of-core özellik grafiği yöneten asenkron sarmalayıcı. | **EVET**<br>`mesa_storage/kuzu_provider.py`<br>(`class KuzuGraphProvider`) | `run_in_executor` kullanarak event loop bloklamasını engeller. Node ID'leri `agent_id::` önekiyle izole eder. |
| **ValenceMotor** | EWMAD (Exponentially Weighted Moving Average of Distances) dayanaklı adaptif yenilik (novelty) eşiği ve startup hydration (`load_state`/`save_state`) motoru. | **EVET**<br>`mesa_memory/valence/core.py`<br>(`class ValenceMotor`) | `load_state()`, `save_state()` ve `_hydrate_embeddings()` metotları mevcut, EWMAD hesaplaması aktif. |
| **TripletExtractor** | Zero-shot Türkçe yasal extraction (`MESA_EXTRACTION_LANG=tr`), opsiyonel REBEL pipeline, bisection retry ve Lost-in-the-Middle önleme katmanları. | **EVET**<br>`mesa_memory/extraction/triplet_extractor.py`<br>(`class TripletExtractor`) | `RebelExtractor` (`rebel_pipeline.py`) entegre edilmiş, `salience-first ordering` ve `bisection retry` uygulanmış. |
| **Tier3Validator** | Çift LLM (LLM_A & LLM_B) konsensüs kapısı. Hem STORE gelirse kabul, aksi halde ret veya DLQ yönlendirmesi. | **EVET**<br>`mesa_memory/consolidation/validator.py`<br>(`class Tier3Validator`) | `validate_candidate()` ve konsensüs karar matrisi eksiksiz uygulanmış. |
| **rem_cycle.py** | API sıcak yolunu bloklamadan asenkron graf konsolidasyonu yapan, 50 kayıt eşiğiyle tetiklenen arka plan işçisi. | **EVET**<br>`mesa_workers/rem_cycle.py`<br>(`class REMCycleWorker`) | 50 kayıt eşiği (`activation_threshold`), çelişki değerlendirmesi (`evaluate_contradiction`) ve çözümleme mevcuttur. |
| **gatekeeper.py** | CI/CD süreçlerinde Recall ve TTFT metriklerini denetleyen, başarısızlıkta PR/derlemeyi engelleyen kalite kapısı. | **EVET**<br>`mesa_evals/gatekeeper.py`<br>(`run_gatekeeper`) | `enforce_cost_efficiency`, `enforce_latency_limit` metotları ve ihlal sınıfları (`GateViolation`) mevcuttur. |
| **mesa_mcp** | Model Context Protocol sunucu arayüzü (`server.py`). | **EVET**<br>`mesa_mcp/server.py` | MCP standardına uygun olarak araç (tool) tanımlarını sunar. |
| **mesa_client** | `httpx` tabanlı senkron/asenkron Python SDK istemcisi ve `MesaLangchainRetriever` entegrasyonu. | **EVET**<br>`mesa_client/client.py`<br>`mesa_client/langchain.py` | Hem natif API istemcisi hem de LangChain retriever genişletmesi mevcuttur. |
| **AdapterFactory** | OpenAI, Claude, Groq, LiteLLM ve yerel Ollama adaptörlerini üreten fabrika sınıfı. | **EVET**<br>`mesa_memory/adapter/factory.py`<br>(`class AdapterFactory`) | `get_adapter()` metodu ve tüm alt adaptör sınıfları (`ollama.py`, `claude.py`, `live.py`) tanımlı. |
| **StorageFacade** | `ARCHITECTURE.md`'de "completely replacing the deprecated StorageFacade" denilen eski depolama katmanı. | **KALDIRILMIŞ**<br>(Sadece test referansları) | Üretim kodundan tamamen silinmiş. `test_storage_unification.py` içinde silindiği ve ulaşılamaz olduğu test ediliyor. |
| **MaintenanceWorker** | API'den ayrı senkron `sqlite3` bağlantısı (`isolation_level=None`) ile hard-delete (`retention`), `VACUUM` ve `Table.optimize()` yapan bakım işçisi. | **EVET**<br>`mesa_workers/maintenance.py`<br>(`class MaintenanceWorker`) | İddia edildiği gibi senkron `sqlite3` bağlantısı kullanıyor ve periyodik `run_maintenance_cycle()` koordine ediyor. |
| **wal_checkpoint_worker** | Her 5 dakikada bir pasif WAL checkpoint (`PRAGMA wal_checkpoint(PASSIVE)`) yürüten arka plan görevlisi. | **EVET**<br>`mesa_memory/api/server.py`<br>(satır 253) | `server.py` içinde `async def wal_checkpoint_worker()` olarak tanımlı ve `startup` sırasında `asyncio.create_task` ile başlatılıyor. |
| **ConsolidationLoop** | Eski monolitik orchestrator. Decompose edilerek `TripletExtractor`, `Tier3Validator` ve `GraphWriter` sınıflarına ayrılmıştır. | **EVET**<br>`mesa_memory/consolidation/loop.py`<br>(`class ConsolidationLoop`) | Boru hattını koordine eden orkestratör olarak çalışmaktadır. |
| **HybridRetriever & CrossEncoderReranker** | FTS5 + LanceDB + KùzuDB havuzlarını birleştiren Stage 1 Alpha Reranker ve Stage 2 `CrossEncoder` learned reranker. | **EVET**<br>`mesa_memory/retrieval/hybrid.py`<br>`mesa_memory/retrieval/reranker.py` | `HybridRetriever` ve `CrossEncoderReranker` sınıfları ve fallback mekanizması (`_load_failed`) aktiftir. |
| **BatchResponseParser & GraphWriter** | LLM yanıtlarından Markdown JSON bloğunu temizleyen, kesik JSON salvaging (`_salvage_truncated_json`) yapan ve graf yazan sınıflar. | **EVET**<br>`mesa_memory/consolidation/parser.py`<br>`mesa_memory/consolidation/writer.py` | 4 katmanlı kurtarma stratejisi (`BatchResponseParser`) ve `GraphWriter` sınıfları eksiksizdir. |

>>> FAZ 0 TAMAMLANDI — bulgu sayısı: 0 (Kritik: 0, Yüksek: 0, Orta: 0, Düşük: 0) — sıradaki faz için onay bekleniyor.
