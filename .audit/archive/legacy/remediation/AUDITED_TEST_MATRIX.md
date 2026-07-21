# Audited Test Matrix

| Test / kontrol | Komut veya yöntem | Sonuç | Sınır |
|---|---|---|---|
| Safe core suite | `pytest -q --ignore=tests/go_live_proofs --ignore=tests/bench --ignore=tests/test_mem0.py tests` | `900 passed, 131 warnings`, 322.67 s | External/live/benchmark bilinçli hariç; 100 legacy `__unset__` deprecation warning var. |
| Critical contracts | Auth, session, SDK, finalization, WAL, reconciliation, DLQ, dispatch, queue, worker, migration, recovery seçkisi | `48 passed`, 10.62 s | Bazı DAO side-effect sınırları mock; HTTP/ASGI ve SQLite RBAC gerçek. |
| Migration forward/legacy | `tests/test_migration_closure.py` | PASS | Managed legacy; unmanaged drift değil. |
| Migration rollback smoke | Audit SQLite: `head → a1d2e3f4b5c6 → head` | PASS | Tek son revision geri alma; production rehearsal değil. |
| Recovery contracts | `tests/test_recovery_contract.py` | PASS | Gerçek Lance/Kuzu reopen testi içerir. |
| Independent recovery | Audit root: SQLite + file + vector marker, backup→restore | PASS | Minimal sentetik veri; user storage değil. |
| Prior recovery evidence | `validate_snapshot` backup ve restore | PASS | Her ikisi `valid=true`, 15 manifest file. |
| Wheel checksum | `sha256sum -c SHA256SUMS` | PASS | İçerik/hijyen ayrı satırda fail. |
| Wheel rebuild | `pip wheel --no-deps --no-build-isolation` | FAIL (reproducibility/hygiene) | Farklı SHA; `__pycache__/*.pyc` dahil. |
| Wheel install/import | Audit venv `pip install --no-deps`; target site-packages'tan `mesa_storage.recovery` import | PASS (kısmi) | Tam isolated import dependency indirmeden mümkün değil; clean venv'de core deps eksik olması beklenen. |
| Dependency gate | `pip check`; metadata/installed rich karşılaştırması | FAIL | `rich==13.9.4` core `rich>=15.0.0` ile uyumsuz. |
| Docker/Compose | Statik dosya incelemesi | STATIC PASS | Docker executable/daemon yok; koşulmadı. |
| CI | Workflow statik incelemesi | STATIC PASS | Runner koşulmadı. |
| Whitespace | `git diff --check` | PASS | Audit sonunda tekrarlandı. |
| Hang investigation — first collection 10% | Collect-only + file-binary `-vv --durations=25`, 180 s bounded timeout | PASS / NOT_REPRODUCED | 900 collected; dosya sınırında 113 test: 40 passed/6.55 s ve 73 passed/9.33 s. Full-suite yeniden başlatılmadı. Ayrıntı: `HANG_INVESTIGATION.md`. |

Tüm test/runtime storage'ı `/storage/mesa-lab/audit-independent` veya pytest geçici
dizinleri altındadır. Model/provider/dotenv devre dışıydı.

## Resume bounded full-suite aggregate

Önceki tek-parça run'ın `%8` civarında gözlenen takılması başarı sayılmadı. Aynı
collection doğrudan tekrar başlatılmadan, 74 dosya 8 bounded gruba bölündü; daha önce
tamamlanan ilk 7 dosya yeniden koşulmadı. Her eksik grup 300 s timeout,
`--ignore=tests/test_mem0.py --ignore=tests/go_live_proofs --ignore=tests/bench`,
`-q --durations=20` ile çalıştı.

| Kanıt bölümü | Executed | Passed | Failed / skipped / timeout | Duration |
|---|---:|---:|---:|---:|
| Önceki first-10 A (`adapter*`) | 40 | 40 | 0 / 0 / 0 | 6.55 s |
| Önceki first-10 B (`adaptive_router`, API router/schemas) | 73 | 73 | 0 / 0 / 0 | 9.33 s |
| Resume group 1 (`async_*`) | 46 | 46 | 0 / 0 / 0 | 75.83 s |
| Resume group 2 | 134 | 134 | 0 / 0 / 0 | 18.27 s |
| Resume group 3 | 60 | 60 | 0 / 0 / 0 | 77.60 s |
| Resume group 4 | 127 | 127 | 0 / 0 / 0 | 20.00 s |
| Resume group 5 | 74 | 74 | 0 / 0 / 0 | 16.03 s |
| Resume group 6 | 121 | 121 | 0 / 0 / 0 | 65.33 s |
| Resume group 7 | 216 | 216 | 0 / 0 / 0 | 56.03 s |
| Resume group 8 | 9 | 9 | 0 / 0 / 0 | 5.24 s |
| **Toplam** | **900** | **900** | **0 / 0 / 0** | **350.21 s** |

En yavaş tamamlanan testler retry/circuit-breaker/lifecycle beklemeleriydi:
`test_fault_tolerance.py::test_circuit_breaker_trips_on_continuous_503` (48.08 s),
`test_session_lifecycle.py::TestSessionLifecycle::test_lifecycle` (37.70 s),
`test_async_lock_loop.py::test_loop_circuit_breaker_open` (32.04 s). Hiçbiri timeout
değildir. Her grup sonrasında pytest/uvicorn/worker ve localhost listener kontrolü
temizdi. Tam loglar `/storage/mesa-lab/audit-independent/hang-investigation/bounded-groups/` altındadır.
