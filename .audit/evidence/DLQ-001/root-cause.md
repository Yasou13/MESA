# Confirmed root causes

- DLQ-001: JSONL records lacked durable state/owner/token/expiry/attempt metadata; destructive or opaque replay acknowledgement could lose evidence.
- Test harness: WAVE-002 graph contract changed from non-awaited fixture behavior to awaited fail-closed graph insertion and tombstone-filtered reads.
- Trace side effect: worker wrote a hard-coded relative path at runtime without a test-safe injection point.

QUEUE-001, WORKER-001 and FLOW-001 require a raw-log dispatch/admission/health contract that is not implemented in this limited DLQ safety repair.
