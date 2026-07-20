# Final Test Matrix — Master Closure

| Grup / kanıt | Sonuç | Yorum |
|---|---|---|
| Authorization/session/API ilk grup | 48 passed, 3 stale fixture failure | Üçü yeni purge/principal sözleşmesine hizalandı. |
| Authorization/purge hedef tekrar | 18 passed | Principal, RBAC, target/session purge geçti. |
| Triple-store/purge/data ilk grup | 46 passed, 1 stale chaos expectation | Fail-closed tombstone contractına hizalandı. |
| Purge/chaos hedef tekrar | 14 passed | Retry-pending/blocked davranışı geçti. |
| WAL/claim/replay/reconciliation | 22 passed | Fence, replay, gerçek-store reconciliation. |
| Queue/admission/DLQ/receipt/worker | 24 passed, 1 yanlış negative harness path | Trusted-root test harness’i düzeltildi. |
| Trace/trusted-root hedef tekrar | 9 passed | Missing/outside/symlink path fail-closed. |
| Runtime/deployment/migration/recovery | 20 passed | Profile, worker, assets, migration, DR. |
| Vector model isolation + reconciliation | 31 passed | Model/provider yüklenmeden fail-closed ve UNKNOWN sonucu. |
| Repository-wide core suite | 889 passed, 10 failed | Failures yeni sözleşmelere göre stale expectation/global-state idi; unsafe go-live proofs, external Mem0 ve bench hariç. |
| Failure subset tekrar-1 | 5 passed, 5 failed | Kalan beş kesinleştirildi. |
| Failure subset tekrar-2 | 5 passed | İlk full-suite 10 failure’ın tamamı hedefli olarak kapandı. |
| CWD/protected artifact regression | 4 passed | `dummy.txt`/default trace yazımı yok; protected hashler değişmedi. |
| API-only runtime | PASS | `/health/init` 200/ready, API version 0.6.1, graceful exit 0. |
| Combined model-disabled runtime | PASS (fail-closed) | Required worker degraded: 503, graceful exit 0. |
| Worker-only runtime | PASS | Start/readiness/recovery/stop ve process cleanup. |
| W3 UNKNOWN real-store E3 | PASS | `UNKNOWN_OR_UNVERIFIABLE`, no ACK, model loaded=false. |
| Backup/restore validation | PASS | 15-file manifest, checksums, purge reconciliation. |
| Wheel build/checksum/import | PASS | v0.6.1 offline wheel; repo dışından import. |
| Ruff release-boundary source | PASS | All checks passed. |
| Python compileall | PASS | Source/test syntax tamam. |
| TOML/YAML/JSON parse | PASS | `pyproject`, Compose, CI, provenance. |
| `git diff --check` | PASS | Whitespace/conflict sorunu yok. |
| `pip check` | FAIL / environment drift | `letta/typer`, `litellm/openai`, `textual/rich`; source remediation yapılmadı. |
| Docker build/Compose | EXTERNAL_PENDING | Docker komutu kurulu değil. |
| Harici CI runner | EXTERNAL_PENDING | Workflow statik doğrulandı; GitHub runner çalıştırılmadı. |
| Clean full-suite post-repair rerun | NOT_REPEATED | Safe-resume politikası gereği yalnız başarısız subset tekrarlandı; independent audit kapısıdır. |

Model/Ollama/external provider kullanılmadı. Test storage ve artifact’ları yalnız `/storage/mesa-lab` ile pytest geçici dizinlerinde tutuldu.
# Fast zero-closure test reconciliation — 2026-07-20

| Scope | Result |
|---|---|
| Security/data/migration/runtime critical matrix | 54 passed |
| Metrics/worker/async-loop bounded matrix | 55 passed |
| Lifecycle/retrieval bounded matrix | 139 passed |
| Safe core suite | 902 passed, 1 profile-harness failure; corrected target 1 passed |
| Wheel | two bytecode-free identical builds; fresh install/pip check/imports/CLI passed |
