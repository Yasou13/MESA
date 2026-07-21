# Faz 0 — Statik yapılandırma incelemesi

Tarih: 2026-07-21
Durum: Tamamlandı; remediation için kullanıcı onayı bekleniyor.

## Kapsam ve yöntem

İnceleme README, `.env.example`, Compose/Docker, `pyproject.toml`, CI/Dependabot
ve ilgili runtime kodunun statik karşılaştırmasını kapsar. Gerçek `.env` dosyası,
production sistemi, migration veya container başlatma kullanılmadı.

## Bulgular

### DOC-003 — Docker quickstart güncel fail-closed Compose sözleşmesiyle uyumsuz

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı / Kritik / P0 |
| Kategori | Dokümantasyon ve deployment konfigürasyonu |
| Release blocker | Evet |
| Referans | `README.md:35-45`; `docker-compose.yml:16-24`; `.env.example:3-12`; `mesa_memory/api/server.py:82-98` |
| Beklenen davranış | Quickstart, çalıştırılabilir minimum ortamı ve etkin çalışma modunu doğru tanımlar. |
| Gerçek davranış | README `MESA_PRINCIPAL_ID` yazmaz, `LLM_API_KEY` verir, `.kuzu/` mount ister ve `docker-compose` kullanır. Compose `MESA_PRINCIPAL_ID` zorunlu kılar, named `mesa-data` volume kullanır, `MESA_MODEL_ENABLED=false` ile `MESA_EXTERNAL_PROVIDER_ENABLED=false` zorlar. |
| Tekrar üretim | README'nin verdiği dört placeholder değişkeniyle `docker compose --env-file /dev/null config --quiet` exit 1 ve `MESA_PRINCIPAL_ID` zorunlu hatası verdi. Placeholder API key + principal ile exit 0 verdi. |
| Etki | İlk kullanım container başlatmadan başarısız olabilir; başlasa bile LLM destekli olduğu iddia edilen kurulum fail-closed, modelsiz/provider'sız profildir. |
| Kök neden | README eski local/LLM topolojisiyle güncel güvenlik hardening'inden sonra güncellenmemiş. |
| Minimum güvenli düzeltme | README quickstart'ını `docs/installation.md` sözleşmesiyle eşitle; principal gereksinimi, named volume, `docker compose`, model/provider'ın kasıtlı kapalı olduğu ve LLM modunun bu Compose profilinde desteklenmediğini açıkça yaz. |
| Regresyon testi | README'den extract edilen örnek environment ile `docker compose config --quiet` çalışan bir docs-contract testi ekle. |
| Tahmini efor / bağımlılık | Küçük / yok |

### DOC-004 — Kök README ve ek dokümanlar olmayan requirements manifestlerine yönlendiriyor

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı / Yüksek / P1 |
| Kategori | Dokümantasyon ve geliştirici deneyimi |
| Release blocker | Hayır; yeni geliştirici kurulumu için yüksek etkili |
| Referans | `README.md:293-306,422-449`; `docs/api-reference.md:220`; `docs/colab_kurulum_rehberi.md:19-28`; `pyproject.toml:22-108` |
| Beklenen davranış | Dokümante edilen kurulum komutlarının repository'de bulunan manifestleri kullanması. |
| Gerçek davranış | `requirements-core.txt` ve `requirements-ml.txt` izlenen dosya değildir; kökte paket tanımı `pyproject.toml`dadır. |
| Kanıt | `git ls-files` ve çalışma ağacı taramasında bu iki dosya bulunmadı; README bunları açıkça çağırıyor. |
| Etki | Belgelenen yerel/Colab kurulumları doğrudan dosya bulunamadı hatası verir. |
| Kök neden | Eski bağımlılık ayrımı kaldırılmış, referanslar temizlenmemiş. |
| Minimum güvenli düzeltme | `pip install -e .`, `pip install -e ".[ml]"` ve `pip install -e ".[ml,adapters]"` biçiminde tek kanonik yönteme geçir; proje ağacını ve API referansını güncelle. |
| Regresyon testi | Dokümanlardaki manifest dosya referanslarını ve örnek pip extras'larını doğrulayan statik test. |
| Tahmini efor / bağımlılık | Küçük / yok |

