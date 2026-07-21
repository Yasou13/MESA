# Başlangıç Baseline’ı

Bu kayıt, çalışma ortamı ve doğrulama sonuçlarının tarihçeli baseline’ını tutar. Faz 0 ilk gözlem HEAD’i, audit branch başlangıç HEAD’i ve Faz 14 karar HEAD’i aşağıda ayrı tutulur; ölçülmemiş alanlar sonuç uydurulmadan işaretlenir.

| Alan | Değer | Kanıt / Not |
|---|---|---|
| Kayıt tarihi | 2026-07-17 | Audit sistemi kurulumu sırasındaki güvenlik kontrolü |
| Repository kökü | `/home/yasin/Desktop/MESA` | `pwd` |
| Aktif branch | `audit/production-readiness` | `git status --short --branch` |
| Audit branch başlangıç commit’i | `c69d1f9c18844c393c26291db6c67628d82167f1` | Faz 1 `git rev-parse HEAD` |
| Git çalışma ağacı | Commit edilmemiş takipli değişiklik yok; 3 korunmuş untracked yol mevcut | Faz 1 `git status --short --branch` |
| İşletim sistemi / ortam | Henüz ölçülmedi | — |
| Runtime sürümleri | Henüz ölçülmedi | — |
| Dependency durumu | Henüz ölçülmedi | — |
| Build sonucu | Henüz ölçülmedi | Build çalıştırılmadı |
| Test sonucu | Henüz ölçülmedi | Test çalıştırılmadı |
| Lint sonucu | Henüz ölçülmedi | Lint çalıştırılmadı |
| Type-check sonucu | Henüz ölçülmedi | Type-check çalıştırılmadı |
| Runtime sonucu | Henüz ölçülmedi | Servis başlatılmadı |

## Faz 0 başlangıç kontrolü

| Konu | Doğrulanmış durum |
|---|---|
| Repository kökü | `/home/yasin/Desktop/MESA` |
| Aktif Git branch | `audit/production-readiness` |
| Commit hash | `8798abc90979401d4785cc25d4627517860cb959` |
| Takipli çalışma ağacı | Değişiklik yok (`git status --short` yalnızca `??` girdileri döndürdü) |
| Audit altyapısı | `.audit/` önceki untracked çalışma belgeleridir; `AGENTS.md` audit-owned değildir |
| Önceden mevcut kullanıcı dosyaları | `cold_path_trace.txt`, `dummy.txt`, `results/mesa_client/contradiction_stress_200_v2_seed42/` |
| Kullanıcı dosyalarına işlem | Hiçbirine dokunulmadı, silinmedi veya commit edilmedi |

Bu baseline’da build, test, lint, type-check veya runtime sonucu yoktur; Faz 0 kapsamında hiçbiri çalıştırılmadı.


## Baseline güncelleme kaydı

| Tarih | Alan | Önceki değer | Yeni değer | Kanıt |
|---|---|---|---|---|
| 2026-07-17 | Faz 0 güvenlik ve kapsam baseline’ı | Başlatılmamış şablon | Statik keşif kanıtları eklendi | `COMMAND_LOG.md` |


## Faz 1 yerel donanım profili

| Alan | Ölçüm | Kanıt |
|---|---|---|
| RAM | 16 GiB toplam; Faz 1 sonunda yaklaşık 7,7 GiB kullanılabilir | free -h |
| Swap | 2,0 GiB toplam; yaklaşık 725 MiB kullanım | free -h |
| GPU | Intel TigerLake-LP GT2 / Iris Xe entegre grafik | lspci |
| Ayrık GPU / CUDA / ROCm | Mevcut kabul edilmedi; kullanılmadı | Kullanıcı sınırı ve donanım taraması |
| Disk | Repository filesystem’inde yaklaşık 53 GiB boş | df -h |
| Geçici disk | /tmp 1,6 GiB; kurulum sırasında %99’a çıktı, son ölçüm %78 | df -h |
| Kaynak politikası | Ollama başlatılmadı, model indirilmedi, Docker/benchmark/load/soak çalıştırılmadı | Faz 1 komut günlüğü |

