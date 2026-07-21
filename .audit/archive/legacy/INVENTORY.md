# Repository Envanteri

Faz 0’da gerçek dosya sistemi ve tanımlı build/test yapılandırmaları üzerinden doldurulacaktır.

| Alan | Bulgular | Kanıt | Durum |
|---|---|---|---|
| Üst dizinler | Henüz envanterlenmedi | — | Bekliyor |
| Diller | Henüz envanterlenmedi | — | Bekliyor |
| Framework’ler | Henüz envanterlenmedi | — | Bekliyor |
| Entry point’ler | Henüz envanterlenmedi | — | Bekliyor |
| Uygulama servisleri | Henüz envanterlenmedi | — | Bekliyor |
| Worker / background job’lar | Henüz envanterlenmedi | — | Bekliyor |
| Veritabanları / persistence | Henüz envanterlenmedi | — | Bekliyor |
| Harici bağımlılıklar | Henüz envanterlenmedi | — | Bekliyor |
| Deployment dosyaları | Henüz envanterlenmedi | — | Bekliyor |
| Build / test tanımları | Henüz envanterlenmedi | — | Bekliyor |

## Detay kayıt şablonu

| Bileşen | Tür | Yol | Sorumluluk | Çalışma zamanı | Bağımlılıklar | Kanıt | Not |
|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — |


## Faz 0 doğrulanmış envanter

Bu bölüm, önceki boş şablon satırlarının yerine geçen yetkili Faz 0 kaydıdır. Yalnızca statik dosya ve tanım incelemesine dayanır; uygulama, test, dependency, migration veya Docker çalıştırılmamıştır.

### Ağaç ve fiziksel kapsam (en çok 3 seviye)

```text
.
├── mesa_memory/{adapter,api,consolidation,extraction,observability,retrieval,security,valence}
├── mesa_storage/alembic/versions
├── mesa_workers
├── mesa_api
├── mesa_client
├── mesa_mcp
├── mesa_evals/benchmark_adapters
├── mesa-benchmark/{mesa_benchmark/{clients,core,datasets,evaluators,metrics,reports},datasets,scripts,tests}
├── tests/{bench,fixtures,go_live_proofs,integration,utils}
├── scripts, demo/visualizer, examples, notebooks
├── docs/{adr,historical_benchmarks}
├── data/raw
├── .github/workflows ve .githooks
├── storage/{archive_tests,benchmark_*,hello_mesa,*.lance,kuzu_db} [runtime, ignored]
├── results, results_archive, results_smoke_archive [benchmark çıktıları]
├── venv [sanal ortam, ignored]
└── .mypy_cache, .pytest_cache, .ruff_cache, __pycache__, .test_storage_tmp, .benchmarks [cache/runtime]
```

| Ölçüm | Sonuç | Not |
|---|---:|---|
| Fiziksel ağaç (Git dizini hariç) | 89.028 dosya / 11.678 dizin | Venv, cache, storage ve sonuçlar dâhil |
| Analiz kapsamı | 313 dosya / 61 dizin | Git, venv, cache, storage ve büyük sonuç ağaçları hariç |
| Kaynak kapsamındaki ana uzantılar | 207 .py, 43 .md, 11 .yaml, 11 .json, 5 .sh, 4 .yml, 3 .ipynb | Python baskın dil; JS/HTML/CSS demo alanında |
| Büyük alanlar | venv ≈6,9 GB; storage ≈70 MB; mesa-benchmark ≈16 MB | venv ve storage .gitignore ile dışlanmış |
| Büyük kaynak/varlık dosyası | mesa-benchmark/datasets/beam/dataset.json ≈13,8 MB | Versioned benchmark veri kümesi |
| Bozuk symlink | Bulunmadı | find ile statik kontrol |

### Generated, cache ve sonuç alanları

