# Testing and developer workflow

Install the declared environment with `uv sync --locked --python 3.10 --extra dev`, then run
`uv pip check`. Use [CONTRIBUTING](../CONTRIBUTING.md) and [architecture](architecture-v4.md)
for module boundaries and documentation requirements.

```bash
uv lock --check
uv run ruff check .
uv run mypy mesa_memory mesa_storage mesa_workers mesa_api mesa_client \
  --ignore-missing-imports --explicit-package-bases --follow-imports=skip
make test-local
```

`test-local` selects `not optional_provider and not optional_mcp and not live_external`.
For the complete installed contract environment use `dev` and `adapters`, then
`make test-all`. Optional provider/MCP tests use mocks; live-external tests require
explicit external enablement. Do not run official TEST or production calls as
documentation smoke. Historical unsafe proof scripts are not release commands.
The obsolete backup/restore proof that modified real `./storage` data has been
removed. Use the isolated recovery contract tests instead.

Inspect fixture roots before execution: `tests/conftest.py` includes
`.test_storage_tmp` fixtures, while runtime contracts use explicit isolated roots.
Do not point a test at user storage or assume `--basetemp` redirects every
custom fixture. Native stores/model tests may consume substantial memory; run
focused checks sequentially before considering load or provider tests.

Focused tests cover V4 API/SDK, revision/ownership, projection fencing, provider
identity, vector representation, graph provenance, temporal/scope filtering,
recovery and bounded query planning. Start with the affected contract, then run
the established broader gates. Tests must not turn backend errors into empty
successful answers or skip failing security checks.

Lightweight authored-document checks:

```bash
.venv/bin/python scripts/check_documentation.py
```

This checks selected first-party prose links/anchors and supported code-fence
syntax. All maintained root documents, guides and ADRs are included, including
the root architecture entry point. Reports, results and `for_mvp/` are outside
this check. No MkDocs/Sphinx site is configured. Executable quickstart is a
separate check.
A static parse is not an executed example; each smoke report must name real,
mocked, skipped or unavailable backend evidence.

Build artifacts with `uv build --wheel`; inspect metadata, migration data and
fresh imports. CI coverage floor is 82 including SDK, MCP service floor is 55.
Legacy full-tree Black/Mypy debt is reported separately; do not call that debt
fixed because the narrower production checks passed.

<a id="ci-assurance"></a>

## CI assurance

CI gerçek production ortamının yerine geçmez; aynı commit üzerinde
tekrarlanabilir contract ve artifact kanıtı üretir.

| Alan | Blocking kontrol | Artifact |
|---|---|---|
| Secret | TruffleHog history scan + tracked-secret policy | security assurance |
| Kod kalitesi | Ruff, sınırlı Black, production Mypy, override ratchet | job log |
| V4 contract | API, SDK, catalog, ownership, Graph V2, projection, RRF, admin CLI | `v4-contract` |
| V3 uyumluluk | Principal, queue, worker, finalization ve mevcut route testleri | core JUnit |
| Güvenlik | V3 isolation + v4 tenant/workspace/dataset negatif testleri | security/v4 JUnit |
| Migration/DR | Alembic closure, recovery, v4 rebuild/projection testleri | migration/DR |
| Package | Wheel install, v3/v4 imports, admin CLI smoke | package report |
| Retrieval contract | Core deterministic RRF ablation and closure manifest | `v4-contract`; comparative benchmark evidence is external |
| Container | Non-root image, v3 Compose ve v4 single-combined Compose | Docker logs |
| Doküman | Belgelenmiş CLI/import/Compose komutları | docs smoke |
| Coverage | Full test coverage threshold | HTML/XML report |

`MESA CI` dört Python sürümünde kalite ve core contractlarını çalıştırır.
`v4-contract` işi versioned yüzeyi ayrıca görünür kılar; v4 testlerinin yalnız
genel coverage çalışmasına tesadüfen dahil olmasına güvenilmez.

`v4-contract` ve `migration-dr` işleri ayrıca content-free closure evidence
manifesti yükler. Manifest tek Alembic head'i, canonical OpenAPI hash/count'u,
bounded JUnit hash/sonuç özetini, golden candidate/mutation/pipeline/entity/
assertion ID'lerini, deterministik RRF ablation değerlerini ve küçük fixture
SQLite registry/store sayılarını içerir. Source/query içeriği, credential,
filesystem path veya ham test output'u manifestte yer almaz.

Karşılaştırmalı benchmark suite'i ve eski `Benchmark data quality` workflow'u
Core checkout'unda bulunmaz; sahibi `mesa_benchmark` projesidir. Core CI'daki
küçük deterministik RRF fixture'ı karşılaştırmalı model kalitesi kanıtı değildir.
Release için gereken dış benchmark kanıtı ayrıca aynı commit'e bağlanır.

`MESA external release gates` iki deployment topology’sini ayrı test eder:

- `docker-compose.yml`: v3 lexical-core API + worker;
- `docker-compose.v4.yml`: tek combined storage owner.

V4 Compose’ın CI’da model-disabled başlaması yalnız topology, health, ACL/admin
CLI ve restart sözleşmesini kanıtlar. Gerçek full-cognitive provider davranışı
staging kapısıdır.

### CI’nın kanıtlamadığı konular

- 24 saat production-benzeri soak;
- gerçek provider’ın kalite/maliyet/güvenlik sonucu;
- production storage boyutunda migration süresi;
- gerçek deployment platformunun secret, network ve disk davranışı.
- Ortam preflight'ı başarısız olduğu için skipped olan gerçek-provider rehearsal.

Bu kanıtlar release evidence paketine dışarıdan eklenmeden GO verilemez.

### Workflow tetikleme

Core checkout’unda `.github/workflows/ci.yml` ve `external-release-gates.yml`
mevcuttur. Karşılaştırmalı benchmark workflow’u burada tetiklenmez. Release
politikası benchmark kanıtını hâlâ ister; dış workflow/kanıt owner tarafından
aynı commit için ilişkilendirilmeden bu kapı tamamlanmış sayılamaz. Workflow YAML, dependency lock,
Docker/Compose ve docs değişiklikleri ilgili smoke kapılarını tetikler. Weekly
legacy static baseline yalnız borç görünürlüğüdür ve güncel v4 blocking
contractının yerine geçmez.
