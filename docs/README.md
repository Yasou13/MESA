# MESA dokümantasyonu

Bu klasörde 12 kullanım/geliştirme rehberi ve 8 mimari karar belgesi vardır.
Hepsini sırayla okumak gerekmez. Amacınıza göre aşağıdaki giriş noktasını seçin.
Paket sürümü 0.9.0; V4 güncel API'dir.

## Nereden başlamalıyım?

| Amacınız | Okuyacağınız belge |
|---|---|
| MESA'yı kurup ilk örneği çalıştırmak | [Kurulum ve ilk çalıştırma](getting-started.md) |
| Model, provider, port veya kapasite ayarlamak | [Yapılandırma](configuration.md) |
| HTTP, Python SDK veya terminal komutlarıyla kullanmak | [API, SDK ve CLI](api-reference.md) |
| Codex/Antigravity bağlamak | [MCP rehberi](../README_MCP.md) |
| Tenant, dataset, revision ve mutation kavramlarını anlamak | [Kavramlar ve veri ekleme](concepts-and-ingestion.md) |
| Sistemin modüllerini ve sınırlarını anlamak | [Mimari](architecture-v4.md) |
| Arama puanlarını, grafı ve context'i anlamak | [Arama ve graf](retrieval-and-graph.md) |
| Yetki, yedekleme, hata veya yükseltme işlemi yapmak | [Operasyon rehberi](RUNBOOK.md) |
| Mevcut V4 verisinden vektör/graf depolarını yeniden üretmek | [Projection rebuild](v4-rebuild-runbook.md) |
| Erişim ve veri güvenliği sınırlarını öğrenmek | [Güvenlik](security-and-privacy.md) |
| Testleri çalıştırmak ve CI sonuçlarını anlamak | [Test ve CI](testing.md) |
| Yeni sürüm yayımlamak | [Release prosedürü](release.md) |

## Birleştirilen konular nerede?

- Docker ve çalışma profilleri: [kurulum](getting-started.md#installation).
- Terminal komutları: [CLI](api-reference.md#cli).
- MCP yönetim endpoint'leri ve onay kimlikleri: [control API](api-reference.md#mcp-control-api).
- Hata tablosu: [troubleshooting](RUNBOOK.md#troubleshooting).
- Eski V4 verisini yükseltme: [v0.8.0 upgrade](RUNBOOK.md#upgrade-v080).
- Diğer MESA projelerinin sorumlulukları: [ekosistem](architecture-v4.md#ecosystem).
- Kapasite ve desteklenmeyen özellikler: [sınırlar](architecture-v4.md#limitations).
- GitHub kontrollerinin kapsamı: [CI](testing.md#ci-assurance).
- v0.8.1 yayın durumu ve istisnaları: [CHANGELOG](../CHANGELOG.md#v081-publication).

## Tasarım kararları ve teknik dosyalar

[ADR dizini](adr/README.md), tasarımın neden seçildiğini açıklar. Yedi güncel
karar ayrı dosyadadır; emekli kararların kısa tarihçesi dizindedir. Tarihsel
kararlar mevcut özellik veya performans garantisi değildir.

[OpenAPI snapshot](reference/openapi.json), üretilmiş API şemasıdır; çalışan
uygulamanın `/openapi.json` çıktısı ve route/schema kodu güncel otoritedir.
[Prometheus kuralları](prometheus_alerts.yml) izleme sistemine yüklenen alarm
kurallarıdır; Markdown rehberi değildir.

## Belgeleri güncel tutma

Her konu için yukarıdaki ana belgeyi güncelleyin; aynı konuyu anlatan yeni
“final/latest/current” dosyaları açmayın. API/paket sürümü, V3/V4 ayrımı,
uygulanmış özellik ve ölçülmüş sonuç birbirine karıştırılmamalıdır.
Üretim uygunluğu [release kapıları](release.md) ile değerlendirilir; birim test
başarısı veya sürüm numarası production GO değildir.

Taşımalarda bağlantıları ve doküman testlerini birlikte güncelleyin; ardından
[test rehberindeki doküman kontrolünü](testing.md) çalıştırın. Eski tam metinler
Git geçmişindedir. `for_mvp/` çalışma planıdır ve bu düzenlemenin dışındadır.
