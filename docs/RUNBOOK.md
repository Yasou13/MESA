# MESA Operations Runbook

This runbook outlines operational procedures for the MESA Enterprise Memory system.

## 1. Deadlock Mitigation
**Symptom:** The MESA API hangs or times out on `POST /v3/memory/insert` and `DELETE /v3/memory/purge`, with CPU utilization remaining normal but SQLite/Kuzu operations stalling indefinitely.

**Root Cause:** Prior to v0.6.0, MESA utilized a nested transaction architecture where `MemoryDAO` held an SQLite `transaction()` lock while making blocking I/O calls to LanceDB and KùzuDB. Exhausting connection pools during bulk operations led to a circular wait deadlock (lock starvation).

**Resolution (Implemented in v0.6.0):**
MESA now strictly follows the **Compensating Transaction Saga Pattern**. Secondary stores (LanceDB and KùzuDB) are written to *before* opening the primary SQLite transaction. If secondary stores fail, compensating transactions are executed. This eliminates the lock inversion completely.
*Action:* No manual intervention is required. This is structurally mitigated.

## 2. API Rate Limiting & Cost Control
**Symptom:** Clients receive `HTTP 429 Too Many Requests`.

**Root Cause:** MESA enforces two layers of API protection:
1. **Burst Protection:** SlowAPI enforces a strict 60 requests per minute limit per Agent ID.
2. **Daily Quota:** MESA enforces a 1,000 requests per day limit per Agent ID, tracked persistently in the `daily_limits` SQLite table.

**Resolution:**
- Advise the client to implement exponential backoff logic (already standard in `mesa_client`).
- For legitimate traffic spikes exceeding 1,000 req/day, administrators can manually override the limit for a tenant via SQLite:
  ```bash
  sqlite3 storage/mesa.db "UPDATE daily_limits SET request_count = 0 WHERE agent_id = '<agent-id>' AND date = date('now');"
  ```

## 3. Database Compaction and Maintenance
**Symptom:** Storage space utilization increases indefinitely. `DELETE /v3/memory/purge` does not reclaim space.

**Root Cause:** MESA employs a soft-delete mechanism for all hot-path operations. `purge` only updates `invalid_at = CURRENT_TIMESTAMP`. This prevents catastrophic WAL contention during peak hours.

**Resolution:**
- Hard-deletes and physical space reclamation are performed strictly by the `MaintenanceWorker` during configured idle windows (default: 3 AM).
- You can force a manual maintenance cycle by running:
  ```bash
  python -m mesa_workers.maintenance --force
  ```

## 4. Key Rotation and Secrets Management
**Symptom:** Need to rotate the MESA Master API Key.

**Root Cause:** API key leaked or standard rotation policy dictates a change.

**Resolution:**
1. Update the environment variable `MESA_API_KEY` on the hosting infrastructure.
2. The CI/CD pipeline enforces `TruffleHog` OSS scanning on every commit to prevent hardcoded credentials. If TruffleHog fails the build, immediately revoke the offending keys.

## 5. Deployment Smoke Testing
**Symptom:** Post-deployment validation required.

**Resolution:**
MESA CI/CD executes `scripts/canary_smoke_test.py` against the built wheel before final release. Administrators can manually execute the smoke test to verify basic dependency and runtime sanity:
```bash
python scripts/canary_smoke_test.py
```
