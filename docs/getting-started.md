# Installation and first run

This guide owns dependency installation, deployment choices and the executable
local quickstart. Configure providers in [configuration](configuration.md) and
use [operations](RUNBOOK.md) for recovery. V4 production qualification remains
release candidate / **NO-GO** until the [release gates](release.md) pass.

<a id="installation"></a>

## Installation and deployment

### Install the locked environment

Use Python 3.10–3.13 (the CI matrix) and
[uv](https://docs.astral.sh/uv/getting-started/installation/).
Docker with Compose is needed only for containers. Run from the repository:

```bash
uv sync --locked --python 3.10
uv pip check
```

[pyproject.toml](../pyproject.toml) declares supported dependency ranges and
[uv.lock](../uv.lock) fixes the graph used by CI/Docker. CI pins its installer
version in the workflow; an installed dependency range is not a model download.
Choose `--python 3.11`, `3.12` or `3.13` if appropriate. Leaving the interpreter
unspecified can select a newer Python outside the tested matrix. Use
`uv sync --locked --python 3.10 --extra dev` when running developer tests.

SQLite (with FTS5) comes from the Python interpreter; LanceDB, PyArrow and Kùzu
are installed as core Python dependencies. No separate database server, Docker
container, manual SQL import or ML extra is needed for models-disabled startup.
Native wheels depend on Python/platform availability. The isolated installation
verified for this guide is Linux x86_64 / Python 3.10; other platforms were not
verified in this developer-experience pass.

| Extra | When needed |
|---|---|
| `dev` | Local checks and development |
| `adapters` | Provider SDK integrations |
| `ml` | Local transformer/embedding or legacy REBEL dependencies |
| `loadtest` | Explicit load-test work |
| `mcp` | Compatibility extra; MCP itself is already a core dependency |

For a provider setup that needs both SDKs and local models:

```bash
uv sync --locked --python 3.10 --extra adapters --extra ml
uv pip check
```

Install only the extras needed by the selected runtime. Model downloads,
credentials and operational provider endpoints are separate prerequisites.

### Choose topology and storage

| Use | Profile / Compose file | Storage rule |
|---|---|---|
| Canonical V4 | `combined` / `docker-compose.v4.yml` | One process owns API, consumers and stores |
| Local model-disabled startup | `combined`, models/providers false | Health/configuration checks; no ingestion demonstration |

Choose a dedicated absolute writable storage root. Do not use the repository,
home directory or filesystem root. V3 and V4 must not write the same root.
Fresh combined startup initializes the databases automatically; keep SQLite,
LanceDB, Kùzu and the policy database together under that root. The runtime has
one owner: stop it before running offline admin or migration commands.
The runtime does not load `.env` implicitly; the explicit dotenv precedence is
explained in [configuration](configuration.md).

### Native V4 startup

Before startup:

1. Select a new or compatible V4 storage root and provision credentials/ACL using
   [runbook section 2](RUNBOOK.md#credential-bootstrap).
2. Install the selected extraction and embedding dependencies; configure their
   provider/model identity. External `text-embedding-3-small` requires dimension
   1536; the local Magibu default is 768. Equal dimensions alone are insufficient.
3. Select validation mode. Mode 0 has no LLM validator but still needs canonical
   extraction and embeddings; mode 2 requires distinct A/B validator identities.
4. Supply the issued API key and provider secrets privately to the process.

Example profile after those prerequisites (the storage path is an operator
example, not an automatically provisioned directory):

```bash
export MESA_STORAGE_ROOT=/srv/mesa/v4-data
export MESA_RUNTIME_PROFILE=combined
export MESA_LOAD_DOTENV=false
export MESA_MODEL_ENABLED=true
export MESA_TIER3_MODE=0
export MESA_PRINCIPAL_ID=service-api
# Set MESA_EXTERNAL_PROVIDER_ENABLED to match the reviewed providers.
.venv/bin/python -m mesa_memory.runtime_entrypoint
```

The launcher binds `0.0.0.0`; use the explicit loopback command from the
quickstart for local use. Readiness is `/health/init`; `/health` requires a key.
The [V4 example](../examples/v4_quickstart.py) creates the catalog and session,
inserts a source, polls its mutation and checks evidence. Its `docs-*` identities
require the grants shown in the quickstart.

### Compose

Provide `MESA_API_KEY` and `MESA_PRINCIPAL_ID` privately before either topology.
V4 also requires explicit `MESA_MODEL_ENABLED` and
`MESA_EXTERNAL_PROVIDER_ENABLED` values, plus the provider configuration above:

```bash
docker compose -f docker-compose.v4.yml config --quiet
docker compose -f docker-compose.v4.yml up --build -d
docker compose -f docker-compose.v4.yml ps
```

For V3 lexical compatibility:

```bash
docker compose config --quiet
docker compose up --build -d
```

Compose publishes the API on loopback. V4 mounts its named volume under
`/var/lib/mesa`, with storage at `/var/lib/mesa/data`. Host provisioning against
`/srv/mesa/v4-data` does not provision that named volume: run admin commands
against the actual deployment's policy database, with the same credential and
ACL scope. Do not add a second writer to the volume. Rendering Compose with
models disabled verifies topology only.

### Upgrade and verification

Use [migration](RUNBOOK.md#upgrade-v080) for schema/representation upgrades,
[backup and restore](RUNBOOK.md#6-backup-restore-ve-v4-rebuild) for recovery, and
[projection rebuild](v4-rebuild-runbook.md) for the offline runner. Projection
rebuild does not convert a V3 database to V4.

Run [developer checks](testing.md) for the affected contracts. Passing these
checks does not replace real-provider, migration/restore or soak release evidence.

## Local quickstart

Run from the repository after `uv sync --locked --python 3.10`. This uses one
combined process, no model loading and no external provider access. It checks
installation, credentials and readiness. Canonical V4 insert is unavailable
in this profile: it returns `503 canonical_processing_unavailable`. V4 needs a
model-enabled combined consumer with reviewed extraction/embedding dependencies.
V3 raw-memory processing also calls embeddings, so it cannot complete the
insert/poll/search journey in this models-disabled profile. Use the V4 provider
setup below for your first source-backed insertion and query.

### Provision before startup

Choose a disposable location on a disk with sufficient free space. The example
uses a unique child under `~/mesa-doc-demos`; do not reuse user storage.
`/tmp` may be memory-backed on some hosts.

```bash
mkdir -p "$HOME/mesa-doc-demos"
export MESA_STORAGE_ROOT="$(mktemp -d "$HOME/mesa-doc-demos/run-XXXXXX")"
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
only the new sample policy database. Start the following server in this terminal after provisioning. For client
commands, use another terminal with the same exported settings and key supplied
privately; a newly opened terminal does not automatically inherit these exports:

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

With `MESA_API_KEY` already exported, the same checks are available through:

```bash
MESA_BASE_URL=http://127.0.0.1:18080 make health
```

This checks `/health/init` and authenticated `/health` with a five-second timeout
per request. It exits nonzero for missing/invalid credentials, connection errors,
invalid responses or unhealthy status. It does not open local databases, print
credentials or follow redirects. The default base URL is `http://127.0.0.1:8000`.

### V4 canonical example (provider prerequisites)

`examples/v4_quickstart.py` supplies required title/source/catalog IDs, freezes
one chunk, polls the mutation and checks source-linked evidence. It requires
`MESA_MODEL_ENABLED=true` and operational configured models/providers. Do not run
it with the model-disabled environment above. Install the needed `ml`/`adapters`
extras, provision reviewed provider/model/dimension settings on a **new root**,
retain the same ACL and one-owner rules, and launch on loopback. A concrete
environment example is in [minimal V4 provider setup](configuration.md#minimal-v4-provider-setup).
Follow [configuration](configuration.md)
and [installation](getting-started.md#installation). The exact command after those prerequisites is:

```bash
.venv/bin/python -m examples.v4_quickstart
```

The example prints assertion IDs and source/dataset/chunk references after
checking `COMMITTED` and provenance, then ends the session. The client's `with`
block closes HTTP resources; ending a session is a separate server operation.
`wait_until_committed()` can return failed terminal states; always check
`state == "COMMITTED"`. A timeout does not cancel the mutation: inspect
`client.status(mutation_id)` before deciding what to retry.

Developer-experience verification on Linux/Python 3.10 used an isolated,
non-editable locked Core install, models-disabled startup/health checks,
and this V4 example with explicitly injected deterministic extraction/embedding.
SQLite, LanceDB, Kùzu and background processing were real. The test limited Kùzu
to a 64 MiB buffer pool, 256 MiB mapping and one query thread to fit the host's
available RAM; this is test sizing, not a production capacity result. No real
model was called. Real-provider V4 and Docker were **not verified** in this pass.

### Restart and cleanup

Stop this example's server with Ctrl-C and restart it with the same environment.
Query the saved session/mutation while the session is active. Do not run two
writers. End the session only after retrieval. Reads after end depend on retained
permissions; new writes require an active session. `end_session` can return
`status=pending` with a `finalization_id`: finalization is durable background
work, not an HTTP client close operation.

After stopping the server, retain or remove only this newly created sample root
using your file manager. No generic database/volume removal command is provided.
If the port is occupied choose another loopback port and set `MESA_BASE_URL`
for the example accordingly.
