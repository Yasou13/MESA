# WAVE-004 Evidence Manifest

- Run: `rem-20260719-164500-W004`
- Result: `PARTIALLY_COMPLETE`
- Canonical scope: DLQ-001 (P0), QUEUE-001 (P1), WORKER-001 (P1), FLOW-001 (P1); all release blockers.
- Evidence: E2 passed for durable DLQ file-queue claim/lease/ACK/NACK/poison and isolated worker trace tests. E3 was not run.
- Protected paths were preserved. Runtime output stayed under `/storage/mesa-lab`.