| Yol | Tür | Kapsam durumu | İlk kanıt |
|---|---|---|---|
| venv/ | Yerel Python sanal ortamı | Analiz dışı | .gitignore; çok büyük paket/binary içeriği |
| .mypy_cache/, .pytest_cache/, .ruff_cache/, __pycache__/ | Tool/runtime cache | Analiz dışı | Ağaç ve .gitignore |
| storage/ | Yerel SQLite/Lance/Kuzu/benchmark runtime verisi | Kod analizi dışı, varlığı kaydedildi | .gitignore ve disk içerikleri |
| results/, results_archive/, results_smoke_archive/ | Benchmark sonucu/arşivi | Kod analizi dışı, benchmark kanıtı olarak korunur | .state.json dosyaları ve dizin adları |
| .test_storage_tmp/, .benchmarks | Test/benchmark runtime alanı | Analiz dışı | .gitignore |
| .audit/ | Kalıcı audit çalışma kayıtları | Faz dokümantasyonu | Mevcut çalışma sistemi |

### Ana bileşenler

| Bileşen | Dosya veya dizin yolu | Ana giriş noktası | Olası sorumluluk | Kullanımda olduğuna dair ilk kanıt | Durum |
|---|---|---|---|---|---|
| API/backend | mesa_memory/api/server.py, mesa_api/router.py | app; create_memory_router | FastAPI yaşam döngüsü ve v3 memory endpoint’leri | Docker CMD, server importları | Doğrulandı (statik) |
| Alternatif dev API | scripts/run_server.py | main(), app | Geliştirme sunucusu ve health/metrics endpoint’leri | Makefile dev | Kısmen doğrulandı |
| Web/demo UI | demo/, demo/demo_server.py | index.html, demo_server.py | Demo/visualizer arayüzü | Dosya ağacı | Kısmen doğrulandı |
| Core memory | mesa_memory/ | API server tarafından import edilen modüller | Retrieval, extraction, consolidation, security, valence | pyproject paket kapsamı | Doğrulandı (statik) |
| SQL storage | mesa_storage/sqlite_engine.py, schemas.py, dao.py | AsyncEngine, MemoryDAO | SQLite WAL, şema ve DAO | API server import/başlatma | Doğrulandı (statik) |
| Vector storage | mesa_storage/vector_engine.py | VectorEngine | LanceDB vektör persist/arama | API server state.vector_engine | Doğrulandı (statik) |
| Graph storage | mesa_storage/kuzu_provider.py, kuzu_setup.py | KuzuGraphProvider | Kuzu graph şeması/erişimi | API server state.graph_provider | Doğrulandı (statik) |
| Worker’lar | mesa_workers/ | schedule_*_worker, MaintenanceWorker | Ingestion, consolidation, REM, PageRank ve bakım | API lifespan importları | Doğrulandı (statik) |
| Kalıcı queue | mesa_memory/consolidation/loop.py | PersistentQueue kullanımı | Human-review ve dead-letter kuyrukları | Config queue path’leri | Doğrulandı (statik) |
| Retrieval | mesa_memory/retrieval/ | QueryAnalyzer, HybridRetriever | Hybrid/decomposition/rerank çözümleme | router importları | Doğrulandı (statik) |
| Extraction | mesa_memory/extraction/ | Triplet extractor, REBEL pipeline | Triplet extraction | ingestion worker importları | Doğrulandı (statik) |
| Consolidation lifecycle | mesa_memory/consolidation/, mesa_workers/entity_consolidation_worker.py | ConsolidationLoop | Validator/writer/loop ve arka plan işleme | API lifespan | Doğrulandı (statik) |
| AuthN/AuthZ | mesa_memory/api/server.py, security/rbac.py | API key dependency, AccessControl | API key ve SQLite-backed RBAC | server/router importları | Doğrulandı (statik) |
| SDK/client | mesa_client/{client,langchain}.py | MesaClient, AsyncMesaClient | HTTP client ve LangChain entegrasyonu | MCP ve package export importları | Doğrulandı (statik) |
| MCP server | mesa_mcp/server.py | main() | MCP Server, AsyncMesaClient üzerinden erişim | Server ve client importları | Doğrulandı (statik) |
| Eval sistemi | mesa_evals/ | python -m mesa_evals; bağımsız CLI’ler | Legal audit, load/soak/recall/sweep | __main__, argparse girişleri | Doğrulandı (statik) |
| Benchmark | mesa-benchmark/ | python -m mesa_benchmark | Dataset/client/evaluator/report pipeline | Docker ENTRYPOINT, __main__ | Doğrulandı (statik) |
| Migration | mesa_storage/alembic/, scripts/migrate_to_kuzu.py, scripts/down_migrate.py | Alembic env, argparse CLI | SQL/Kuzu ve raw-log migration araçları | Dosya ağacı | Kısmen doğrulandı |
| Observability | mesa_memory/observability/, docs/prometheus_alerts.yml | ObservabilityLayer, metrics/tracer | Structlog, Prometheus metrics, tracing | API server importları | Doğrulandı (statik) |
| Container/ops | Dockerfile, docker-compose.yml, install.sh | Docker CMD/Compose service | API image, compose, bootstrap | Yapılandırma tanımları | Doğrulandı (statik) |
| CI/CD | .github/workflows/ci.yml, .githooks/pre-push | GitHub Actions job’ları | Build, security gates, package/canary | Workflow ve hook | Doğrulandı (statik) |
| Test altyapısı | tests/, mesa-benchmark/tests/, conftest.py | pytest | Unit/integration/proof/bench testleri | pyproject pytest ayarları | Doğrulandı (statik) |
| Dokümantasyon/deneysel | README.md, ARCHITECTURE.md, docs/, notebooks/, examples/ | — | Kullanım, ADR, runbook, benchmark ve deneyler | Dosya ağacı | Doğrulandı (statik) |

