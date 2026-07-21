# INVENTORY

Bu Faz 0 kapsamındaki deployment ve kalite-gate envanteri:

| Konu | Kanonik / ilgili dosyalar | Gözlem |
|---|---|---|
| Paket tanımı | `pyproject.toml` | Kök bağımlılık tanımı; sürümler alt sınırlarla belirtilmiş, kök lock dosyası yok |
| Docker runtime | `Dockerfile`, `docker-compose.yml` | Python 3.13.5 runtime; API ve worker ayrı rollerde; named volume `/var/lib/mesa` altında |
| Ortam şablonu | `.env.example` | Fail-closed runtime değişkenleri ve `MESA_PRINCIPAL_ID` içerir; model/provider kapalı |
| Kullanıcı quickstart | `README.md` | Eski LLM/.kuzu/requirements yönergeleri ile yeni runtime topolojisini aynı belgede karıştırıyor |
| Operatör runbook | `docs/installation.md` | Güncel fail-closed Compose sözleşmesini ve gerekli principal değişkenini açıklıyor |
| CI | `.github/workflows/ci.yml`, `.github/workflows/external-release-gates.yml` | Python 3.10 ile test/gate; Docker imajı 3.13.5 ile build edilir ancak 3.13 test matrisi yok |
| Bağımlılık güncellemeleri | `.github/dependabot.yml` | Sadece kök `pip` ekosistemi izleniyor |
| Güvenlik yönetişimi | repository kökü | `SECURITY.md` izlenen dosya olarak bulunmadı |
