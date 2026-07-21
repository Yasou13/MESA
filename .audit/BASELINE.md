# BASELINE

Tarih: 2026-07-21

| Alan | Sonuç |
|---|---|
| Branch | `remediation/production-blockers` |
| Commit | `60c1c576918c01869294140482e4c035669aa2eb` |
| Başlangıç çalışma ağacı | `cold_path_trace.txt`, `dummy.txt` ve `results/mesa_client/contradiction_stress_200_v2_seed42/` untracked; kullanıcıya ait kabul edilip korunuyor |
| Sistem Python | `python` komutu yok |
| Test Python | `venv/bin/python` = Python 3.10.12 |
| Docker Compose | v5.3.1 |
| Dar test | `venv/bin/python -m pytest -q tests/test_deployment_assets.py`: 3 geçti, 6.38 s |
| Compose negatif doğrulama | `docker compose --env-file /dev/null config --quiet`: exit 1; `MESA_API_KEY` zorunlu |
| README ortamıyla Compose doğrulaması | README'deki dört placeholder değişkeni ile `docker compose ... config --quiet`: exit 1; `MESA_PRINCIPAL_ID` zorunlu |
| Compose pozitif doğrulama | Placeholder `MESA_API_KEY` ve `MESA_PRINCIPAL_ID` ile aynı komut: exit 0 |

`.env` dosyası okunmadı; gerçek secret değeri hiçbir komut çıktısına veya audit kaydına alınmadı.
