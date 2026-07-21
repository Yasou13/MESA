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
