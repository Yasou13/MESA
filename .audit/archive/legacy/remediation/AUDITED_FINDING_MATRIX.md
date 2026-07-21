# Audited Finding Matrix — Resume Completion

Run: `audit-20260720-120000-independent-master-resume`  
Kural: `OPEN`, `FIXED_NOT_VERIFIED`, `VERIFIED_RESOLVED`, `N/A` ve
`OPTIONAL_FEATURE_BLOCKED` dışındaki durum kullanılmaz. Canonical ID veya source
değiştirilmemiştir.

| ID | Sev. | Blocker | Claimed | Audited | Evidence / correction reason |
|---|---|---|---|---|---|
| ENV-001 | P1 | Evet | OPEN | OPEN | `pip check` fail; core `rich>=15` test venv'inde yok. |
| BOOT-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | Archived API/combined rehearsal + bounded tests. |
| SEC-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | Profile/dotenv negative contracts. |
| OPS-001 | P1 | Evet | OPEN | OPEN | Reproducible locked baseline yok. |
| OPS-002 | P2 | Hayır | OPEN | OPEN | Tarihsel baseline geriye dönük üretilemez. |
| ARCH-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | API/worker role evidence. |
| ARCH-002 | P2 | Hayır | VR | VERIFIED_RESOLVED | Controlled shutdown evidence. |
| ARCH-003 | P1 | Hayır | VR | VERIFIED_RESOLVED | Trace negative tests, protected hashes. |
| ARCH-004 | P1 | Evet | OPEN | OPEN | MCP direct storage creation açık. |
| DOC-001 | P2 | Hayır | OPEN | OPEN | README parity açık. |
| DOC-002 | P1 | Hayır | FNV | FIXED_NOT_VERIFIED | Docker persistence rehearsal yok. |
| FLOW-001 | P1 | Evet | FNV | FIXED_NOT_VERIFIED | Deployed consumer topology yok. |
| DATA-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | Purge journal/real-store restore contracts. |
| SDK-001 | P1 | Evet | OPEN | OPEN | MCP/SDK `/v3` path mismatch açık. |
| SDK-002 | P1 | Hayır | VR | VERIFIED_RESOLVED | Purge response contract. |
| FLOW-002 | P2 | Hayır | VR | VERIFIED_RESOLVED | Finalization/restart/idempotency contracts. |
| SEC-002 | P0 | Hayır | VR | VERIFIED_RESOLVED | ASGI route + persistent SQLite RBAC positive/negative matrix. |
| SEC-003 | P1 | Evet | OPEN | OPEN | Tenant daily-limit scope açık. |
| SDK-003 | P1 | Hayır | VR | VERIFIED_RESOLVED | ASGI SDK auth header contract. |
| DATA-002 | P0 | Hayır | VR | VERIFIED_RESOLVED | Triple-store failure compensation evidence. |
| DATA-003 | P1 | Hayır | VR | VERIFIED_RESOLVED | Model-disabled fail-closed test. |
| DATA-004 | P1 | Hayır | VR | VERIFIED_RESOLVED | Duplicate fallback regression. |
| LOGIC-001 | P1 | Hayır | OPEN | OPEN | Closure evidence yok. |
| LOGIC-002 | P1 | Evet | OPEN | OPEN | Partial extraction state açık. |
| LOGIC-003 | P1 | Evet | OPEN | OPEN | Cold-start quarantine bypass açık. |
| PERF-001 | P2 | Hayır | OPEN | OPEN | Runtime cardinality evidence yok. |
| RLS-001 | P1 | Evet | OPEN | OPEN | Adaptive state tenant scope açık. |
| INPUT-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | Schema bounds contracts. |
| CI-001 | P2 | Hayır | VR | VERIFIED_RESOLVED | Immutable action SHA static verification. |
| DATA-005 | P0 | Evet | VR | FIXED_NOT_VERIFIED | Contract/UNKNOWN result var; asserted real-store E3 failure/restart proof yok. |
| CONC-002 | P1 | Hayır | VR | VERIFIED_RESOLVED | SQLite atomic claim/fence contracts. |
| CONC-003 | P1 | Hayır | OPEN | OPEN | Valence/routing concurrency açık. |
| DLQ-001 | P0 | Evet | VR | FIXED_NOT_VERIFIED | JSONL receipt/restart contract var; production consumer topology/runtime proof yok. |
| QUEUE-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | Tenant/global admission/restart contracts. |
| WORKER-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | Actual worker subprocess/readiness contract. |
| TEST-001 | P0 | Evet | FNV | FIXED_NOT_VERIFIED | 900 bounded pass kanıtı var; CI/coverage/external live scope yok. |
| COVERAGE-001 | P1 | Evet | OPEN | OPEN | CI coverage execution yok. |
| PERF-002 | P1 | Evet | OPEN | OPEN | Full-tenant retrieval scan açık. |
| PERF-003 | P1 | Evet | OPEN | OPEN | Capacity rehearsal yok. |
| PERF-004 | P2 | Hayır | OPEN | OPEN | Hydration N+1 açık. |
| STAGE-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | Role split + lab runtime; deployment gate ayrı açık. |
| CONFIG-002 | P1 | Hayır | VR | VERIFIED_RESOLVED | Explicit fail-closed profile contracts. |
| MIG-001 | P0 | Evet | OPEN | OPEN | Unmanaged schema drift fingerprint yok. |
| MIG-002 | P1 | Evet | OPEN | OPEN | Kuzu version/lock/postflight yok. |
| MIG-003 | P1 | Evet | OPEN | OPEN | Kuzu resume/idempotency yok. |
| MIG-004 | P0 | Evet | OPEN | OPEN | Tenant payload backfill açık. |
| BACKUP-001 | P0 | Hayır | VR | VERIFIED_RESOLVED | Offline manifest + real Lance/Kuzu reopen + independent restore. |
| RESTORE-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | Hash/purge reconciliation + isolated restore. |
| TEST-002 | P1 | Hayır | VR | VERIFIED_RESOLVED | Fresh/legacy migration and recovery tests. |
| DOCKER-001 | P0 | Evet | FNV | FIXED_NOT_VERIFIED | Docker daemon yok; restart persistence yok. |
| DOCKER-002 | P1 | Hayır | VR | VERIFIED_RESOLVED | `.dockerignore` static contract. |
| DOCKER-003 | P1 | Evet | FNV | FIXED_NOT_VERIFIED | Image build yok. |
| CONFIG-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | Compose fail-closed static contract. |
| HEALTH-001 | P1 | Hayır | VR | VERIFIED_RESOLVED | API/worker readiness evidence. |
| CI-002 | P1 | Evet | FNV | FIXED_NOT_VERIFIED | External runner yok. |
| RELEASE-001 | P1 | Evet | FNV | OPEN | Wheel `__pycache__/*.pyc` içerir; clean rebuild hash'i farklı. |

`VR` = claimed `VERIFIED_RESOLVED`; `FNV` = claimed `FIXED_NOT_VERIFIED`; `OPEN`
canonical `CONFIRMED_OPEN` karşılığıdır.

## Independent recount

| Ölçüm | Claimed | Audited |
|---|---:|---:|
| Unique technical finding | 56 | 56 |
| VERIFIED_RESOLVED | 28 | 26 |
| OPEN + FIXED_NOT_VERIFIED | 28 | 30 |
| Open/FNV P0 | 4 | 6 |
| Open/FNV P1 | 20 | 20 |
| Open/FNV P2 | 4 | 4 |
| FIXED_NOT_VERIFIED | 7 | 8 |
| Technical release blocker | 21 | 23 |

False-closure downgrades: `DATA-005`, `DLQ-001`. Classification correction:
`RELEASE-001` is `OPEN`, not merely FNV. No optional MCP capability is counted as a
core release blocker; `test_mcp_forget_memory...` executed in the bounded suite.
