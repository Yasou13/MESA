# Source Diff Ownership

Checked at: 2026-07-19T15:18:31+03:00

| Path | Before SHA-256 | Current SHA-256 | Diff summary | Owning wave/faz | Supporting audit/evidence | Confidence | Retained/reverted |
|---|---|---|---|---|---|---|---|
| `mesa_api/router.py` | `3653c8b9f2fbbc065925cc431f643d6998b911116ba838bde6123e9fb3190554` | `2a19fbc24a0d95e04d21904fcddfbd79d5f10cfc073b0a9b0b5d19278b9a6b1d` | `git diff --numstat`: documented scoped change | WAVE-001 authorization implementation | EV-W001-007/010-013; waves/WAVE-001.md | High | Retained |
| `mesa_memory/api/server.py` | `4276163fe4deabe581bdc00198b081b0711b742def73d21c07552cd1402b32c3` | `a076fe157ff9f4d0b0b617216bbd978d9f0ec96fa82b4a958fd6e450e430ae8b` | `git diff --numstat`: documented scoped change | WAVE-001 authorization implementation | EV-W001-007/010-013; waves/WAVE-001.md | High | Retained |
| `mesa_memory/consolidation/loop.py` | `480ce3991912c860163f5c320d96f306735bdd7b3a3f503f1e73124ca5a18530` | `d9006bb29960df4427e0e45b1c10a058fff46968550fb0844f3a2a0ef139bf44` | `git diff --numstat`: documented scoped change | Faz 9 remediation | CHANGELOG Faz 9; DLQ-001; source-diff SHA-256 evidence | High | Retained |
| `mesa_memory/security/rbac.py` | `74dddd20fcf14a0bb07539fde55d5aad0f150bca5115a9783d5b0bcf6501543c` | `5ea8cfb74a0b4508118a53a64ea28906f6483df67c7c02e677b03551125726b6` | `git diff --numstat`: documented scoped change | WAVE-001 authorization implementation | EV-W001-007/010-013; waves/WAVE-001.md | High | Retained |
| `scripts/run_server.py` | `445df2ee0ec5df6711db6a0c5135af1daf5ff18defcdb1b28aa3f345ab9fe5c3` | `1e4e8de3bb2db276ad0c8a1922c3f2bde3c890c8d2fcbeeade9ad285b4f5bf24` | `git diff --numstat`: documented scoped change | WAVE-001 authorization implementation (R2 direct caller recovery) | EV-W001-007; waves/WAVE-001.md R2 section | High | Retained |
| `tests/test_principal_authorization.py` | `HEAD absent (untracked test)` | `2c71ba60b3a8c8ec54e18f8974aa0aa102df3f412778495dacfb6c914a4890d9` | `git diff --numstat`: documented scoped change | test-only WAVE-001 extension | EV-W001-011/012; restart-clean-01 test backup | High | Retained |

## Result

All examined source/test diffs have documented ownership. No unclassified source change exists; no automatic revert was performed. WAVE-001 remains `FIXED_NOT_VERIFIED`; the clean-restart test extension is retained.

## WAVE-002 continuation ownership

| Path | Owning wave | Classification | Confidence | Retained/reverted |
|---|---|---|---|---|
| `mesa_storage/dao.py` | WAVE-002 | DATA-001/002 journal, fail-closed saga, tombstone filter | High | Retained |
| `mesa_storage/kuzu_provider.py` | WAVE-002 | exact journal node delete/verify | High | Retained |
| `mesa_storage/schemas.py` | WAVE-002 | purge journal validation | High | Retained |
| `mesa_storage/alembic/versions/c4f1a8e2d9b0_add_purge_journal.py` | WAVE-002 | additive idempotent migration | High | Retained |
| `mesa_api/router.py` | WAVE-002 | principal PURGE gate and non-success partial failure mapping | High | Retained |
| `tests/test_purge_journal_contract.py` | WAVE-002 | test-only lifecycle/crash/exact-scope extension | High | Retained |