### Entry point’ler

| Dosya | Fonksiyon veya class | Nasıl çağrılır | Gerekli config | Çalışma biçimi |
|---|---|---|---|---|
| mesa_memory/api/server.py | app, lifespan | uvicorn mesa_memory.api.server:app; Docker CMD | MESA_API_KEY; storage/provider config | Doğrudan production container girişi |
| scripts/run_server.py | main(), app, lifespan | python scripts/run_server.py; Makefile dev | MESA_PORT, MESA_API_KEY; seçenekler | Doğrudan dev CLI |
| mesa_api/router.py | create_memory_router() | API server tarafından include_router | State DAO, adapter, RBAC | Dolaylı |
| mesa_mcp/server.py | async main() | python -m mesa_mcp.server | MESA_BASE_URL, MESA_API_KEY, MESA_AGENT_ID | Doğrudan CLI/MCP |
| mesa_evals/__main__.py | module dispatch | python -m mesa_evals | Eval’e özgü CLI argümanları | Doğrudan |
| mesa_evals/{evals,generator,legal_generator,legal_audit,gatekeeper,load_test,recall_harness,run_beam_eval,soak_test,sweep}.py | main() / asyncio.run | python -m veya dosya CLI’leri | Dataset, storage, provider/test argümanları | Doğrudan |
| mesa-benchmark/mesa_benchmark/__main__.py | main() | python -m mesa_benchmark | --config; YAML config | Doğrudan |
| mesa-benchmark/Dockerfile | ENTRYPOINT/CMD | Container | config.yaml varsayılanı | Doğrudan |
| mesa-benchmark/scripts/*.py | main() / argparse | Python CLI | Dataset/harici servis argümanları | Doğrudan |
| scripts/reproduce_benchmark.py, run_ablation.py, run_demo_rag.py | argparse / __main__ | Python CLI veya Makefile bench | Benchmark/LLM config | Doğrudan |
| scripts/migrate_to_kuzu.py, down_migrate.py, migrate_raw_logs_agent_id.py | argparse / __main__ | Python CLI | DB/graph yolu | Doğrudan, çalıştırılmadı |
| scripts/health_check.py, canary_smoke_test.py | __main__ | Makefile health/CI | API/health erişimi | Dolaylı veya CI |
| mesa_workers/*.py | schedule_*_worker, MaintenanceWorker | API lifespan ile asyncio task | DAO/adapter/config | Dolaylı, arka plan |
| .github/workflows/ci.yml | docker-build, build, security-and-audit, installation-verification | push/PR to main | GitHub runner/secrets | Dolaylı CI |

### Build ve dependency sistemi

| Alan | Kanıt | Sonuç |
|---|---|---|
| Paketleme | pyproject.toml | setuptools build backend; proje adı mesa-memory; Python >=3.10 |
| Ana package manager | pip | pyproject dependency/extras kullanımı; root lock dosyası yok |
| Core bağımlılıklar | pyproject.toml | FastAPI/Uvicorn, Pydantic Settings, aiosqlite, LanceDB/PyArrow, Kuzu, observability ve Alembic |
| Optional adapter extras | pyproject.toml [adapters] | Anthropic, OpenAI, Ollama, Groq, LiteLLM |
| Optional ML extras | pyproject.toml [ml] | Torch, Transformers, sentence-transformers, spaCy |
| Dev/test extras | pyproject.toml [dev] | pytest, pytest-asyncio, coverage, benchmark, mypy, Black, Ruff |
| Benchmark dependencies | mesa-benchmark/requirements.txt, requirements-dev.txt | Ayrı pip requirements |
| Benchmark lock | mesa-benchmark/requirements-lock.txt | Var; benchmark image bunu kullanır |
| Node/Java | package.json, pom.xml, build.gradle bulunmadı | Bu keşifte Node/Java build sistemi kanıtı yok |
| Docker build | Dockerfile, mesa-benchmark/Dockerfile | Her ikisi Python 3.10 tabanlı |
| Test komutu tanımı | pyproject, Makefile, CI | pytest; kapsam eşiği 85 olarak tanımlı; çalıştırılmadı |

### Config ve environment isimleri (değer okunmadı)

| Kategori | İsimler | Kullanıldığı ana dosya | Zorunluluk / default | Hassas olabilir mi |
|---|---|---|---|---|
| API/auth | MESA_API_KEY, MESA_DAILY_REQUEST_LIMIT, MESA_PORT | API server, middleware, scripts/run_server.py | API server için key zorunlu; diğerlerinde default var | Evet (API key) |
| LLM/model | MESA_LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL_NAME, OPENAI_API_KEY, ANTHROPIC_API_KEY, MESA_OLLAMA_URL, MESA_ZERO_COST_MODE | config.py, adapter/factory.py, docker-compose.yml | Provider/model/url için default veya optional; key’ler provider’a bağlı | Evet (key/URL) |
| Storage/graph | MESA_STORAGE_PATH, MESA_HUMAN_REVIEW_QUEUE_PATH, MESA_DEAD_LETTER_QUEUE_PATH, MESA_DB_PATH, KUZU_TEST_DIR, KUZU_PERF_DIR | config.py, API server, eval/script/test | Storage path defaultlu; test adları test kapsamlı | Yol bilgisi |
| Extraction/retrieval | MESA_REBEL_ENABLED, MESA_REBEL_DEVICE, MESA_EXTRACTION_LANG, MESA_HYBRID_ALPHA, MESA_HYBRID_BETA, MESA_T_ROUTE, MESA_CROSSENCODER_ENABLED, MESA_CROSSENCODER_MODEL, MESA_CROSSENCODER_POOL_MULTIPLIER | config.py, extraction/retrieval | Defaultlu/opsiyonel | Hayır |
| Resilience/resource | MESA_MAX_RAM_MB, MESA_RETRY_MAX_ATTEMPTS, MESA_RETRY_MIN_WAIT_SEC, MESA_RETRY_MAX_WAIT_SEC, MESA_CIRCUIT_BREAKER_THRESHOLD, MESA_CIRCUIT_BREAKER_COOLDOWN, MESA_VACUUM_HOURS | config.py, API server | Default veya absent fallback | Hayır |
| Benchmark/eval | MEM0_API_KEY, ZEP_API_URL, OLLAMA_HOST, OPENAI_BASE_URL, MESA_MAX_SCENARIOS | .env.example, benchmark adapters, scripts | Adapter/benchmark’e bağlı | Evet (key/URL) |
| CI test-only | MESA_OPENAI_API_KEY, MESA_ANTHROPIC_API_KEY | .github/workflows/ci.yml | CI’da test değerleri tanımlı | Evet (isim) |

### Veritabanı ve dış servis haritası

| Servis | Yapılandırma / kullanım | Local zorunluluk | Test temsili | Production bağımlılığı | Durum |
|---|---|---|---|---|---|
| SQLite (aiosqlite) | mesa_storage/sqlite_engine.py; API server AsyncEngine; storage path | API storage zincirinde gerekli görünüyor | Çok sayıda DAO/storage/RBAC testi | Muhtemel zorunlu, runtime doğrulanmadı | Doğrulandı (statik) |
| LanceDB | mesa_storage/vector_engine.py; VectorEngine | API retrieval için gerekli görünüyor | Vector/storage/chaos testleri | Muhtemel zorunlu, runtime doğrulanmadı | Doğrulandı (statik) |
| KuzuDB | kuzu_provider.py, kuzu_setup.py | API graph zincirinde gerekli görünüyor | Kuzu isolation/performance ve CI graph audit | Muhtemel zorunlu, runtime doğrulanmadı | Doğrulandı (statik) |
| Dosya tabanlı queue | PersistentQueue, JSONL path’leri | Consolidation için yapılandırılmış | Tier-3/consolidation testleri | Harici broker yok | Doğrulandı (statik) |
| Ollama | adapter/ollama.py, config, install.sh | Zero-cost veya Ollama provider seçilirse gerekli | Mock Ollama ve adapter testleri | Opsiyonel/provider’a bağlı | Doğrulandı (statik) |
| OpenAI/Anthropic/Groq/LiteLLM | adapter extras/factory | Provider seçimine bağlı | Adapter/eval testleri | Opsiyonel/provider’a bağlı | Doğrulandı (statik) |
| Qdrant | mesa_evals/benchmark_adapters/mem0_adapter.py | MESA API core için kanıtlanmadı | Mem0 benchmark adapter config | Benchmark adapter’a bağlı | Kısmen doğrulandı |
| Redis/PostgreSQL/MinIO | Kaynakta core entegrasyon bulunmadı | — | — | Bu Faz 0 taramasında kanıt yok | Doğrulanması gerekiyor |

### Test, CI/CD ve dokümantasyon

| Alan | Statik sonuç |
|---|---|
| Test sayısı | tests/ ve mesa-benchmark/tests/ altında 71 Python test/destek dosyası; core paketlerde 108 Python dosyası; dosya bazında yaklaşık %66 |
| Test türleri | Unit/async/storage/RBAC/retrieval/consolidation; go_live_proofs; bench/Locust; benchmark suite testleri |
| Framework ve fixture | pytest, pytest-asyncio; root ve tests/conftest.py; mock/fixture yardımcıları |
| Gerçek servis işaretleri | Kuzu/Lance/SQLite, Ollama mock, API/Locust ve backup-restore proof dosyaları; bu Fazda çalıştırılmadı |
| CI | GitHub Actions: docker-build, build, security-and-audit, installation-verification; Black/Ruff/Mypy/Pytest, TruffleHog, RBAC/chaos/legal audit, package/canary adımları |
| Hook | .githooks/pre-push Black format, Ruff --fix, Mypy, pytest tanımlar; etkinliği doğrulanmadı |
| Docker | Ana API Dockerfile + Compose; ayrı mesa-benchmark Dockerfile; hiçbir image/service çalıştırılmadı |
| Dokümantasyon | README, ARCHITECTURE, CHANGELOG, CONTRIBUTING, RUNBOOK, API reference, 8 ADR, installation, benchmark kullanım/metodoloji ve historical benchmark raporları |
| Eski analizler | REPORT.md, REPORT_UNDOCUMENTED.md, REPORT_CLOSING.md bulunmadı; ARCHITECTURE.md ve benchmark tarihçeleri ileride kodla karşılaştırılacak iddialar içerir |

### İlk kapsam matrisi

| Alan | Var mı | Ana yol | Giriş noktası | Kullanım durumu | Dokümante mi | Sonraki faz |
|---|---|---|---|---|---|---|
| Core memory | Evet | mesa_memory/ | API server | Statik kanıtlı | Evet | Faz 2-4 |
| API/backend | Evet | mesa_memory/api, mesa_api | app / create_memory_router | Statik kanıtlı | Evet | Faz 2-3 |
| Storage | Evet | mesa_storage/ | AsyncEngine/VectorEngine/KuzuGraphProvider | Statik kanıtlı | Evet | Faz 3,6 |
| Workers/queue | Evet | mesa_workers/, consolidation/ | schedule_* / MaintenanceWorker | Statik kanıtlı | Evet | Faz 7 |
| SDK/MCP | Evet | mesa_client/, mesa_mcp/ | Client / MCP main | Statik kanıtlı | Kısmen | Faz 2,5 |
| Demo/examples/notebooks | Evet | demo/, examples/, notebooks/ | HTML/demo_server.py | Doğrulanması gerekiyor | Kısmen | Faz 4 |
| Eval/benchmark | Evet | mesa_evals/, mesa-benchmark/ | module/CLI/Docker | Statik kanıtlı | Evet | Faz 8,10 |
| Migration | Evet | alembic/, scripts/migrate* | Alembic/CLI | Statik kanıtlı | Kısmen | Faz 11 |
| Observability | Evet | observability/, prometheus_alerts.yml | metrics/tracer | Statik kanıtlı | Kısmen | Faz 12 |
| Docker/CI/CD | Evet | Dockerfile, compose, workflow, hook | Docker/Actions | Statik kanıtlı | Evet | Faz 12-13 |
| Runtime/cache/results | Evet | venv/, storage/, results*/ | — | Analiz dışı; korunacak | Kısmen | Faz 1/10 gerektiğinde |
| Docs/ADRs/history | Evet | README, docs/, ARCHITECTURE | — | Kaynak karşılaştırması bekliyor | Evet | Faz 2-14 |