## Faz 1 ortam ve kurulum baseline’ı

| Alan | Sonuç | Kanıt / not |
|---|---|---|
| Branch / commit | audit/production-readiness / c69d1f9c18844c393c26291db6c67628d82167f1 | Git başlangıç kontrolü |
| Korunan untracked yollar | cold_path_trace.txt; dummy.txt; results/mesa_client/contradiction_stress_200_v2_seed42/ | Dokunulmadı |
| Sistem Python | Python 3.13.11; pip 25.3 | python --version; python -m pip --version |
| Mevcut venv | venv/bin/python 3.13.11, ancak pip/FastAPI yok | Kullanılamaz baseline ortamı |
| Docker / Compose | Araç mevcut değil | docker komutu bulunamadı |
| Node/npm/Java | Araçlar mevcut değil | version komutları |
| Gerçek .env | Root ve mesa-benchmark altında mevcut; değerler okunmadı | Sadece dosya varlığı |
| İzole venv | /tmp/mesa_phase1_venv, Python 3.13 | Repo dışı geçici ortam |
| Kurulum komutu | python -m pip install -e '.[dev,adapters]' | install.sh editable core yolu ve CI extras tanımından; ML extras donanım sınırıyla dışarıda |
| Kurulum sonucu | Kısmi; pip check kurulu paketlerde temiz, fakat mesa-memory/LanceDB metadata’sı tamamlanmadı | /tmp kapasite baskısı altında kesildi |
| Güvenli config | /tmp çalışma dizini, env -i, sentetik API key, mock provider | Gerçek .env/storage/sağlayıcıdan yalıtıldı |

## Faz 1 kontrol tablosu

| Kontrol | Komut | Sonuç | Başarılı | Başarısız | Atlanan | Süre | Blocker |
|---|---|---|---:|---:|---:|---:|---|
| Dependency install | /tmp venv içinde pip install -e '.[dev,adapters]' | Kısmi kurulum; metadata eksik | — | 1 | — | Yaklaşık 79 sn | ENV-001 |
| Syntax/import check | İzole import kontrolleri | Core seçili importlar; LanceDB metadata importu başarısız | 1 | 1 | — | Kısa | ENV-001 |
| Format check | black --check mesa_memory/ tests/ | 99 dosya değişmeden kalır | 99 | 0 | 0 | 0,96 sn | Hayır |
| Lint | ruff check mesa_memory/ tests/ | Tüm kontroller geçti | 1 | 0 | 0 | Kısa | Hayır |
| Type-check | mypy ... --no-incremental | 53 kaynak dosyada hata yok | 53 | 0 | 0 | 28,58 sn | Hayır |
| Secret scan | TruffleHog | Araç kurulu değil; kurulmadı | — | — | 1 | — | Hayır |
| Static security scan | Bandit | Araç kurulu değil; kurulmadı | — | — | 1 | — | Hayır |
| Unit/component güvenli alt küme | pytest dört seçili dosya | 70 geçti | 70 | 0 | 0 | 4,94 sn | Hayır |
| Integration tests | Geniş suite | Harici/storage etkisi ve eksik kurulum nedeniyle çalıştırılmadı | — | — | 1 grup | — | ENV-001 |
| E2E smoke tests | API + health | API ready olmadı | 0 | 1 | — | 4,83 sn | BOOT-001 |
| Coverage | Seçili güvenli alt küme | config/schema/factory toplam %95; tam proje değildir | 70 | 0 | 0 | 4,94 sn | Hayır |
| Benchmark tests | mesa-benchmark/tests | Kaynak yoğun / ileri performans fazına ertelendi | — | — | 1 grup | — | Hayır |
| Load tests | tests/bench/locustfile.py | Çalıştırılmadı | — | — | 1 grup | — | Hayır |
| Soak tests | mesa_evals/soak_test.py | Çalıştırılmadı | — | — | 1 grup | — | Hayır |
| Package build | python -m build | build modülü mevcut değil; kurulmadı | 0 | 1 | — | Kısa | Hayır |
| Docker build | docker build | Docker mevcut değil | 0 | 1 | — | Kısa | Hayır |
| API startup | uvicorn ... :app, izole /tmp storage | SQLite migration sonrası ready olmadan exit 3 | 0 | 1 | — | 4,83 sn | BOOT-001 |
| Worker startup | API lifespan dolaylı | API ready olmadığı için çalıştırılmadı | — | — | 1 | — | BOOT-001 |
| MCP startup | import mcp | Optional mcp paketi mevcut değil | 0 | 1 | — | Kısa | Hayır |
| Ollama-dependent tests | Adapter/benchmark/eval canlı yolları | Manuel test gerekli; Ollama’ya müdahale edilmedi | — | — | 1 grup | — | Hayır |
| Health check | /health/init | API ready olmadığı için çalıştırılamadı | 0 | 1 | — | — | BOOT-001 |
| Smoke test | SDK/local write/read/restart | API ready olmadığı için çalıştırılamadı | 0 | 1 | — | — | BOOT-001 |