No unclassified source/test/config/Docker/CI/migration change exists in the WAVE-002 owned scope.

## WAVE-003 continuation ownership

| Path | Owning wave | Classification | After SHA-256 | Retained/reverted |
|---|---|---|---|---|
| `mesa_storage/dao.py` | WAVE-002 + WAVE-003 | W3 durable raw-log/WAL claims, replay/ack/recovery | `84253506a724ec5406227ebd272ba006380d29096bdca7f8894f2ce86ab5434e` | Retained |
| `mesa_storage/vector_engine.py` | WAVE-002 + WAVE-003 | W3 complete alignment mutation barrier | `17f1090361865283aca0bbda7dc9658fbfcabc65ec81a2358d332c7847e272cb` | Retained |
| `mesa_workers/ingestion_worker.py` | WAVE-003 | W3 real DAO atomic claim/fenced terminal caller | `02e80c8d98fbd69fda89d819382d2f506b4b3fabcd35223d230120cdaf67cb72` | Retained |
| `mesa_storage/alembic/versions/e9b7c3a1d4f2_add_claim_leases.py` | WAVE-003 | additive durable claim metadata migration | `78ca1776e9162860c36f7d33326166bb9c456c52c4c684f57550ff6c3583d887` | Retained |
| `tests/test_wal_claim_replay_contract.py` | WAVE-003 | E2 controlled concurrency/replay contract | `a4feed8a42e17cec6449934123791fa4246eb902ba2f7d9a64d7c42efe2f91bf` | Retained |

No user-owned untracked file was modified.

## WAVE-004 continuation ownership

| Path | Owning wave | Classification | SHA-256 | Retained/reverted |
|---|---|---|---|---|
| `mesa_memory/consolidation/loop.py` | Faz 9 + WAVE-004 | W4 file-locked DLQ lease/ACK/NACK/poison state | `09562004e6056d0c2bc9e91aa9cc3fb3d86320494827cd58799753923ad89dcf` | Retained |
| `mesa_workers/ingestion_worker.py` | WAVE-003 + WAVE-004 | W4 lab-contained trace override | `b597c188fba036a26390297e8b85c6d1983a3b4275f2388e1cdb707497d55f2f` | Retained |
| `tests/test_dao.py` | WAVE-004 | W2 graph contract fixture alignment | `221d51573ff629480c5e43dbf22acbbf361138fb34f0b5498194deb0c7912f15` | Retained |
| `tests/test_storage_unification.py` | WAVE-004 | W2 rollback source-inspection alignment | `28649b5646eadc1d9ded395d56be828d8914c830931b7915dd44850c44d553a4` | Retained |
| `tests/test_durable_dlq_contract.py` | WAVE-004 | DLQ E2 contract | `7677acc4009289e8da2f4916049751fdc2d839a342076de5b204b4dd3914a740` | Retained |
| `tests/test_ingestion_trace_path.py` | WAVE-004 | protected-trace isolation E2 | `93a4fb564d150ee2626b5885a7b777740ddd72a59059fcfebc0f9c058fe92cf1` | Retained |

No unclassified WAVE-004 source/config/test change exists; protected user files were not modified.

## WAVE-005 ownership

| Path | Owning wave | Reason | Retained |
|---|---|---|---|
| `mesa_memory/config.py` | WAVE-005 | fail-closed runtime profile/dotenv/storage | Retained |
| `mesa_memory/api/server.py` | WAVE-005 | validated paths/profile-gated lifecycle | Retained |
| `mesa_memory/worker_runtime.py` | WAVE-005 | explicit worker-only boundary | Retained |
| `tests/test_runtime_profiles_contract.py` | WAVE-005 | profile E2 | Retained |

## Master closure full source ownership — 2026-07-20

