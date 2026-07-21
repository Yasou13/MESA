# Source analysis

- `mesa_memory/api/server.py:get_api_key` validates a configured key and attaches `PrincipalContext` from server-side configuration.
- `scripts/run_server.py` mirrors that context for its authenticated middleware path.
- `mesa_memory/security/rbac.py` stores explicit `(principal_id, agent_id, permission)` mappings and exact permission checks.
- `mesa_api/router.py:start_session` treats request `agent_id` as target and requires `SESSION_CREATE` before granting session WRITE.
- SDK/MCP search found no `/session/start` contract test. MCP accepts caller-supplied agent/session tool arguments for other operations; that cross-system E3/contract proof remains outside this component run.
- Other session/write/status/purge routes are not claimed principal-mapped by this wave; their coverage remains required follow-up scope.
