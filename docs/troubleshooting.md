# Troubleshooting

Capture version/commit, runtime profile, content-free health, mutation/operation
IDs and error class. Never include keys, claim tokens or full source/query text.

| Symptom | Check | Safe response |
|---|---|---|
| Missing runtime profile/root | Explicit environment and CLI help | Use a dedicated writable root, never repo/home/filesystem root |
| 401 | Key registry, active principal, key rotation | Re-provision/reload authorized credentials; do not disable auth |
| 403/session conflict | ACL, session principal and dataset/agent binding | Grant intended scope offline; start a properly scoped session |
| Insert 422 | Required title/IDs, character bounds and content | Compare actual OpenAPI schema; keep exact source |
| Insert 409 | Frozen revision, identity/idempotency conflict | Reuse exact retry payload or create a new revision |
| Embedding 409/mismatch | Full provider/model/revision/dimension/normalization identity | Follow offline migration/rebuild; equal dimension is not equal space |
| `503 vector_backend_unavailable` | Provider availability and operational identity | Restore the selected provider; no silent model switch |
| `503 graph_backend_unavailable` | Health, timeout, Kùzu schema/parity | Diagnose query/backend and bounded traversal; do not advertise complete results |
| `503 maintenance_pending` | Active/retryable rebuild status | Follow [rebuild runbook](v4-rebuild-runbook.md); no manual lease/queue deletion |
| Mutation stalls or enters DLQ | Projection predecessor, fenced claim, provider errors | Repair cause, then authorized replay if state permits |
| Semantic rejection | Rejection reason and validation policy | Treat as terminal; generic replay returns `NON_REPLAYABLE` |
| Cleanup `BLOCKED` | Store availability and shared ownership | Preserve evidence, repair store, run supported recovery |
| Readiness 503 | Worker liveness, thresholds, canonical backlog | Resolve health; process alive does not mean ready |
| FTS/parity error | Matching Alembic head and checksummed backup | Stop writers and rehearse recovery into a new root |
| Old Kùzu timestamp/schema error | Release code and offline schema migration | Upgrade/rehearse from backups; do not issue ad hoc ALTER or wipe |
| Growing RSS/disk | Lance fragments/versions, queues and retained generations | Measure representative workload; apply supported maintenance, reserve capacity |

Inspect `/v4/capability` before assuming semantic/graph availability. A model-disabled
profile intentionally lacks semantic generation. `wait_until_committed()` can
return failed terminal states; inspect `state` explicitly.

CLI `--help`, [configuration](configuration.md), [API](api-reference.md),
[operations](RUNBOOK.md) and [migration](migration-v0.8.0.md) describe supported
interfaces. Avoid raw SQL edits, in-place restore, forced queue completion and
unbounded retries. Real provider or production-size evidence unavailable in an
audit must remain `NOT EXECUTED`, never converted to a simulated success.