### Faz 0 kapsamında atlanmaması gereken alanlar

- .githooks/pre-push, install.sh, scripts/release_v0.4.2.sh ve scripts/release_v0.5.2.sh operasyon/release araçlarıdır.
- .benchmarks, .test_storage_tmp, storage/ ve üç sonuç ağacı repository içinde görünür fakat kaynak kod değildir.
- demo/visualizer, notebooks/, examples/legal_assistant.py ve data/raw/ ana API zincirinin dışında deneysel/demo veri alanlarıdır.
- docs/mesa-benchmark-çalıştırma.md bir önceki benchmark çalışma promptudur; docs/historical_benchmarks/ geçmiş sonuç/idda kayıtlarıdır.
- mesa-benchmark bağımsız dependency, Docker, datasets, clients ve tests alt ağacıyla ayrı incelenmelidir.
- Alembic sürümleri ve migration scriptleri hem storage hem operasyon kapsamına girer; bu Fazda çalıştırılmadı.


## Faz 2 doğrulanmış modül sınırları

| Bileşen | Sorumluluk / giriş noktası | Ana bağımlılıklar | State / storage / dış erişim | Kullanım kanıtı ve durum |
|---|---|---|---|---|
| `mesa_memory.api.server` | Production FastAPI `app`, `lifespan` | API router, DAO, 3 storage motoru, workers | Global `state`; storage ve worker yaşam döngüsü sahibi | Docker `CMD`; Doğrulandı |
| `mesa_api` | `/v3/memory` ve session router/schemalar | Core retrieval/consolidation, DAO, ingestion worker | Router state tutmaz; DAO ve `BackgroundTasks` kullanır | `server.py` `include_router`; Doğrulandı |
| `mesa_storage` | Async SQLite, LanceDB, Kuzu provider, `MemoryDAO` | aiosqlite/LanceDB/Kuzu | Her engine connection/executor state sahibidir | API lifespan; Doğrulandı |
| `mesa_workers` | Cold path, entity consolidation, REM, maintenance, PageRank | DAO, adapter/core | Ayrı OS process değil, API event-loop task/coroutine modeli | `server.py` task başlatmaları; Doğrulandı |
| `mesa_memory` core | Adapter, valence, consolidation, extraction, retrieval, RBAC, observability | Config, DAO, external LLM adapterları | Config modül-global; queue JSONL; bazı executorlar | API/router import zinciri; Doğrulandı |
| `mesa_client` | Sync/async HTTP SDK ve LangChain köprüleri | `httpx`, paylaşılan Pydantic şemalar | HTTP client state; REST `/v3/memory/*` | MCP ve README kullanım yolu; Doğrulandı |
| `mesa_mcp` | stdio MCP server; dört tool | SDK, paylaşılan şemalar; `get_stats` için doğrudan storage | Import-time env globals; `get_stats` local storage açar | `server.py:call_tool`; Kısmen doğrulandı |
| `mesa_evals` / `mesa-benchmark` | Ayrı eval/benchmark CLI ve adapter pipeline’ları | Core/storage veya harici clientlar | Runtime/dataset state; ağır yol | `__main__`, ayrı Docker/requirements; core production path dışında |
| `scripts/run_server.py` | Alternatif geliştirme FastAPI app | API/router/storage/worker | Ayrı global `_state`, sabit `./storage` yolları | Makefile `dev`; production entry ile eşdeğerliği doğrulanmadı |
| demo/examples/notebooks | Deneysel veya kullanıcı örnekleri | Çeşitli SDK/core yolları | Ana service state sahibi değil | Ana production çağrı zincirinde kanıt yok |
| Migration / deployment / CI | Alembic ve scripts; Docker/Compose/Actions | Storage, Python runtime | Build/deployment state | Dosya tanımları statik; çalıştırılmadı |

