# Kod, regresyon ve paket doğrulaması

Sonuç tarihi (UTC): 2026-10-15T17:19:02.149696+00:00. Ortam: Linux, Python 3.10.12, uv 0.10.8; yerel testler model-disabled, dış provider kapalı. Başlangıç SHA: `8ddd2dc04cf697fe237e7a49e391543c01182d83`; dokümantasyon commit: `b83aff4188c1a59e8294d5f6587451a1bd08160a`; bağımsız inceleme düzeltmeleri bu commit üzerine uygulanmıştır. Resmî release commit’i oluşturulmadı.

Başarılı bounded koşum: `python3 reports/v0.8.0-documentation-release/20261014T120040Z/run_bounded_tests.py` exit 0. Gerçek alt komutlar `bounded-tests/shard-001.json` … `shard-038.json` ve `bounded-tests/manifest.json` içinde. Her shard `.venv/bin/python -m pytest -q -m 'not live_external' -o faulthandler_timeout=90 --junitxml=<shard.xml> <en fazla 6 dosya>` çalıştırdı. Model/provider disabled, thread sayıları 1. `tests/go_live_proofs` ve `tests/bench` bu yerel regresyon koşumunun dışında; dışlanmaları gizlenmedi.

Sonuç: **1948 test, 0 failure, 0 error, 0 skipped; 38/38 process exit 0**. Özet `bounded-tests/summary.json`; peak process-tree RSS 1304.9 MiB. Limit 1800 MiB, MemAvailable alt sınırı 3 GiB; hiçbir koruma tetiklenmedi. Bu yerel testler release commitinin CI matrisi veya canlı provider kanıtı değildir.

Önceki koşum 1884 pass / 5 README sözleşme failure idi; README gerçek operasyon gereksinimlerine düzeltilerek hedefli 27 test ve son 1948 test geçti. Testler gevşetilmedi. Tek process final denemesi kesildi, tamamlanmış JUnit yok; PASS sayılmadı. Sandbox aiosqlite hang ile OOM olayı birbirine karıştırılmadı. OOM sebebine dair kernel kanıtı yok; 16 raporu.

| Kontrol | Gerçek komut / kanıt | Sonuç |
|---|---|---|
| Ruff | `.venv/bin/ruff check .` | exit0 |
| Production Mypy | `.venv/bin/python -m mypy mesa_memory mesa_storage mesa_workers mesa_api mesa_client --ignore-missing-imports --explicit-package-bases --follow-imports=skip` | exit0, 138 kaynak |
| Layer | `.venv/bin/python scripts/check_layer_imports.py` | exit0 |
| Lock | `uv lock --check --offline` | exit0, 251 paket |
| Installed dependencies | `uv pip check` | exit0, 159 paket |
| Black | pyproject PY310/88 ile sequential Python API | `black-final.json`: dar quality kapsamı ve tarihsel geniş kapsam borcu ayrı raporlanır |
| Dashboard | npm ci/build/lint | exit0 |
| npm security | `npm audit --json` | 0 açık; nanoid 3.3.20/source-map-js 1.2.2 |
| Wheel iki üretim | `SOURCE_DATE_EPOCH=0 uv build --wheel --out-dir <a/b>` | her ikisi exit0; SHA256 aynı |
| Wheel content/import | `packaging-proof.json`, `wheel-import-proof.json` | 195 dosya, Alembic head mevcut, pycache yok, bağımsız wheel import exit0 |
| Aday metadata preflight | `python scripts/release_preflight.py v0.8.0 --metadata-only` | exit1, 0.7.1 metadata / v0.8.0 changelog başlığı yok; beklenen BLOCKED |

Wheel SHA256: `be8bf641aff9d50069b1ee68bd5ed2d7dcf62ed81c5e1e669cac79f945dc9dbe`. Bu **0.7.1** build'dir; v0.8.0 artifact olduğu iddia edilmez. Son full-suite sonrası runtime semantiği değişmedi: açıklama docstringleri, örnek/smoke kontrolleri ve dashboard transitif patch düzeltildi. Smoke ve dashboard ilgili değişiklikler sonrasında tekrar doğrulandı.

Black final: zorunlu CI üç dosyası PASS; yeni LangChain Field açıklaması formatlandı. 44 genel format farkı başlangıç SHA’da da vardı (black-final.json); legacy-static-baseline workflow continue-on-error olduğundan zorunlu dar gate ile aynı değildir. Genel Black PASS iddiası yok.

Doğrulanan düzeltme commit’i: `f627a93ae55a5ce5f0819efddf1a357e54db91f2`. Test dosyaları bu SHA’ya kadarki runtime semantiği ile aynıdır; son değişiklikler açıklama/örnek kontrolleri ve patch lockfile düzeyindedir.
