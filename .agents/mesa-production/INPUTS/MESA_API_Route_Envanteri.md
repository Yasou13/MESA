# MESA API Route Envanteri

Toplam statik tespit edilen üretim route handler: **90**

| Dosya:satır | Handler | Dekoratör |
|---|---|---|
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:112` | `health` | `app.get('/api/health')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:116` | `catalog` | `app.get('/api/catalog')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:143` | `system` | `app.get('/api/system')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:147` | `get_ollama_settings` | `app.get('/api/settings/ollama')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:166` | `test_ollama` | `app.post('/api/settings/ollama/test')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:173` | `save_ollama` | `app.put('/api/settings/ollama')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:182` | `delete_ollama` | `app.delete('/api/settings/ollama')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:187` | `datasets` | `app.get('/api/datasets')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:191` | `get_dataset` | `app.get('/api/datasets/{dataset_id}')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:198` | `get_dataset_scenarios` | `app.get('/api/datasets/{dataset_id}/scenarios')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:211` | `sync_dataset` | `app.post('/api/datasets/{dataset_id}/sync', status_code=202)` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:225` | `dataset_operation` | `app.get('/api/dataset-operations/{operation_id}')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:232` | `plan_preview` | `app.post('/api/plans/preview')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:259` | `create_job` | `app.post('/api/jobs', status_code=202)` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:284` | `list_jobs` | `app.get('/api/jobs')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:291` | `get_job` | `app.get('/api/jobs/{job_id}')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:297` | `job_progress` | `app.get('/api/jobs/{job_id}/progress')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:321` | `job_diagnostics` | `app.get('/api/jobs/{job_id}/diagnostics')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:326` | `control_job` | `app.post('/api/jobs/{job_id}/control', status_code=202)` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:334` | `resume_job` | `app.post('/api/jobs/{job_id}/resume', status_code=202)` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:342` | `retry_job` | `app.post('/api/jobs/{job_id}/retry', status_code=202)` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:350` | `extend_time` | `app.post('/api/jobs/{job_id}/extend-time', status_code=202)` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:362` | `archive_job` | `app.post('/api/jobs/{job_id}/archive')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:371` | `results` | `app.get('/api/results')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:379` | `questions` | `app.get('/api/jobs/{job_id}/questions')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:423` | `export_job` | `app.get('/api/jobs/{job_id}/export')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:442` | `events` | `app.get('/api/jobs/{job_id}/events')` |
| `mesa-benchmark/mesa_benchmark/dashboard/app.py:483` | `frontend` | `app.get('/{path:path}', include_in_schema=False)` |
| `mesa_api/router.py:196` | `insert_memory` | `router.post('/insert', status_code=202, summary='Queue memory insertion (hot path)', response_description='Acknowledged with log_id for tracking', responses={403: {'model': ErrorResponse, 'description': 'RBAC Access Denied'}})` |
| `mesa_api/router.py:317` | `get_status` | `router.get('/status/{log_id}', summary='Query cold-path processing status for a raw_log entry', response_description='Current processing status of the log entry')` |
| `mesa_api/router.py:367` | `search_memory` | `router.post('/search', summary='Search memory', response_description='Retrieved context with latency metrics', response_model=MemorySearchResponse)` |
| `mesa_api/router.py:496` | `get_memory_record` | `router.get('/records/{memory_id}', summary='Retrieve one memory record within an agent and session scope')` |
| `mesa_api/router.py:564` | `purge_memory` | `router.delete('/purge', summary='Soft-delete memory records', response_description='Purge result with affected record count')` |
| `mesa_api/router.py:651` | `start_session` | `router.post('/session/start', tags=['session'], summary='Start a new session', response_description='Returns a new unique session_id', response_model=SessionStartResponse)` |
| `mesa_api/router.py:709` | `get_session_context` | `router.get('/session/{session_id}/context', tags=['session'], summary='Retrieve session context', response_description='Consolidated memory and recent logs for the session', response_model=SessionContextResponse)` |
| `mesa_api/router.py:786` | `end_session` | `router.post('/session/{session_id}/end', tags=['session'], summary='End a session', response_description='Session termination status')` |
| `mesa_api/routers/control/router.py:29` | `create_client` | `router.post('/clients', status_code=201)` |
| `mesa_api/routers/control/router.py:40` | `list_clients` | `router.get('/clients')` |
| `mesa_api/routers/control/router.py:45` | `get_client` | `router.get('/clients/{client_id}')` |
| `mesa_api/routers/control/router.py:52` | `list_connections` | `router.get('/connections')` |
| `mesa_api/routers/control/router.py:59` | `get_settings` | `router.get('/settings')` |
| `mesa_api/routers/control/router.py:64` | `update_setting` | `router.post('/settings')` |
| `mesa_api/routers/control/router.py:79` | `create_policy` | `router.post('/policies', status_code=201)` |
| `mesa_api/routers/control/router.py:93` | `list_policies` | `router.get('/policies')` |
| `mesa_api/routers/control/router.py:100` | `toggle_client_enabled` | `router.put('/clients/{client_id}/enabled')` |
| `mesa_api/routers/control/router.py:107` | `list_bindings` | `router.get('/clients/{client_id}/bindings')` |
| `mesa_api/routers/control/router.py:113` | `list_managed_clients` | `router.get('/managed-clients')` |
| `mesa_api/routers/control/router.py:113` | `list_managed_clients` | `router.get('/codex', include_in_schema=False)` |
| `mesa_api/routers/control/router.py:160` | `revoke_managed_credential` | `router.post('/credentials/{credential_id}/revoke')` |
| `mesa_api/routers/control/router.py:160` | `revoke_managed_credential` | `router.post('/codex/credentials/{credential_id}/revoke', include_in_schema=False)` |
| `mesa_api/routers/control/router.py:173` | `list_activity` | `router.get('/activity')` |
| `mesa_api/routers/control/router.py:186` | `get_activity_call` | `router.get('/activity/{call_id}')` |
| `mesa_api/routers/control/router.py:193` | `list_approvals` | `router.get('/approvals')` |
| `mesa_api/routers/control/router.py:203` | `list_pending_approvals_endpoint` | `router.get('/approvals/pending')` |
| `mesa_api/routers/control/router.py:215` | `decide_approval_endpoint` | `router.post('/approvals/{approval_id}/decide')` |
| `mesa_api/routers/control/router.py:226` | `get_overview` | `router.get('/overview')` |
| `mesa_api/v4_router.py:239` | `create_workspace` | `router.post('/catalog/workspaces', status_code=201)` |
| `mesa_api/v4_router.py:264` | `list_workspaces` | `router.get('/catalog/workspaces')` |
| `mesa_api/v4_router.py:288` | `create_dataset` | `router.post('/catalog/datasets', status_code=201)` |
| `mesa_api/v4_router.py:316` | `list_datasets` | `router.get('/catalog/datasets')` |
| `mesa_api/v4_router.py:343` | `create_document` | `router.post('/catalog/documents', status_code=201)` |
| `mesa_api/v4_router.py:369` | `list_documents` | `router.get('/catalog/documents')` |
| `mesa_api/v4_router.py:392` | `create_revision` | `router.post('/catalog/revisions', status_code=201)` |
| `mesa_api/v4_router.py:419` | `list_revisions` | `router.get('/catalog/revisions')` |
| `mesa_api/v4_router.py:443` | `create_source_chunk` | `router.post('/catalog/source-chunks', status_code=201)` |
| `mesa_api/v4_router.py:473` | `purge_document` | `router.delete('/catalog/documents/{document_id}', status_code=202)` |
| `mesa_api/v4_router.py:508` | `start_session` | `router.post('/sessions/start', status_code=201)` |
| `mesa_api/v4_router.py:547` | `get_capability` | `router.get('/capability', status_code=200)` |
| `mesa_api/v4_router.py:560` | `rebuild_index` | `router.post('/rebuild', status_code=202)` |
| `mesa_api/v4_router.py:565` | `insert_memory` | `router.post('/memory/insert', status_code=202)` |
| `mesa_api/v4_router.py:692` | `search_memory` | `router.post('/memory/search')` |
| `mesa_api/v4_router.py:722` | `mutation_status` | `router.get('/mutations/{mutation_id}', response_model=V4MutationStatusResponse)` |
| `mesa_api/v4_router.py:754` | `rollback_mutation` | `router.post('/mutations/{mutation_id}/rollback', status_code=202)` |
| `mesa_api/v4_router.py:781` | `replay_mutation` | `router.post('/mutations/{mutation_id}/replay', status_code=202)` |
| `mesa_api/v4_router.py:808` | `get_context` | `router.get('/sessions/{session_id}/context')` |
| `mesa_api/v4_router.py:835` | `end_session` | `router.post('/sessions/{session_id}/end')` |
| `mesa_mcp/gateway/app.py:83` | `handshake` | `app.post('/mcp/v1/handshake')` |
| `mesa_mcp/gateway/app.py:89` | `call_tool` | `app.post('/mcp/v1/tools/call')` |
| `mesa_mcp/gateway/app.py:108` | `operation_status` | `app.get('/mcp/v1/operations/{operation_id}')` |
| `mesa_mcp/gateway/app.py:114` | `health` | `app.get('/mcp/v1/health')` |
| `mesa_mcp/gateway/app.py:119` | `codex_session_start` | `app.post('/mcp/v1/codex/sessions/start')` |
| `mesa_mcp/gateway/app.py:154` | `codex_session_end` | `app.post('/mcp/v1/codex/sessions/end')` |
| `mesa_mcp/gateway/http_gateway.py:28` | `connect` | `router.post('/mcp/v1/connect')` |
| `mesa_mcp/gateway/http_gateway.py:52` | `heartbeat_ping` | `router.post('/mcp/v1/heartbeat')` |
| `mesa_mcp/gateway/http_gateway.py:60` | `list_tools` | `router.get('/mcp/v1/tools/list')` |
| `mesa_mcp/gateway/http_gateway.py:74` | `call_tool` | `router.post('/mcp/v1/tools/call')` |
| `mesa_memory/api/server.py:758` | `health_init` | `app.get('/health/init')` |
| `mesa_memory/api/server.py:786` | `health_v3` | `app.get('/v3/health', dependencies=[Depends(get_api_key)])` |
| `mesa_memory/api/server.py:791` | `health` | `app.get('/health', dependencies=[Depends(get_api_key)])` |
| `mesa_memory/api/server.py:796` | `metrics` | `app.get('/metrics', dependencies=[Depends(get_api_key)])` |
