# MESA MCP

MESA exposes its V4 API to MCP clients through the `mesa-mcp` stdio server
(`mesa_mcp/server.py` + `mesa_mcp/v4_service.py`). Every MCP client connects to
the same `mesa-mcp` binary with one command and one config snippet.

## Quick start: `mesa mcp config <client>`

1. Start a local MESA instance (creates `~/.mesa/credentials.json`):

```bash
mesa start
```

2. Print a ready-to-paste configuration snippet for your MCP client (prints to
   stdout without modifying any files; a plaintext-key warning is written to
   stderr):

```bash
mesa mcp config claude-code
mesa mcp config claude-desktop
mesa mcp config codex
mesa mcp config cursor
mesa mcp config gemini
mesa mcp config antigravity
```

(`mesa mcp-config` remains available as a backward-compatible alias that prints
the generic JSON block.)

Example JSON output (`claude-desktop`, `cursor`, `gemini`, `antigravity`;
`claude-code` also adds `"type": "stdio"`):

```json
{
  "mcpServers": {
    "mesa": {
      "command": "/absolute/path/to/mesa-mcp",
      "args": [],
      "env": {
        "MESA_BASE_URL": "http://127.0.0.1:8000",
        "MESA_API_KEY": "...",
        "MESA_TENANT_ID": "local",
        "MESA_WORKSPACE_ID": "default",
        "MESA_DATASET_ID": "default",
        "MESA_ACTOR_ID": "local-agent"
      }
    }
  }
}
```

Example TOML output (`codex`):

```toml
[mcp_servers.mesa]
command = "/absolute/path/to/mesa-mcp"
args = []

[mcp_servers.mesa.env]
MESA_BASE_URL = "http://127.0.0.1:8000"
MESA_API_KEY = "..."
MESA_TENANT_ID = "local"
MESA_WORKSPACE_ID = "default"
MESA_DATASET_ID = "default"
MESA_ACTOR_ID = "local-agent"
```

## Tools

`mesa-mcp` exposes six stdio tools using the MCP client's own tool approval and
the MESA API key's server-side grants (it creates no local control-plane
database and works from any working directory):

- `mesa_health`: checks MESA API health and V4 capability without returning
  credentials.
- `mesa_recall`: searches V4 dataset memory and wraps every returned passage as
  untrusted data. Supports sessionless search when `dataset_ids` is passed or
  `use_session: false` is set (allowing `READER`-only API keys to search
  without starting a write session), and accepts optional `document_ids` (1..64
  caller document IDs). Each recalled item includes `document_id`,
  `revision_id`, `revision_number`, `chunk_id`, `span` (`{"start", "end"}`),
  `valid_from`, and `valid_to`.
- `mesa_get_context`: builds a token-bounded context bundle through the V4
  `ContextBuilder` endpoint.
- `mesa_remember`: stores new V4 document memory.
- `mesa_improve`: creates a new revision of an existing V4 document.
- `mesa_forget`: purges a document from the catalog.

## Smoke check

`mesa-mcp` speaks MCP JSON-RPC over stdio (logs go to stderr):

```bash
MESA_BASE_URL=http://127.0.0.1:8000 \
MESA_API_KEY=... \
mesa-mcp
```

Use an MCP client or MCP Inspector to initialize the process and call
`mesa_health`.

## Optional advanced mode: HTTP Gateway

For multi-client environments that require server-side policies, operator
approvals, and an activity audit log, MESA also ships the optional
`mesa-mcp-gateway` HTTP/Streamable HTTP gateway.

Start the gateway after the MESA V4 API is ready, supplying a Fernet key in
`MESA_GATEWAY_ENCRYPTION_KEY` and pointing `MESA_GATEWAY_CONTROL_DB` at the
deployment database:

```bash
MESA_BASE_URL=http://127.0.0.1:8000 \
MESA_GATEWAY_CONTROL_DB=/absolute/path/to/mesa-data/mesa.db \
MESA_GATEWAY_ENCRYPTION_KEY=... \
mesa-mcp-gateway
```

Every `/control/mcp/*` call requires an active authenticated principal and the
server-side `ADMIN` control role, including loopback calls. See
[control API authorization](docs/api-reference.md#mcp-control-api).

### Operator approval (gateway mode)

Grant the operator the server-side `ADMIN` control role, then approve or reject
pending gateway operations by their public `operation_id`:

```bash
export MESA_STORAGE_ROOT=/absolute/path/to/mesa-data
export MESA_GATEWAY_CONTROL_DB=$MESA_STORAGE_ROOT/mesa.db
export MESA_PRINCIPAL_ID=memory-operator

mesa-v4-admin --policy-db $MESA_STORAGE_ROOT/rbac_policy.db \
  grant-control --principal $MESA_PRINCIPAL_ID
mesa operations approve op_012345 --reason "source reviewed"
mesa operations reject op_678901 --reason "insufficient provenance"
```

See [configuration](docs/configuration.md),
[control API](docs/api-reference.md#mcp-control-api), and
[getting started](docs/getting-started.md).