Not: İzole API denemesi yalnızca /tmp/mesa_phase1_storage altında 104 KiB SQLite test state’i oluşturdu. Gerçek storage/ veya kullanıcı verisi kullanılmadı ve bu geçici alan silinmedi.


## Faz 1.5 — Baseline güvenlik ve izolasyon doğrulaması

| Kontrol | Sonuç | Kanıt / değerlendirme |
|---|---|---|
| Branch ve değişiklik kapsamı | Uygun | Aktif branch `audit/production-readiness`; bu fazda uygulama, test, config, dependency veya deployment dosyası değiştirilmedi |
| Dependency yöntemi | Uygun değil | Root `requirements-core.txt` yok; Faz 1 kaydı `pip install -e '.[dev,adapters]'` ve `/tmp` %99 doluluk gösteriyor |
| Gerçek `.env` izolasyonu | Uygun değil | `mesa_memory/config.py` modül seviyesinde `load_dotenv()` çağırıyor; Faz 1 testleri ve API import zinciri bu modülü import ediyor (SEC-001) |
| Storage/runtime | Kısmen doğrulanabilir | Faz 1 `/tmp` storage kullandı; doğrulama sırasında `/tmp/mesa_phase1_venv` ve `/tmp/mesa_phase1_storage` yoktu |
| Docker, Ollama, canlı sağlayıcı ve ağır testler | Çalıştırılmadı | Komut/test kayıtları Docker build, Ollama, model indirme, benchmark, load, soak ve concurrency yollarının ertelendiğini gösteriyor |
| Komut kanıtı | Yetersiz | Bazı kayıtlar tam maskeli komut/env/argüman yerine özet içeriyor (OPS-002) |

### Faz 1.5 kararı

Faz 1 baseline sonucu güvenli ve tamamen izole kabul edilemez: gerçek `.env` izolasyonu sağlanmamış, talep edilen dependency kurulum yolu mevcut değildir. Güvenlik/izolasyon çıkış kriterleri karşılanmadı.

## Audit dokümantasyon sahipliği ve kanonik kapsam düzeltmesi (2026-07-19)

| Alan | Kanonik kayıt |
|---|---|
| Faz 0 ilk gözlem HEAD’i | `8798abc90979401d4785cc25d4627517860cb959` |
| Audit branch başlangıç HEAD’i | `c69d1f9c18844c393c26291db6c67628d82167f1` |
| Faz 14 karar HEAD’i | `c69d1f9c18844c393c26291db6c67628d82167f1` |
| Faz 14 karar kapsamı | HEAD + commit edilmemiş Faz 9 source diff + audit çalışma ağacı |
| `AGENTS.md` sahipliği | Önceden mevcut, untracked, kullanıcıya ait ve kapsam dışı; audit tarafından changed/staged/committed edilmedi |