### CI-003 — Üretim Python sürümünde test coverage yok

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı / Yüksek / P1 |
| Kategori | CI ve release assurance |
| Release blocker | Evet |
| Referans | `Dockerfile:2`; `.github/workflows/ci.yml:32-37,90-93,141-145,160-164,221-225,247-251,263-270`; `.github/workflows/external-release-gates.yml:56-58,91-93,223-225` |
| Beklenen davranış | Docker runtime Python sürümü, en az bir zorunlu test hedefinde çalıştırılır. |
| Gerçek davranış | Test/gate workflow'larında Python 3.10 kullanılıyor; Docker build Python 3.13.5 tabanlı ancak bu sürümde test matrisi yok. |
| Etki | 3.13'e özgü bağımlılık veya çalışma zamanı uyumsuzluğu merge sonrası imajda ortaya çıkabilir. |
| Kök neden | CI test sürümü Docker runtime güncellemesiyle eşitlenmemiş. |
| Minimum güvenli düzeltme | Önce 3.10 ve 3.13'ü zorunlu matrix'e ekle; bağımlılık desteği doğrulanınca 3.11/3.12 genişletmesini ayrı küçük değişiklikte yap. |
| Regresyon testi | Workflow statik testi: Docker Python minor sürümünün test matrix'inde zorunlu olması. |
| Tahmini efor / bağımlılık | Orta / GitHub Actions çalışma süresi ve paketlerin 3.13 uyumluluğu |

### OPS-001 — Lock eksikliği yeniden doğrulandı

Bu bulgu zaten merkezi audit kaydında açıktır. `pyproject.toml` minimum sürüm
kısıtları kullanır; kök `uv.lock`, `poetry.lock`, `Pipfile.lock` veya hash'li
requirements lock dosyası yoktur. Docker builder `pip wheel .` ile o anki
resolver sonucunu kullanır. Temiz, ağ erişimli onaylı runner olmadan doğru
hash lock üretilemeyeceği için bu turda değiştirilmedi.

### SEC-004 — Güvenlik bildirim süreci ve bağımlılık güncelleme kapsamı eksik

| Alan | Değer |
|---|---|
| Durum / önem / öncelik | Doğrulandı / Orta / P2 |
| Kategori | Güvenlik yönetişimi ve supply chain |
| Release blocker | Hayır |
| Referans | repository kökü; `.github/dependabot.yml:1-6` |
| Beklenen davranış | Private disclosure, desteklenen sürümler ve yanıt hedeflerini açıklayan `SECURITY.md`; pip, GitHub Actions ve Docker/base-image güncellemelerini kapsayan otomasyon. |
| Gerçek davranış | İzlenen `SECURITY.md` yok; Dependabot yalnız root `pip` ekosistemini haftalık izliyor. |
| Etki | Güvenlik açığı bildirme yolu belirsiz, Actions/base image güncellemeleri izlenmiyor. |
| Minimum güvenli düzeltme | `SECURITY.md` ekle; Dependabot'a `github-actions` ekosistemini ekle. Docker image güncellemesi için seçilecek aracın (Dependabot destekli Dockerfile veya Renovate) kararı ayrıca kaydedilmeli. |
| Regresyon testi | Policy dosyası ve Dependabot ekosistemlerini doğrulayan statik test. |
| Tahmini efor / bağımlılık | Küçük / Docker update aracına ilişkin proje tercihi |

## Çalıştırılan kontroller

| Komut | Sonuç |
|---|---|
| `docker compose --env-file /dev/null config --quiet` | Exit 1; `MESA_API_KEY` zorunlu |
| README'deki dört placeholder değişkeniyle aynı Compose config | Exit 1; `MESA_PRINCIPAL_ID` zorunlu |
| Placeholder `MESA_API_KEY` + `MESA_PRINCIPAL_ID` ile Compose config | Exit 0 |
| `venv/bin/python -m pytest -q tests/test_deployment_assets.py` | 3 geçti, 6.38 s |
| `git diff --check` | Temiz; audit güncellemesinden önce çalıştırıldı |

## Açık belirsizlikler

- 3.13 uyumluluğu bu ortamda test edilmedi; yalnız CI tanımının kapsamadığı doğrulandı.
- Hash lock üretimi, artifact indirme ve supply-chain seçimleri ağ erişimli temiz runner ve kullanıcı onayı gerektirir.
- Compose container'ları başlatılmadı; `config` doğrulaması yalnız interpolation/topoloji sözleşmesini kanıtlar.

## Önerilen remediation sırası

1. DOC-003: Docker quickstart ve çevre değişkenlerini güncelle, docs-contract testi ekle.
2. DOC-004: Eski requirements referanslarını tüm dokümanlardan çıkar, docs-contract testini genişlet.
3. CI-003: Zorunlu 3.13 test hedefi ekle ve doğrula.
4. OPS-001: Onaylı temiz runner'da hash lock üret, CI/Docker'ı frozen install'a taşı.
5. SEC-004: Disclosure policy ve Dependabot kapsamını ekle.
