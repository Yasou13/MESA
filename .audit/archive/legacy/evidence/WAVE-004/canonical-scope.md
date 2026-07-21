# WAVE-004 canonical scope

| ID | Severity / priority | Release blocker | Root-cause group | Required evidence |
|---|---|---|---|---|
| DLQ-001 | Kritik / P0 | Evet | Durable DLQ ownership, tenant metadata, replay and failure retention | E2 claim/ack/crash/poison/tenant; E3 process replay |
| QUEUE-001 | Yüksek / P1 | Evet | Raw-log admission/backpressure, capacity and backlog observability | E2 limits/lag; E3 load/restart |
| WORKER-001 | Yüksek / P1 | Evet | Worker supervision, heartbeat and readiness contract | E2 task-dead/lag health; E3 lifecycle |
| FLOW-001 | Yüksek / P1 | Evet | Durable raw-log dispatch/restart delivery | E2 recovery consumer; E3 crash/restart |

Dependencies: WAVE-003 fenced raw-log/WAL claims are reused conceptually, not duplicated. Explicit out of scope: external broker adoption, API health contract redesign, raw-log admission policy, config/runtime-profile work (WAVE-005), Docker/staging/backup/restore.
