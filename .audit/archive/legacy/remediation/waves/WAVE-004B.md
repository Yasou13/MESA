# WAVE-004B — Admission control and backpressure

Result: `FIXED_NOT_VERIFIED`.

- `DEC-REM-008` ile typed, fail-closed global/per-tenant count+byte ve in-flight/retry policy onaylandı.
- SQLite coordinator ölçüm→kontrol→raw-log/dispatch/queue/receipt yazımını tek transaction’da yapar.
- E2: 9 admission/HTTP/restart test geçti; W4A ile 11 target test.
- E3 component: isolated `/storage/mesa-lab/storage/WAVE-004B` SQLite rehearsal concurrent limit, finalize sonrası reopen ve restart muhasebesini geçti.
- API/worker E3 çalıştırılmadı: WAVE-005 runtime profile/dotenv isolation blocker’ı açıktır.
- `QUEUE-001` kapanmaz; canonical sayımlar ve `NO_GO` değişmez.
