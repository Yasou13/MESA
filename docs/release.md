# Release procedure

V4 paket sürümü `0.9.0`; depo sahibi bu source release için aşağıda kayıtlı
yayın istisnasını açıkça onayladı. Production qualification halen
release candidate olarak değerlendirilir. Production kararı
Final MVP Certification tamamlanana kadar `NO-GO`dur. Sürüm numarası veya
tag’in atanması tek başına production GO anlamına gelmez. Eski V3 tag/release kayıtları tarihsel bağlamlarıyla değerlendirilir.

## Zorunlu v4 kapıları

Production GO yalnız aşağıdaki kanıtların aynı commit için mevcut olması halinde
verilebilir. Source yayın istisnası varsa sürüme özel kaydedilir; bu kapıları
karşılanmış yapmaz:

- kilitli build, Ruff, Black ve Mypy;
- tüm core ve benchmark contract testleri;
- v4 API/SDK/MCP contract parity;
- Tier-3 ret, retry, lease kaybı, projection crash ve rollback testleri;
- Graph V2 identity/provenance/purge ve iki yönlü reconciliation;
- cross-tenant/workspace/dataset negatif güvenlik testleri;
- dataset filtreli RRF lane-ablation ve vector-only karşılaştırma raporu;
- production-benzeri queue saturation, worker crash ve concurrency;
- offline backup → restore → rebuild → parity → cutover rehearsal;
- seçilen gerçek provider/model ile boot/restart, maliyet ve token kanıtı;
- 24 saat soak.

Son madde dahil tüm kapılar geçene kadar production-readiness kararı `NO-GO`
kalır. CI başarısı tek başına GO değildir.

## Yerel preflight

Temiz checkout ve release commit’inde:

```bash
uv lock --check
uv sync --locked --python 3.11 --extra dev
uv pip check
uv run ruff check .
uv run mypy mesa_memory mesa_storage mesa_workers mesa_api mesa_client \
  --ignore-missing-imports --explicit-package-bases --follow-imports=skip
make test-local
# Adapter-inclusive ayrı gate: dev + adapters kurulumu sonrası make test-all
uv build --wheel
```

Compose sözleşmeleri:

```bash
docker compose -f docker-compose.v4.yml config --quiet
```

Bu model-disabled render yalnız deployment topology doğrulamasıdır.
Full-cognitive kanıt gerçek provider ile ayrı staging rehearsal’dan gelir.

## CI kanıtları

Release commit’inde şu workflow’lar geçmelidir:

- `MESA CI`: quality, v4 contract, security, migrations, package ve coverage;
- Benchmark contractları ve dataset filtreli RRF ablation artifact’ı:
  eski `Benchmark data quality` workflow’u Core’dan çıkarılmıştır; ayrı
  `mesa_benchmark` kanıtı aynı release commitine bağlanmalıdır. Yokluğu PASS değildir;
- `MESA external release gates`: v3 compatibility Compose, v4 combined Compose,
  flow, bounded capacity ve executable docs.

24 saat soak GitHub hosted runner süresine sığdırılmaz; production-benzeri
ortamda dışarıda yürütülür. Başlangıç/bitiş zamanı, image digest, config hash,
health/parity serisi, restart/incident kaydı ve sonuç özeti release evidence
olarak saklanır.

## Sürüm, build ve tag

Kapılar geçtikten sonra:

1. SemVer kararı `pyproject.toml` ve `CHANGELOG.md` içine uygulanır.
2. Release commit’i CI’dan geçirilir.
3. Temiz checkout’ta wheel/SBOM üretilir ve doğrulanır.
4. Yetkili operator signed annotated tag oluşturur.

```bash
git tag -s vX.Y.Z -m "MESA vX.Y.Z"
python scripts/release_preflight.py vX.Y.Z
git push origin vX.Y.Z
```

Core CI tag job’ı reproducible wheel, CycloneDX SBOM ve GitHub OIDC attestation
üretir. Repository workflow’ları GitHub Release veya production deployment’ı
otomatik oluşturmaz.

CI tag checkout’unda yalnız SemVer, `pyproject.toml` sürümü ve CHANGELOG
başlığı doğrulanır. Annotated tag ve imza doğrulaması trusted operator
keyring’ine sahip temiz checkout’ta yukarıdaki tam preflight komutuyla yapılır;
CI bu imza kontrolünü taklit etmez veya zayıflatmaz.

Release operator, wheel ve SBOM için üretilen **OIDC build attestations**
kayıtlarını indirmeli ve artifact digest’leriyle birlikte doğrulamalıdır.

## Migration ve rollback paketi

Release artifact’ları şunları birlikte taşır:

- wheel/image digest ve SBOM;
- Alembic head ve migration rehearsal sonucu;
- backup manifest formatı ve restore doğrulaması;
- V4 upgrade/projection rebuild/parity raporu; V3 içerik taşıma varsa ayrı ingestion doğrulaması;
- retained backup konumu ve cutover rollback prosedürü;
- v4 API/OpenAPI, SDK/MCP contract test sonuçları;
- benchmark ve soak evidence.

Restore mevcut hedefi asla ezmez. Parity veya health kapısı başarısızsa cutover
yapılmaz; eski storage ve v3 compatibility servisi korunur.

## Yayınlama (yalnızca depo sahibi)

PyPI yayını ve sürüm etiketleme adımları:

```bash
uv build
uvx twine check dist/*
uv publish
git tag v0.9.0
git push origin v0.9.0
```

Yayın tamamlandıktan sonra `README.md` dosyasındaki kurulum satırı `pip install "mesa-memory[adapters]"` olarak değiştirilir.

## Sürüme özel yayın istisnaları

v0.8.1 source yayını depo sahibi tarafından 18 Ekim 2026'da açıkça onaylandı.
Yerel GPG anahtarı bulunmadığından bu yayın annotated, imzasız tag kullanır;
signed-tag preflight başarısı iddia edilmez. Gerçek provider ve production
sertifikasyonu bu source yayınına dahil değildir. Ayrıntılar
[v0.8.1 sürüm notlarında](../CHANGELOG.md#v081-publication) kayıtlıdır.

v0.8.0 source yayın onayı, imzasız tag ve eksik sertifikasyon kanıtları
[v0.8.0 sürüm notlarında](../CHANGELOG.md#v080-publication) tutulur. Bu istisna sonraki
sürümlere taşınmaz ve production GO sayılmaz. Bu sayfa genel prosedürü,
sürüm notları ise o yayının durumunu tanımlar.
