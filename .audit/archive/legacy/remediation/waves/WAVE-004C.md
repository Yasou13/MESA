# WAVE-004C — Worker supervision and readiness

Result: `FIXED_NOT_VERIFIED`.

`WorkerSupervisor` required queue-task state, bounded restart budget and controlled shutdown semantics provides. `/health/init` required worker degraded/blocked ise 503 verir. E2: startup/shutdown/crash-restart/blocked/readiness 3 test geçti. Existing API-only/worker-only profile WAVE-005 dependency olarak açık; API/worker E3 yapılmadı.
