# CI assurance coverage

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
| Benchmark | Benchmark test/type/lint + deterministic RRF ablation JSON | benchmark evidence |
| Container | Non-root image, v3 Compose ve v4 single-combined Compose | Docker logs |
| Doküman | Belgelenmiş CLI/import/Compose komutları | docs smoke |
| Coverage | Full test coverage threshold | HTML/XML report |

`MESA CI` dört Python sürümünde kalite ve core contractlarını çalıştırır.
`v4-contract` işi versioned yüzeyi ayrıca görünür kılar; v4 testlerinin yalnız
genel coverage çalışmasına tesadüfen dahil olmasına güvenilmez.

`Benchmark data quality`, release/research MESA satırlarında
`MesaV4ClientAdapter` kullanır. Legacy/internal config’ler v3 adapter ile
uyumluluk testi yapabilir. Deterministik lane-ablation raporu vector-only,
vector+BM25, vector+graph ve tüm-lane RRF MRR değerlerini kaydeder.

`MESA external release gates` iki deployment topology’sini ayrı test eder:

- `docker-compose.yml`: v3 lexical-core API + worker;
- `docker-compose.v4.yml`: tek combined storage owner.

V4 Compose’ın CI’da model-disabled başlaması yalnız topology, health, ACL/admin
CLI ve restart sözleşmesini kanıtlar. Gerçek full-cognitive provider davranışı
staging kapısıdır.

## CI’nın kanıtlamadığı konular

- 24 saat production-benzeri soak;
- gerçek provider’ın kalite/maliyet/güvenlik sonucu;
- production storage boyutunda migration süresi;
- gerçek deployment platformunun secret, network ve disk davranışı.

Bu kanıtlar release evidence paketine dışarıdan eklenmeden GO verilemez.

## Workflow tetikleme

Core v4 dosyaları, storage şeması, retrieval veya evaluator değiştiğinde hem
`MESA CI` hem benchmark workflow’u tetiklenir. Workflow YAML, dependency lock,
Docker/Compose ve docs değişiklikleri ilgili smoke kapılarını tetikler. Weekly
legacy static baseline yalnız borç görünürlüğüdür ve güncel v4 blocking
contractının yerine geçmez.
