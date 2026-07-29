# MESA MCP

MESA MCP exposes MESA to local MCP clients over stdio. Antigravity uses a
small stdio bridge which connects to an independently supervised HTTP gateway;
the bridge never imports or accesses storage backends directly.

## Prerequisites

Start the MESA V4 API and then the MCP gateway. The gateway owns credentials,
policy, approvals, operation state and the connection pool; the bridge owns
only its local encrypted write spool.

```bash
MESA_RUNTIME_PROFILE=combined MESA_STORAGE_ROOT=/absolute/path/to/mesa-data \
MESA_API_KEY=your-local-api-key MESA_PRINCIPAL_ID=local-mcp \
.venv/bin/python -m mesa_memory.runtime_entrypoint

MESA_BASE_URL=http://127.0.0.1:8000 \
MESA_GATEWAY_ENCRYPTION_KEY=fernet-key \
MESA_GATEWAY_CONTROL_DB=/absolute/path/to/mesa-data/mesa.db \
.venv/bin/mesa-mcp-gateway

## Codex direct HTTP integration

Codex connects directly to the durable gateway at `http://127.0.0.1:8765/mcp`.
The local, gitignored `.codex/config.toml` contains no credential. Create or
update the trusted project configuration through the local CLI:

```bash
.venv/bin/mesa codex install --workspace /absolute/path/to/project \
  --control-db /absolute/path/to/mesa.db
.venv/bin/mesa codex doctor --workspace /absolute/path/to/project
.venv/bin/mesa codex run --workspace /absolute/path/to/project
```

`install` creates or updates a workspace-fingerprint binding, merges only the
MESA-managed config/hook entries and writes a timestamped backup before every
change. Its token and credential ID live only in
`$XDG_CONFIG_HOME/mesa/codex/<fingerprint>.env` (directory 0700, file 0600).
`run` is the supported way to give Codex CLI that environment; it never writes
the token into the repository or global desktop environment.

Use `mesa codex profile get|set` to change the binding’s hook settings and
context limits. Limits never exceed eight memories and 2,500 tokens. Use
`mesa codex rotate-token` while the gateway is running: it proves the new
credential with an MCP initialize call before replacing the local env file and
revoking the old credential. `disable` revokes the active credential and
removes the local secret; `uninstall` also removes only the MESA-managed
project config and hook entries.

Each Codex credential can access only its recorded workspace binding. The
dashboard shows safe credential summaries and can revoke a credential, but it
never issues or displays plaintext tokens. Codex hooks load bounded project
memory on session start and compaction; they never write transcript content to
memory and do not prevent a session from starting when MESA is unavailable.
```

Do not commit credentials. The systemd service and protected local environment
must both be mode `0600`.

For systemd, copy `deploy/systemd/mcp-gateway.env.example` to
`/etc/mesa/mcp-gateway.env`, set mode `0600`, and use the supplied
`mesa-mcp-gateway.service`. The unit intentionally obtains all long-lived
gateway secrets from `EnvironmentFile`; neither Codex project config nor the
dashboard contains them.

The dashboard may manage the local control plane from a loopback browser
connection. Remote `/control/mcp/*` callers require the normal `X-API-Key`;
deploy the dashboard behind an authenticated reverse proxy when it is not
local-only.

## Antigravity

Install Antigravity from the trusted repository root:

```bash
.venv/bin/mesa antigravity install --workspace /absolute/path/to/project \
  --control-db /absolute/path/to/mesa.db
.venv/bin/mesa antigravity doctor --workspace /absolute/path/to/project
```

The install command writes a MESA-managed entry to the local, gitignored
`.agents/mcp_config.json` without a token. The bridge reads its binding-scoped
credential only from the protected XDG config file. This is a breaking migration: legacy shared
`MESA_GATEWAY_TOKEN` bridge configurations fail closed until installed again.
Every write tool requires an explicit `idempotency_key`; stdout remains reserved
for MCP JSON-RPC and logs go to stderr.
Use `mesa antigravity profile get|set` for the binding’s token-bounded context
profile, and `rotate-token`, `disable` or `uninstall` for its credential
lifecycle.

## Tools

- `mesa_health` reports bridge, gateway, MESA and spool state, including
  `DEGRADED` operation.
- `mesa_recall` performs scoped V4 search or token-bounded context retrieval.
- `mesa_remember`, `mesa_improve` and `mesa_forget` return durable operation
  IDs; default policy returns `PENDING_APPROVAL` rather than holding stdio open.
- `mesa_get_operation_status` polls approval and mutation progress.

The bridge fails closed when the client/workspace binding is absent. A repeated
write with the same idempotency key returns the original operation. While the
gateway is briefly unavailable, remember and improve are encrypted and queued
under `$XDG_STATE_HOME/mesa`; forget is never queued.

## Smoke check

With the same protected environment used by Antigravity:

```bash
.venv/bin/python -m mesa_mcp.antigravity_bridge
```

Use MCP Inspector or Antigravity to initialize the process and call
`mesa_health`. Do not run it in a terminal expecting human-readable stdout:
stdio is a JSON-RPC protocol stream. The direct `mesa_mcp.server` remains for
legacy V3-compatible integrations only.