| Owner | Explicit paths | Classification |
|---|---|---|
| W1/B auth + lifecycle | `mesa_api/router.py`, `mesa_api/schemas.py`, `mesa_client/client.py`, `mesa_mcp/server.py`, `mesa_memory/security/rbac.py` | Principal binding, SDK/MCP contract, purge ve session finalization |
| W3/A data | `mesa_storage/dao.py`, `mesa_storage/kuzu_provider.py`, `mesa_storage/schemas.py`, `mesa_storage/vector_engine.py`, `mesa_workers/ingestion_worker.py` | Purge, WAL, dispatch, projection reconciliation, model-isolation caller |
| W4/A queue | `mesa_memory/consolidation/loop.py`, `mesa_workers/supervision.py` | JSONL receipt/fence/restart, supervisor/readiness |
| W5/D runtime | `mesa_memory/api/server.py`, `mesa_memory/config.py`, `mesa_memory/container_health.py`, `mesa_memory/runtime_entrypoint.py`, `mesa_memory/worker_runtime.py`, `scripts/run_server.py` | Explicit runtime roles, dotenv/storage isolation, process health |
| C migration/DR | `mesa_storage/recovery.py`, `mesa_storage/alembic/versions/c4f1a8e2d9b0_add_purge_journal.py`, `e9b7c3a1d4f2_add_claim_leases.py`, `f6d4a7b8c9e0_add_dispatch_journal.py`, `f7e5b9c0d1a2_add_dispatch_queue_payload_bytes.py`, `f8a6c0d1e2b3_add_dispatch_completion_receipts.py`, `a1d2e3f4b5c6_add_wal_projection_states.py`, `b2e3f4a5c6d7_add_session_finalization_journal.py` | Additive migrations ve offline recovery CLI |
| D deployment | `.dockerignore`, `.env.example`, `.github/workflows/ci.yml`, `Dockerfile`, `docker-compose.yml`, `pyproject.toml` | Image/Compose/CI/artifact/recovery gates |
| Existing test reconciliation | `tests/test_api_router.py`, `test_api_schemas.py`, `test_chaos.py`, `test_consolidation.py`, `test_dao.py`, `test_dao_coverage.py`, `test_p0b_missing.py`, `test_p0c_loop.py`, `test_router_coverage.py`, `test_session_lifecycle.py`, `test_storage_unification.py`, `test_tier3_resilience.py` | Yeni fail-closed/durable sözleşmelere stale fixture/expectation hizalaması |
| New regression tests | `tests/test_async_client_auth_contract.py`, `test_deployment_assets.py`, `test_dispatch_completion_contract.py`, `test_downstream_fence_reconciliation_contract.py`, `test_durable_dispatch_contract.py`, `test_durable_dlq_contract.py`, `test_ingestion_trace_path.py`, `test_migration_closure.py`, `test_principal_authorization.py`, `test_purge_journal_contract.py`, `test_queue_admission_contract.py`, `test_queue_trusted_root_contract.py`, `test_recovery_contract.py`, `test_runtime_profiles_contract.py`, `test_session_finalization_contract.py`, `test_session_principal_route_isolation.py`, `test_triple_store_mutation_contract.py`, `test_vector_model_isolation.py`, `test_wal_claim_replay_contract.py`, `test_worker_runtime_contract.py`, `test_worker_supervision_contract.py` | Campaign A–E E2/E3 contracts |
| Audit records | `.audit/*.md`, `.audit/remediation/**` | Historical append-only reconciliation ve independent handoff |

`git status --short` içinde görünen tracked/untracked implementation, test, config, Docker, CI ve migration yollarının tamamı yukarıda sınıflandırılmıştır. Runtime artifact’ları repository’ye eklenmedi. Korunan `AGENTS.md`, `cold_path_trace.txt`, `dummy.txt` ve `results/mesa_client/contradiction_stress_200_v2_seed42/` stage/commit edilmedi; final negative regression öncesi/sonrası trace ve dummy içerik hashleri aynı kaldı. Unclassified tracked source change yoktur.
# Fast zero-closure ownership — 2026-07-20

This run added only scoped packaging, optional-import, MCP boundary, and test-harness remediation on top of the pre-existing user/remediation worktree. No protected path was modified or staged; no local checkpoint commit was created because pre-existing dirty ownership cannot be safely separated.
