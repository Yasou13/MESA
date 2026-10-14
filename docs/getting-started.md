# Local quickstarts and V4 provider requirements

Run from the repository after `uv sync --locked --extra dev`. This uses one
combined process, no model loading and no external provider access. It exercises
V3 durable raw-memory and lexical retrieval. Canonical V4 insert is unavailable
in this profile: it returns `503 canonical_processing_unavailable`. V4 needs a
model-enabled combined consumer with reviewed extraction/embedding dependencies.

## Provision before startup

```bash
export MESA_STORAGE_ROOT="$(mktemp -d /tmp/mesa-doc-demo-XXXXXX)"
export MESA_RUNTIME_PROFILE=combined
export MESA_LOAD_DOTENV=false
export MESA_MODEL_ENABLED=false
export MESA_EXTERNAL_PROVIDER_ENABLED=false
export MESA_TIER3_MODE=0
export MESA_PRINCIPAL_ID=docs-principal
export MESA_PORT=18080
export MESA_API_KEY="$(.venv/bin/mesa-v4-admin issue-key --principal docs-principal)"
.venv/bin/mesa-v4-admin grant-role --principal docs-principal --tenant docs-tenant --role OWNER
.venv/bin/mesa-v4-admin grant-agent --principal docs-principal --agent docs-agent --permission SESSION_CREATE
```

Keep the issued key out of logs and shared terminal output. The commands write
only the new sample policy database. In another terminal inherit this same
environment and start one loopback server:

```bash
.venv/bin/python -m uvicorn mesa_memory.api.server:app --host 127.0.0.1 --port 18080
```

Wait for readiness:

```bash
curl --fail http://127.0.0.1:18080/health/init
curl --fail -H "X-API-Key: $MESA_API_KEY" http://127.0.0.1:18080/health
```

Readiness returns `{"status":"ready"}` on success; health reports store/queue
state. The normal launcher binds all interfaces internally; this explicit
uvicorn command limits the local example to loopback.

## Insert, poll and search

With the same environment:

```bash
.venv/bin/python -m examples.v3_quickstart
```

The V3 example starts a server-created `docs-agent` session and inserts
“The demo archive opens at nine.” It polls raw-log status until `processed`,
rejects `failed`/`rejected`, and searches only that authorized session. Expected
summary: `PROCESSED; scoped V3 raw memory found`. No LLM extraction, embedding
quality or V4 tenant guarantee is inferred from this demo.

## V4 canonical example (provider prerequisites)

`examples/v4_quickstart.py` supplies required title/source/catalog IDs, freezes
one chunk, polls the mutation and checks source-linked evidence. It requires
`MESA_MODEL_ENABLED=true` and operational configured models/providers. Do not run
it with the model-disabled environment above. Install the needed `ml`/`adapters`
extras, provision reviewed provider/model/dimension settings, retain the same
ACL and one-owner rules, and launch on loopback. Follow [configuration](configuration.md)
and [installation](installation.md). The exact command after those prerequisites is:

```bash
.venv/bin/python -m examples.v4_quickstart
```

This provider-enabled standalone example was **NOT EXECUTED** in this
documentation audit. V4 API/storage behavior is additionally exercised through
local contract tests with explicitly injected deterministic providers. Those
fixtures are not real-model evidence. `wait_until_committed()` can return failed
terminal states; always check `state == "COMMITTED"`.

## Restart and cleanup

Stop this example's server with Ctrl-C and restart it with the same environment.
Query the saved session/mutation while the session is active. Do not run two
writers. End the session only after retrieval; active-session search after end
is rejected.

After stopping the server, retain or remove only this newly created sample root
using your file manager. No generic database/volume removal command is provided.
If the port is occupied choose another loopback port and set `MESA_BASE_URL`
for the example accordingly.