Orphan/dead-code adayları: `scripts/run_server.py` ikinci application composition root’tur; demo/notebook ve eval/benchmark yollarının production kullanımı kanıtlanmamıştır. Bu etiketler runtime kullanılmıyor iddiası değildir.

## Faz 4 analiz kapsamı ve teknik borç adayları

| Alan | İnceleme yöntemi | Sonuç |
|---|---|---|
| Storage | DAO/vector/Kuzu/schema/SQLite hata ve mutation yolları | DATA-002..004; runtime migration/concurrency Faz 6’ya bırakıldı |
| API/security | Server dependency, middleware, router session/status modelleri | SEC-002..003, LOGIC-001, SDK-003 |
| Valence/fitness | Motor, state, config ve call-chain statik tarama | Kesin yeni bulgu yok; async hydration/shared state Faz 6 adayı |
| Extraction/consolidation | Parser/bisection/writer/loop/DLQ zinciri | LOGIC-002 |
| Retrieval | Candidate union, alpha/cold-start, quarantine, reranker sınırları | LOGIC-003 |
| Workers/async | Task, queue, cancellation, executor taraması | ARCH-002 mevcut; CONC-CAND-001 açık aday |
| SDK/MCP | Sync/async client, LangChain, MCP tool zinciri | SDK-003; SDK-001/002 yeniden doğrulandı |
| Adapters/config | Factory/provider/fallback/env kullanım zinciri | DATA-003; CONFIG-CAND-001 |
| Observability | Logger/metric/request path taraması | PERF-001 |
| Dead code/teknik borç | Entry point/import/reference taraması | `scripts/run_server.py` ve `MesaStore.mdelete` aday; kesin dead-code değil |


