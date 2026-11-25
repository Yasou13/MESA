# MESA MCP

The `mesa-mcp` stdio server gives MCP clients access to the V4 API. Ingestion,
recall and context work with models off. `mesa_ask` and `mesa_summarize` use
the chat model configured on the MESA server.

## Connect a local server

1. Start a local MESA instance. It creates `~/.mesa/credentials.json`.

```bash
mesa start
```

2. Run the command for your client and paste its stdout into your MCP
   configuration. Keep the snippet private because it contains the MESA API key.

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

For answering or summaries, run `mesa setup` and restart `mesa start`.
[Model setup](docs/configuration.md#local-model-setup)

## Tools

`mesa-mcp` exposes eight stdio tools using the MCP client's own tool approval and
the MESA API key's server-side grants (it creates no local control-plane
database and works from any working directory):

- `mesa_health`: checks MESA API health and V4 capability without returning
  credentials.
- `mesa_recall`: searches authorized passages and wraps every returned passage as
  untrusted data. Supports sessionless search when `dataset_ids` is passed or
  `use_session: false` is set (allowing keys with dataset `READER` and
  `SESSION_CREATE` for the actor agent to search without starting a write
  session), and accepts optional `document_ids` (1..64
  caller document IDs). Each recalled item includes `document_id`,
  `revision_id`, `revision_number`, `chunk_id`, `span` (`{"start", "end"}`),
  `valid_from`, and `valid_to`. PDF results carry `page`. XLSX results
  carry `sheet`, `row_start`, and `row_end`.
- `mesa_ask`: takes a `question` and returns a cited answer or `not_found: true`.
  Accepts document and date filters. The defaults are `expand: none` and
  `retrieval_mode: single`. Use a citation's `revision_id` and `span` to reopen
  the exact text. PDF and XLSX citations also include the available location.
- `mesa_summarize`: summarizes a single document revision (default: ACTIVE or
  an explicit `revision_id`), naming the articles, sections, or pages relied on.
- `mesa_get_context`: builds a token-bounded context bundle through the V4
  `ContextBuilder` endpoint.
- `mesa_remember`: stores new V4 document memory.
- `mesa_improve`: creates a new revision of an existing V4 document.
- `mesa_forget`: purges a document from the catalog.

Add a file using `mesa add <file>`, then use its document ID to restrict recall
or to request a summary. Ask and summarize surface the server's `no LLM configured`
error when there is no chat model. [Answer and summary API](docs/api-reference.md#llm-answers-and-summaries) ·
[Document text API](docs/api-reference.md#document-text-get-v4documentsdocument_idtext)

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
