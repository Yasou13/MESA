# MESA MCP

MESA MCP exposes the existing MESA HTTP service to local MCP clients over
stdio. It does not import or access MESA storage backends directly.

## Prerequisites

Run a MESA API service first and configure its URL and API key. The MCP server
uses its existing `/v3` service endpoints, including the scoped record lookup
endpoint added for MCP.

```bash
MESA_RUNTIME_PROFILE=combined \
MESA_STORAGE_ROOT=/absolute/path/to/mesa-data \
MESA_API_KEY=your-local-api-key \
MESA_PRINCIPAL_ID=local-mcp \
.venv/bin/python -m mesa_memory.runtime_entrypoint
```

Do not commit API keys. Configure them in Antigravity's local environment or a
secret manager.

## Antigravity

Copy `.agents/mcp_config.example.json` to `.agents/mcp_config.json`, then add
the required `MESA_API_KEY` to the local MCP environment. The config uses
absolute paths and launches Python directly; stdout remains reserved for MCP
JSON-RPC and logs go to stderr.

## Tools

- `mesa_health` checks MCP and the MESA service.
- `mesa_store_memory` queues a durable, scoped memory record.
- `mesa_search_memory` searches memories within one project.
- `mesa_get_memory` fetches an exact record ID in that same project.
- `mesa_get_context` returns a token-bounded historical context bundle.

The first version is local-only, stdio-only, and has no lifecycle/delete or
graph tools. `project_id` is converted to a server-controlled MESA session
scope; the MCP caller cannot choose an actor or namespace.

## Smoke check

With the same environment used by the MCP config:

```bash
.venv/bin/python -m mesa_mcp.server
```

Use MCP Inspector or Antigravity to initialize the process and call
`mesa_health`. Do not run it in a terminal expecting human-readable stdout:
stdio is a JSON-RPC protocol stream.
