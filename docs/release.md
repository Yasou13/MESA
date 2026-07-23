# Release procedure

V4 şu anda yayınlanmamış bir release candidate’tır. Paket sürümü, release
commit’i ve provider kararı sabitlenmeden `pyproject.toml` sürümü
değiştirilmez. Mevcut `v0.6.1` tag/release tarihsel v3 kaydıdır.

## Zorunlu v4 kapıları

Bir release yalnız aşağıdaki kanıtların aynı commit için mevcut olması halinde
hazırlanabilir:

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
uv sync --locked --extra dev --extra mcp
uv pip check
uv run ruff check .
uv run mypy mesa_memory mesa_storage mesa_workers mesa_api mesa_client \
  --ignore-missing-imports --explicit-package-bases --follow-imports=skip
uv run mypy mesa-benchmark/mesa_benchmark
uv run pytest -q
uv run pytest -q mesa-benchmark/tests
uv build --wheel
```

Compose sözleşmeleri:

```bash
MESA_API_KEY=release-placeholder \
MESA_PRINCIPAL_ID=release-principal \
MESA_MODEL_ENABLED=false \
MESA_EXTERNAL_PROVIDER_ENABLED=false \
docker compose -f docker-compose.v4.yml config --quiet
```

Bu model-disabled render yalnız deployment topology doğrulamasıdır.
Full-cognitive kanıt gerçek provider ile ayrı staging rehearsal’dan gelir.

## CI kanıtları

Release commit’inde şu workflow’lar geçmelidir:

- `MESA CI`: quality, v4 contract, security, migrations, package ve coverage;
- `Benchmark data quality`: benchmark contractları ve RRF ablation artifact’ı;
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

Tag workflow’u reproducible wheel, CycloneDX SBOM ve GitHub OIDC attestation
üretir. Repository workflow’ları GitHub Release veya production deployment’ı
otomatik oluşturmaz.

Release operator, wheel ve SBOM için üretilen **OIDC build attestations**
kayıtlarını indirmeli ve artifact digest’leriyle birlikte doğrulamalıdır.

## Migration ve rollback paketi

Release artifact’ları şunları birlikte taşır:

- wheel/image digest ve SBOM;
- Alembic head ve migration rehearsal sonucu;
- backup manifest formatı ve restore doğrulaması;
- v3→ayrı v4 rebuild/parity raporu;
- retained backup konumu ve cutover rollback prosedürü;
- v4 API/OpenAPI, SDK/MCP contract test sonuçları;
- benchmark ve soak evidence.

Restore mevcut hedefi asla ezmez. Parity veya health kapısı başarısızsa cutover
yapılmaz; eski storage ve v3 compatibility servisi korunur.
