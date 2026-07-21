# Evidence Index

| Evidence ID | Wave | Finding | Type | Command/Test | Before/After | Result | Evidence level | Path |
|---|---|---|---|---|---|---|---|---|

| EV-W000-001 | WAVE-000 | SEC-002 | static decision evidence | Source/ADR review | Before | Principal binding and self-grant path recorded | E1 | `evidence/WAVE-000/before.txt` |
| EV-W000-002 | WAVE-000 | SEC-002, SEC-003, RLS-001, SDK-003, LOGIC-001 | manual_decision | Accepted WAVE-000 contract | After | Decision persisted; no finding closed | E1 | `waves/WAVE-000.md` |

| EV-W001-001 | WAVE-001 | SEC-002 | environment preflight | Python import / pytest availability | Before | Required local test dependencies unavailable | E0/E1 | `evidence/WAVE-001/tests.txt` |

## Kurallar

- Kanıt ID formatı `EV-WXXX-NNN` olmalıdır.
- Test yalnız prose olarak yazılmaz.
- Ham çıktı secret içermemelidir.
- Büyük log yerine maskelenmiş özet ve hash kullanılabilir.
- E3/E4 iddiası gerçek runtime/staging kanıtı gerektirir.

| EV-W001-006 | WAVE-001 | SEC-002 | tooling/error | bwrap source patch transport | Before | Classified TOOLING_ERROR; one attempt only | E0 | `evidence/WAVE-001/patch-transport-error.txt` |
| EV-W001-007 | WAVE-001 | SEC-002 | source patch | atomic exact-anchor transform | Before/After | Principal→agent gate added with rollback backups | E1 | `evidence/WAVE-001/source-edit-method.md` |
| EV-W001-008 | WAVE-001 | SEC-002 | target test | `tests/test_principal_authorization.py` | After | 2 passed; pre-fix scenario recorded 200→expected 403 | E2 | `evidence/WAVE-001/target-test.txt` |
| EV-W001-009 | WAVE-001 | SEC-002 | regression | RBAC/session/router focused suite | After | 30 passed; E3/SDK/MCP remains absent | E2 | `evidence/WAVE-001/regression-tests.txt` |

| EV-W001-010 | WAVE-001 | SEC-002 | command/preflight | project venv, pip check, imports, storage preflight | Clean restart | Passed | E2 | `evidence/WAVE-001/restart-clean-01/environment-preflight.txt` |
| EV-W001-011 | WAVE-001 | SEC-002 | target/regression_test | `tests/test_principal_authorization.py` | Clean restart | 5 passed | E2 | `evidence/WAVE-001/restart-clean-01/target-test.txt` |
| EV-W001-012 | WAVE-001 | SEC-002 | regression_test | principal + RBAC + router + session suite | Clean restart | 33 passed | E2 | `evidence/WAVE-001/restart-clean-01/related-regression-tests.txt` |
| EV-W001-013 | WAVE-001 | SEC-002 | static/cross-system | source hashes, caller review, runtime gap | Clean restart | E3/SDK/MCP gaps recorded | E1 | `evidence/WAVE-001/restart-clean-01/cross-system-check.md` |

| EV-W002-001 | WAVE-002 | DATA-002, DATA-004 | deterministic fault injection | `tests/test_triple_store_mutation_contract.py` | Before | 3 expected failures: graph failure swallowed; merge failures fell back to `add()` | E2 | `evidence/WAVE-002/before.txt` |
| EV-W002-002 | WAVE-002 | DATA-002, DATA-004 | focused regression | same test, isolated lab basetemp | After | 3 passed; fail-closed behavior asserted | E2 | `evidence/WAVE-002/after.txt` |
| EV-W002-003 | WAVE-002 | DATA-001 | design boundary | source contract review | Current | Kuzu purge lifecycle/restore semantics absent; no new ID | E1 | `waves/WAVE-002.md` |

| EV-W002-004 | WAVE-002 | DATA-001 | approved ADR + synthetic migration | `c4f1a8e2d9b0`, SQLite fixture | Before/After | Journal/tombstone lifecycle implemented; synthetic migration applied twice | E2 | `evidence/WAVE-002/data001-migration.md` |
| EV-W002-005 | WAVE-002 | DATA-001 | lifecycle/crash/security regression | `tests/test_purge_journal_contract.py` | Before/After | 5 failed before; 7 passed after; no real-store E3 claim | E2 | `evidence/WAVE-002/data001-after.txt` |

## WAVE-004

- Evidence: `evidence/WAVE-004/`
- Result: `PARTIALLY_COMPLETE`
- Key tests: 52 isolated DLQ/worker/trace, 2 W3, 10 W2, 33 DAO.

| WAVE-004B | `evidence/WAVE-004B-MANIFEST.md`, `/storage/mesa-lab/artifacts/WAVE-004B/e3-component-rehearsal.txt` | DEC-REM-008, E2/E3 component/restart evidence | FIXED_NOT_VERIFIED |

| WAVE-004C | `waves/WAVE-004C.md`, `tests/test_worker_supervision_contract.py` | supervisor/readiness E2 | FIXED_NOT_VERIFIED |
| WAVE-004D | `waves/WAVE-004D.md`, `tests/test_dispatch_completion_contract.py` | completion receipt/fence E2 | FIXED_NOT_VERIFIED |

