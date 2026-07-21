# Regression Log

| Regression ID | Wave | Triggering finding | Description | Severity | Reproduction | Status | Canonical finding |
|---|---|---|---|---|---|---|---|

Başlangıçta kayıt yok.

| REG-W001-001 | WAVE-001 | SEC-002 | Unmapped authenticated principal requests another agent session; deny before grant | Kritik | `tests/test_principal_authorization.py` | Passed at E2; E3 pending | SEC-002 |

| REG-W001-002 | WAVE-001 | SEC-002 | Clean-restart mapped/inactive/READ-only session-create contract extensions | Kritik | `tests/test_principal_authorization.py` | Passed at E2; E3/cross-endpoint scope remains | SEC-002 |

| REG-W002-001 | WAVE-002 | DATA-002 | Kuzu node-write error must propagate, compensate the newly inserted vector, and skip SQLite | Kritik | `tests/test_triple_store_mutation_contract.py` | Passed at E2; E3/recovery pending | DATA-002 |
| REG-W002-002 | WAVE-002 | DATA-004 | Single merge failure must not invoke duplicate-prone `add()` fallback | Yüksek | `tests/test_triple_store_mutation_contract.py` | Passed at E2; real Lance/replay pending | DATA-004 |
| REG-W002-003 | WAVE-002 | DATA-004 | Bulk merge failure must not invoke duplicate-prone `add()` fallback | Yüksek | `tests/test_triple_store_mutation_contract.py` | Passed at E2; real Lance/replay pending | DATA-004 |

| REG-W002-004 | WAVE-002 | DATA-001 | Exact-scope SQLite tombstone → verified Kuzu → verified vector → FINALIZED | Kritik | `tests/test_purge_journal_contract.py` | Passed at E2; E3 absent | DATA-001 |
| REG-W002-005 | WAVE-002 | DATA-001 | Kuzu/vector partial failure preserves tombstone and resumes only missing step | Kritik | `tests/test_purge_journal_contract.py` | Passed at E2; real-store recovery absent | DATA-001 |
| REG-W002-006 | WAVE-002 | DATA-001, SEC-002 | Cross-tenant principal without PURGE grant is denied; retry never returns success | Kritik | `tests/test_purge_journal_contract.py` | Passed at E2; real HTTP E3 absent | DATA-001, SEC-002 |

## WAVE-004

- DAO: 33 passed after classified fixture alignment.
- DLQ/worker/trace: 52 passed; protected trace hash unchanged.
- W3: 2 passed. W2: 10 passed.

| REG-W004B-001 | WAVE-004B | QUEUE-001 | Atomic count/byte/tenant/in-flight/retry admission, overload HTTP and durable restart accounting | Yüksek | `tests/test_queue_admission_contract.py` | 9 passed E2; isolated SQLite component E3 passed; API/worker E3 pending | QUEUE-001 |

| REG-W004C-001 | WAVE-004C | WORKER-001 | Supervisor startup/crash bounded restart/blocked/readiness | Yüksek | `tests/test_worker_supervision_contract.py` | 3 passed E2; role/process E3 pending | WORKER-001 |
| REG-W004D-001 | WAVE-004D | DLQ-001 | Claim-fenced completion receipt before finalized ACK; stale/failed no ACK | Kritik | `tests/test_dispatch_completion_contract.py` | 2 passed E2; JSONL DLQ E3 pending | DLQ-001 |

| REG-W005-001 | WAVE-005 | SEC-001, CONFIG-002, STAGE-001 | explicit runtime profile/dotenv/storage/role contract | Yüksek | `tests/test_runtime_profiles_contract.py` | 4 passed E2 + API/worker E3 | SEC-001, CONFIG-002, STAGE-001 |
| REG-W001V-001 | WAVE-001-V | SEC-002 | isolated HTTP mapped/unmapped/invalid credential | Kritik | `http-e3.txt` | scoped E3 passed; matrix incomplete | SEC-002 |
| REG-W003V-001 | WAVE-003-V | DATA-005, CONC-002 | restart expiry/reclaim/stale token fence | Kritik | `claim-restart-e3.txt` | scoped E3 passed; WAL/alignment incomplete | DATA-005, CONC-002 |
| REG-W004V-001 | WAVE-004-V | DLQ-001 | dispatch/admission/completion restart | Kritik | `queue-restart-e3.txt` | scoped E3 passed; JSONL DLQ incomplete | DLQ-001 |

| REG-CONT-001 | Continuation | WAVE-005, SEC-002 | API-only readiness + mapped/read-only/inactive HTTP route matrix | Kritik | `http-matrix.txt` | passed scoped E3; broader matrix pending | SEC-002, HEALTH-001 |

## Master closure regressions — 2026-07-20

| Regression ID | Wave | Finding | Sonuç | Kanıt |
|---|---|---|---|---|
| REG-MC-001 | W3-V | DATA-005, CONC-002 | 31 passed + real UNKNOWN E3 PASS | vector isolation/reconciliation tests + lab summary |
| REG-MC-002 | W4-V | DLQ-001 | consumer receipt/restart/poison/trusted-root PASS | grouped queue tests and prior E3 |
| REG-MC-003 | W1-V | SEC-002, SDK-002/003 | auth/session/purge/SDK/MCP target groups PASS | grouped tests |
| REG-MC-004 | FLOW-002 | FLOW-002 | durable finalization tests PASS | `test_session_finalization_contract.py` |
| REG-MC-005 | Release | TEST-001 | full core 889 pass/10 stale; failure subset 10/10 PASS | `FINAL_TEST_MATRIX.md` |
| REG-MC-006 | ARCH-003 | ARCH-003 | 4 passed; protected hashes before/after equal | CWD/trace tests |
