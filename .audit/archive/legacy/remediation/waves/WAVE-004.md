# WAVE-004 — Durable queue / DLQ / worker recovery

## Metadata

| Alan | Değer |
|---|---|
| Run | rem-20260719-164500-W004 |
| Result | PARTIALLY_COMPLETE |
| Scope | DLQ-001, QUEUE-001, WORKER-001, FLOW-001 |
| Evidence | E2 DLQ/worker/trace; E3 yok |

## Completed

DLQ JSONL artık file-lock protected claim token, owner, lease, attempt count, guarded ACK/NACK, expiry reclaim ve visible BLOCKED poison state taşır. Opaque batch return ACK edilmez. Worker trace testte yalnız lab-root altında yazılır. 13 DAO fixture failure sınıflandırıldı ve 33 DAO test geçti.

## Open material scope

Raw-log durable dispatcher/restart delivery (FLOW-001), admission/backpressure/queue health (QUEUE-001), and worker supervision/readiness (WORKER-001) için E2 implementation yoktur. DLQ per-record durable completion receipt ve E3 process proof da yoktur.

## Result

`PARTIALLY_COMPLETE`; canonical counts and NO_GO remain unchanged. WAVE-004-V is queued for DLQ E3 only, but it does not resolve the remaining main WAVE-004 scope.