## Faz 7 worker/queue envanter eki

- Production worker modeli ayrı process/container değil, `mesa_memory.api.server.lifespan` içindeki `asyncio.create_task`, `BackgroundTasks` ve sınıf içi task'lerden oluşur.
- `docker-compose.yml` ayrı worker/scheduler/queue broker servisi başlatmaz; external Redis/RabbitMQ/cron/systemd/supervisor entry point bulunmadı.
- Entity consolidation adı node merge değil, agent-scoped description/embedding güncellemesidir.
- `lancedb_wal` kalıcı tablo olsa da bağımsız replay consumer bulunmadı; flush yalnız `align_memory_space` içindedir.


## Faz 8 test envanter eki

| Test alanı | Dosya sayısı | Test sayısı | Framework | Ana amaç | Çalıştırma biçimi |
|---|---:|---:|---|---|---|
| `tests/` | 66 | 819 statik | pytest/pytest-asyncio | Unit, component, storage, router, worker, security | `pytest tests/`; CI bench ignore |
| `tests/bench/` | tests altında | Ayrı sayılmadı | pytest/benchmark/load | Async/Kuzu/Locust performans | CI ignore; manuel/ağır |
| `mesa-benchmark/tests` | 5 | 24 statik | pytest | Benchmark framework unitleri | Alt-proje; ana CI discovery dışında |
| `mesa_evals/` | pytest suite değil | — | CLI | kalite, legal audit, load/soak | Manuel/CI legal audit parçası |
| `tests/go_live_proofs` | script/proof | normal gate değil | Python | MCP/payload/backup kanıt denemeleri | Manuel; bazıları mevcut storage riskli |
