# Audited Release Gate Matrix

| Gate | Audit durumu | Gerekçe |
|---|---|---|
| Principal/auth/tenant isolation | PASS (lab E2) | HTTP/ASGI + SQLite RBAC pozitif/negatif matrix geçti. |
| Triple-store mutation/purge | PASS (lab E2/E3) | Purge path kanıtı yeterli; DATA-005 downstream/WAL claim'i ayrı FNV'dir. |
| WAL/claim/reconciliation | FIXED_NOT_VERIFIED | Contractlar geçti; asserted real-store E3 failure/restart proof yok (`DATA-005`). |
| DLQ/queue/worker recovery | FIXED_NOT_VERIFIED | Queue/worker contracts geçti; deployed DLQ consumer topology kanıtı yok (`DLQ-001`). |
| Runtime config/dotenv isolation | PASS (lab E2) | Explicit profile ve negative config testleri geçti. |
| Session finalization | PASS (lab E2) | Durable journal/recovery contract geçti. |
| Migration | FAIL | MIG-001/004 ve Kuzu migration residual'ları açık. |
| Backup/restore | PASS (offline lab) | Snapshot validation + independent restore geçti. |
| Package artifact | FAIL | Checksum geçse de `RELEASE-001`: wheel `pyc` cache içeriyor ve reproducible build kanıtı yok. |
| Dependency consistency | FAIL | Core `rich>=15` requirement test edilen venv'de sağlanmıyor. |
| Safe core full suite | PASS (local bounded) | 8 bounded grup/900 collected/900 passed/0 timeout; `test_mem0.py` tamamen ignore edildi. |
| Docker image/runtime | EXTERNAL_PENDING | Docker yok. |
| CI runner | EXTERNAL_PENDING | Workflow koşulmadı. |
| Performance/capacity | FAIL | Açık PERF-002/003 ve capacity rehearsal yok. |
| Rollback/deployment | EXTERNAL_PENDING | Immutable/reproducible artifact ve Docker rehearsal yok. |
| Faz 13 canonical rehearsal | FAIL | `STATIC_PLAN_ONLY`; audit bunu değiştirmez. |

**Release disposition: `NO_GO`.** Audited open/FNV P0=6 (`DATA-005`, `DLQ-001`,
`TEST-001`, `MIG-001`, `MIG-004`, `DOCKER-001`); core dependency/artifact correctionları,
migration/performance eksikleri ve external deployment gate'leri nedeniyle `GO` veya
`CONDITIONAL GO` desteklenmez.
