# Confirmed root cause

The durable work records had no compare-and-set ownership state. The raw-log read/transition split permitted duplicate claims; WAL rows were bulk-deleted without a per-row success acknowledgement. Alignment did not serialize the complete snapshot-to-promotion interval with mutations. These are code-path facts confirmed by the fail-first contract tests and source review.
