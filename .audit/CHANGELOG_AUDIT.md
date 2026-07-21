# CHANGELOG_AUDIT

## 2026-07-21 — Faz 0 statik deployment/CI incelemesi

- Uygulama kodu, Docker yapılandırması, CI veya dependency manifesti değiştirilmedi.
- Faz 0 durumu, baseline, odak envanteri, kanıtlı bulgular ve komut kaydı eklendi.
- Kullanıcıya ait untracked çalışma dosyaları değiştirilmedi.

## 2026-07-21 — Faz 9 deployment/CI remediation

- README Docker quickstart, local install, environment tablosu ve proje ağacı güncel fail-closed Compose sözleşmesine eşitlendi.
- Eski requirements manifesti referansları README, API ve Colab dokümanlarından çıkarıldı.
- `uv.lock`, Docker frozen export, Python 3.10–3.13 quality/core-test matrix'i, repository-wide Ruff ve production-package mypy gate'i eklendi.
- `SECURITY.md` ile Dependabot Actions/Docker kapsamı eklendi.
- Doküman/deployment contract regresyonları eklendi; Docker Python 3.13 import smoke geçti.

## 2026-07-21 — CI adapter ve TruffleHog remediation

- Yalnız `zero-cost-contract` job'una gerekli `adapters` extra eklendi.
- TruffleHog action Git tag'i korunurken Docker image sürümünden hatalı `v` öneki kaldırıldı.

## 2026-07-21 — CI uv dependency-check remediation

- `uv sync` kullanan quality ve core-tests job'ları klasik `pip` modülünü çağırmak yerine `uv pip check` kullanacak şekilde düzeltildi.
- Deployment asset regresyonu, eski `uv run python -m pip check` çağrısının geri gelmesini engeller.

## 2026-07-21 — API contract, auth ve retrieval remediation

- README ve Colab health/status/session örnekleri authentication, `agent_id` ve gerçek `/v3/memory/session` route sözleşmesine eşitlendi.
- API lifespan explicit dotenv yüklemesinden sonra auth environment ayarlarını yeniler; server/router'daki debug `print` ve `traceback.print_exc` çağrıları structured logging ile değiştirildi.
- Search response retriever source score'unu ve node content'ini korur.
- Full coverage job'ı Python 3.10 ve 3.13 matrix'ine alındı; artifact adları matrix çakışmasını önler.
- API insert path'i artık yalnız durable dispatch admission yapar; worker runtime dispatch queue'yu fence token ile claim edip cold-path sonucundan sonra completion receipt yazar.
