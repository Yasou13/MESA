# MESA Core Round 2 — Regresyon ve Güvenlik Doğrulaması

## Komut sonuçları

| Komut/kapsam | Sonuç |
|---|---|
| `pytest -q tests/test_v4_round2_legal_identity.py tests/test_v4_round2_holdout.py tests/test_embedding_restart_migration_lifecycle.py` | 12 passed |
| retrieval phase3/4, independent/comprehensive, migration, API, ingestion | 98 passed |
| four-lane, graph, vector, context, rebuild geniş seçim | ilk RED: 1 failed + 211 passed (eski rebuild head) |
| rebuild preparation/lifecycle/runner/runbook düzeltme sonrası | 18 passed |
| API/projection/scope/temporal/vector-owner/bounded soak | 112 passed |
| `timeout 1800s .venv/bin/pytest -q --tb=short` | **1948 passed, 128 warnings, 478.17 s** |

İlk tam koşuda global `MESA_SQLITE_SYNCHRONOUS=OFF` verilmesi sekiz production
durability expectation'ını kasıt dışı bozdu: **1940 passed, 8 failed**. Bu koşu
ürün verdict'ine dahil edilmedi. Override'sız tek parça tam koşu yukarıda yeşildir.

## Statik kapılar

- `python -m compileall` ve package import: PASS.
- `ruff check --no-cache .`: PASS.
- `scripts/check_layer_imports.py`: PASS.
- `scripts/check_mypy_override_ratchet.py`: PASS.
- production package mypy, 138 source: PASS.
- değişen 13 Python dosyası Black `--check`: PASS.
- `git diff --check`: PASS.

Black sandbox içinde process kısıtıyla bekledi; aynı read-only komut sandbox dışında
exit 0 verdi. Async SQLite koşuları da aiosqlite worker thread wakeup kısıtı nedeniyle
sandbox dışında çalıştırıldı; ürün kodu bu çevre davranışı için değiştirilmedi.

## Güvenlik matrisi

- Cross-tenant/agent/dataset duplicate kimlik: pre-rank SQL + mevcut kapsam testleri PASS.
- Foreign graph hop: gerçek Kùzu kapsam testleri PASS.
- Uncommitted/retracted/superseded current sonuç: PASS.
- Jurisdiction ve temporal offset karşılaştırması: PASS.
- Caller reserved metadata spoof: ayrı testte sahte TCK identity atıldı, typed TBK
  snapshot `verified_ingestion` olarak kaldı.
- Unknown/ambiguous alias ve conflicting law/code: fail closed PASS.
- Wrong dimension/model space/representation: consumer fail-closed diagnostics PASS.

## Bağımsız skeptic review

İki ayrı review ekseni baseline `348efe8...HEAD` diff'ini inceledi:

- Standards: public contract/changelog eksikliği (düzeltildi); legal matching tekrarları
  ve dict data-clump değerlendirme notu (davranış kusuru kanıtlanmadığı için kapsam dışı
  refactor yapılmadı).
- Spec: Python full-scan/index kullanılmaması (düzeltildi), rebuild legal persistence
  kanıtının eksikliği (lifecycle testine eklendi), rapor/delivery eksikliği (bu audit-02
  setiyle kapatıldı). Producer truth attestation MESA_Data bağımlılığı olarak açıklandı.

Review sonrası substantive değişikliklerden sonra tam suite yeniden çalıştırıldı.