| WAVE-005 | `evidence/WAVE-005/` | Profile E2/API-only/worker-only E3 | FBNV |
| WAVE-001-V | `evidence/WAVE-001-V/http-e3.txt` | Scoped authorization HTTP E3 | FBNV |
| WAVE-003-V | `evidence/WAVE-003-V/claim-restart-e3.txt` | Scoped claim fence E3 | FBNV |
| WAVE-004-V | `evidence/WAVE-004-V/queue-restart-e3.txt` | Scoped queue E3 | FBNV |

| Continuation | `evidence/WAVE-001-V/http-matrix.txt` | API-only readiness and expanded W1 HTTP route matrix | FBNV |


## Continuation E3 matrix update — 2026-07-19

| Evidence ID | Wave | Finding | Type | Result | Evidence level | Path |
|---|---|---|---|---|---|---|
| EV-W001V-002 | WAVE-001-V | SEC-002 | API-key FastAPI route matrix | 6 passed; scoped ownership/purge proof | E3 subset | `tests/test_session_principal_route_isolation.py` |
| EV-W003V-002 | WAVE-003-V | DATA-005, CONC-002 | SQLite subprocess crash/reopen/fence/WAL ack | PASS | E3 subset | `/storage/mesa-lab/wave-003-v/e3-20260719T193826Z/summary.json` |
| EV-W004V-002 | WAVE-004-V | DLQ-001 | JSONL subprocess lease/replay/poison/malformed/duplicate | PASS | E3 subset | `/storage/mesa-lab/wave-004-v/dlq/e3-20260719T194152Z/summary.json` |
| EV-W005-002 | WAVE-005 | STAGE-001, CONFIG-002 | API/worker/combined profile rehearsal | PASS | E3 subset | `/storage/mesa-lab/evidence/WAVE-005/rerun-20260719T194332Z/summary.json` |


## Continuation contract/alignment/crash update — 2026-07-19

| Evidence ID | Wave | Finding | Type | Result | Classification | Path |
|---|---|---|---|---|---|---|
| EV-W001V-003 | WAVE-001-V | SEC-002, FLOW-002 | OpenAPI/SDK/MCP surface | lifecycle applicability resolved; MCP dependency absent | FBNV/BLOCKED | `/storage/mesa-lab/evidence/WAVE-001-V/contract-surface-20260719/summary.json` |
| EV-W001V-004 | WAVE-001-V | SEC-002 | async SDK real ASGI route | 401 pre-fix; post-fix passed | E3 subset | `tests/test_async_client_auth_contract.py` |
| EV-W003V-003 | WAVE-003-V | DATA-005, CONC-002 | real LanceDB/Kùzu crash/replay | two PASS runs | E3 subset | `/storage/mesa-lab/wave-003-v/vector-alignment/e3-20260719T195738Z/summary.json` |
| EV-W004V-003 | WAVE-004-V | DLQ-001 | injected write crash boundaries | 12 process scenarios PASS | E3 subset | `/storage/mesa-lab/wave-004-v/injected-write-crashes/e3-20260719T195954Z/summary.json` |
| EV-W005-003 | WAVE-005 | STAGE-001, CONFIG-002 | dependent profile rerun | PASS | E3 subset | `/storage/mesa-lab/evidence/WAVE-005/rerun-20260719T200350Z/summary.json` |

## Master closure evidence — 2026-07-20

| Evidence ID | Scope | Finding | Sonuç | Path |
|---|---|---|---|---|
| EV-MC-001 | Safe resume | audit state | Inactive lock recovered; exact checkpoint persisted | `LOCK_RECOVERY.md`, `STATE.md` |
| EV-MC-002 | W3 UNKNOWN | DATA-005/CONC-002 | PASS; no ACK; model_loaded=false | `/storage/mesa-lab/storage/MASTER-CLOSURE/W3/rem-20260720-070821-master-closure-resume/unknown-offline/summary.json` |
| EV-MC-003 | Runtime | BOOT/STAGE/HEALTH | API-only ready; combined fail-closed; exit 0 | `/storage/mesa-lab/evidence/MASTER-CLOSURE/runtime-rehearsal-resume.json` |
| EV-MC-004 | DR | BACKUP/RESTORE | manifest/checksum/real-store reopen PASS | `/storage/mesa-lab/backups/MASTER-CLOSURE/`, `/storage/mesa-lab/restore/MASTER-CLOSURE/` |
| EV-MC-005 | Release artifact | RELEASE/DOCKER/CI | wheel hash/import PASS; Docker/CI external pending | `/storage/mesa-lab/artifacts/MASTER-CLOSURE/RELEASE-final-current/` |
| EV-MC-006 | Final tests | TEST-001/002 | grouped matrix + 889/10 + targeted 10/10 | `FINAL_TEST_MATRIX.md` |
| EV-MC-007 | Protected paths | ARCH-003 | trace and dummy hashes unchanged across final negative regression | `MASTER_CLOSURE_REPORT.md` |
