# Installation and first run

This guide covers getting started with MESA as a new user, from installation to querying evidence, followed by advanced operator provisioning and production runtime profiles. Production qualification remains at the release candidate stage: **NO-GO** until the [release gates](release.md) pass for the intended commit.

<a id="installation"></a>

## Installation

Install MESA with the adapters extra directly from GitHub (D34):

```bash
pip install "mesa-memory[adapters] @ git+https://github.com/Yasou13/MESA"
```

Supported natively on Linux and macOS. On Windows, use Docker or WSL.

Or install from source with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
git clone https://github.com/Yasou13/MESA.git
cd MESA
uv sync --locked --python 3.11
uv pip check
```

## Quickstart

### 1. Start the server

For standard local usage, `mesa start` automatically provisions local storage (`~/.mesa/data`), writes credentials (`~/.mesa/credentials.json`), sets model-disabled defaults, and starts the loopback server on `http://127.0.0.1:8000`:

```bash
mesa start
```

### 2. Add and search documents

In another terminal, add and search documents immediately without manual credential configuration:

```bash
mesa add document.txt --source file://document.txt
mesa search "your query"
mesa status
```

### 3. Python SDK

Use the high-level `Mesa` client to ingest documents and query evidence contexts:

```python
from mesa_client import Mesa

mesa = Mesa()
mesa.add(open("document.txt").read(), source="file://document.txt")
print(mesa.context("your query"))
```

`Mesa()` uses `MESA_API_KEY` and `MESA_BASE_URL` when they are set, otherwise the local credentials in `~/.mesa/credentials.json`.

### 4. Connect MCP clients

Connect Claude Desktop or any MCP client by generating a ready-to-paste configuration block:

```bash
mesa mcp-config
```

### 5. Internal models (optional)

Models stay off by default. Documents are stored and found through BM25 lexical search without any model running.

To enable semantic vector search and fact extraction with local Ollama, add to `~/.mesa/.env`:

```bash
MESA_MODEL_ENABLED=true
MESA_LLM_MODEL=ollama:qwen2.5:7b
MESA_EMBEDDING_MODEL=ollama:bge-m3
```

Under decision D31, local `openai_compatible` servers (e.g. vLLM started with `--port 8001`, at `http://127.0.0.1:8001/v1`, since MESA itself uses port 8000) are also permitted with `MESA_EXTERNAL_PROVIDER_ENABLED=false` on a local endpoint and require no API key. For external cloud APIs (OpenAI, Anthropic), set `MESA_EXTERNAL_PROVIDER_ENABLED=true` and supply the required API keys.

Missing extras raise clear, actionable installation messages:
- Ollama LLM, Ollama embedding, or OpenAI-compatible embedding → `pip install "mesa-memory[adapters]"`
- Local embedding (`sentence-transformers`) → `pip install "mesa-memory[ml]"`

### 6. Graph toggle

To run without the Kùzu graph database, set:

```bash
MESA_GRAPH_ENABLED=false
```

GRAPH lanes end `SKIPPED`, and turning the flag back on later backfills the graph at startup.

### 7. Docker

Run MESA in a single-user local container (D32):

```bash
docker compose -f docker-compose.v4.yml up --build -d
```

Credentials are written to `/var/lib/mesa/credentials.json` in the container volume:

```bash
docker compose -f docker-compose.v4.yml exec mesa-v4 cat /var/lib/mesa/credentials.json
docker compose -f docker-compose.v4.yml exec -T mesa-v4 mesa add - --source file://document.txt < document.txt
docker compose -f docker-compose.v4.yml exec mesa-v4 mesa search "your query"
```

No environment variables are required: models and external providers default
off. The image runs `mesa start --host 0.0.0.0` as a non-root user; Compose
publishes only `127.0.0.1:8000`. Credentials retain a loopback base URL for CLI
use inside the container. The named volume keeps data and credentials on
restart. `MESA_API_KEY` and `MESA_PRINCIPAL_ID` are optional for existing
bootstrap setups; when set, the `mesa` commands inside the container use that
key first, so add `-e MESA_API_KEY=` after `exec` to use the local credentials. Ollama defaults to `http://host.docker.internal:11434`,
which reaches the Docker host. The default image includes `adapters`; add
sentence-transformers with
`docker compose -f docker-compose.v4.yml build --build-arg MESA_EXTRAS=ml,adapters`.

### 8. Google Colab

Run MESA with GPU-accelerated Ollama in Google Colab:
[Colab Quickstart](../examples/colab_quickstart.ipynb)

<a id="using-mesa-with-your-llm"></a>

## Using MESA with your LLM

When connecting an external language model to answer questions from MESA memory, supply a system prompt that enforces untrusted data boundaries and precise source citations. `mesa_client` exports `RECOMMENDED_SYSTEM_PROMPT` for this purpose:

```python
from mesa_client import AsyncMesaV4Client, RECOMMENDED_SYSTEM_PROMPT

# System prompt for answering LLMs:
system_prompt = RECOMMENDED_SYSTEM_PROMPT

# Retrieved memory context goes into the prompt/message:
async with AsyncMesaV4Client(base_url="http://127.0.0.1:8000", api_key=api_key) as client:
    bundle = await client.get_context(session_id=session_id, query="What is the policy?")
    user_message = f"{bundle['context']}\n\nQuestion: What is the policy?"
```

The context returned in `bundle["context"]` is deterministically wrapped in `<UNTRUSTED_MEMORY_EVIDENCE>...</UNTRUSTED_MEMORY_EVIDENCE>`. The prompt instructs the model to treat everything within those tags strictly as data, never follow instructions contained within them, cite document and revision IDs for every claim, and decline to answer if the evidence is insufficient.

---

## Advanced Operator & Production Profiles

Use Python 3.11–3.13 (the CI matrix) and
[uv](https://docs.astral.sh/uv/getting-started/installation/).
Docker with Compose is needed only for containers. Run from the repository:

```bash
uv sync --locked --python 3.11
uv pip check
```

[pyproject.toml](../pyproject.toml) declares supported dependency ranges and
[uv.lock](../uv.lock) fixes the graph used by CI/Docker. CI pins its installer
version in the workflow; an installed dependency range is not a model download.
Choose `--python 3.11`, `3.12` or `3.13` if appropriate. Leaving the interpreter
unspecified can select a newer Python outside the tested matrix. Use
`uv sync --locked --python 3.11 --extra dev` when running developer tests.

SQLite (with FTS5) comes from the Python interpreter; LanceDB, PyArrow and Kùzu
are installed as core Python dependencies. No separate database server, Docker
container, manual SQL import or ML extra is needed for models-disabled startup.
Native wheels depend on Python/platform availability. The isolated installation
verified for this guide is Linux x86_64 / Python 3.11; other platforms were not
verified in this developer-experience pass.

| Extra | When needed |
|---|---|
| `dev` | Local checks and development |
| `adapters` | Provider SDK integrations |
| `ml` | Local transformer/embedding dependencies |
| `loadtest` | Explicit load-test work |
| `mcp` | Compatibility extra; MCP itself is already a core dependency |

For a provider setup that needs both SDKs and local models:

```bash
uv sync --locked --python 3.11 --extra adapters --extra ml
uv pip check
```

Install only the extras needed by the selected runtime. Model downloads,
credentials and operational provider endpoints are separate prerequisites.

### Choose topology and storage

| Use | Profile / Compose file | Storage rule |
|---|---|---|
| Canonical V4 | `combined` / `docker-compose.v4.yml` | One process owns API, consumers and stores |
| Local model-disabled startup | `combined`, models/providers false | V4 storage and BM25 search without model calls |

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

V4 provisions local credentials automatically, with models and external
providers off. Existing deployments may supply `MESA_API_KEY` and
`MESA_PRINCIPAL_ID` privately. For model-enabled operation, set the model flags
and provider configuration above:

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
models disabled supports V4 storage and BM25 search.

### Upgrade and verification

Use [migration](RUNBOOK.md#upgrade-v080) for schema/representation upgrades,
[backup and restore](RUNBOOK.md#6-backup-restore-ve-v4-rebuild) for recovery, and
[projection rebuild](v4-rebuild-runbook.md) for the offline runner. Projection
rebuild does not convert a V3 database to V4.

Run [developer checks](testing.md) for the affected contracts. Passing these
checks does not replace real-provider, migration/restore or soak release evidence.

## Local quickstart

Run from the repository after `uv sync --locked --python 3.11`. This uses one
combined process, no model loading and no external provider access. It checks
installation, credentials and readiness. This profile stores V4 records and finds them through BM25
without embedding or extraction calls. VECTOR lanes end `SKIPPED`; enable
embedding later to backfill vectors at startup with the same identity.

### Automatic local bootstrap (`mesa start`)

For standard local usage, `mesa start` provisions local storage (`~/.mesa/data`), initializes credentials (`~/.mesa/credentials.json`), sets model-disabled defaults, and starts the loopback server:

```bash
mesa start
```

In another terminal, add and search documents immediately without manually exporting credentials:

```bash
mesa add notes.txt --source file://notes.txt
mesa search "notes"
mesa status
```

### Provision before startup (manual)

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
one chunk, polls the mutation and checks source-linked evidence. It works with
the model-disabled combined environment above through BM25. For vector search
and fact extraction, set `MESA_MODEL_ENABLED=true` (or enable each model flag
separately), install the needed `ml`/`adapters` extras and provision reviewed
provider/model/dimension settings. Retain the same ACL and one-owner rules.
A concrete provider environment is in [minimal V4 provider setup](configuration.md#minimal-v4-provider-setup).
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

Developer-experience verification on Linux/Python 3.11 used an isolated,
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

<a id="python-sdk"></a>

## Python SDK

For local scripting, rapid prototyping, and agent applications, the high-level `Mesa` client provides a clean interface with automatic credential discovery and catalog session handling:

```python
from mesa_client import Mesa

mesa = Mesa()
mesa.add(open("kira.txt").read(), source="file://kira.txt")
print(mesa.context("kira ne zaman ödenir?"))
```

`Mesa()` automatically discovers connection settings and API keys from environment variables (`MESA_BASE_URL`, `MESA_API_KEY`) or from `<MESA_HOME or ~/.mesa>/credentials.json` provisioned by `mesa start`. On first use, it ensures the configured workspace and dataset exist, starts a session, and reuses it for subsequent additions, searches, and context retrieval.

<a id="using-mesa-with-your-llm"></a>

## Using MESA with your LLM

When connecting an external language model to answer questions from MESA memory,
supply a system prompt that enforces untrusted data boundaries and precise source citations.
`mesa_client` exports `RECOMMENDED_SYSTEM_PROMPT` for this purpose:

```python
from mesa_client import AsyncMesaV4Client, RECOMMENDED_SYSTEM_PROMPT

# System prompt for answering LLMs:
system_prompt = RECOMMENDED_SYSTEM_PROMPT

# Retrieved memory context goes into the prompt/message:
async with AsyncMesaV4Client(base_url="http://127.0.0.1:8000", api_key=api_key) as client:
    bundle = await client.get_context(session_id=session_id, query="What is the policy?")
    user_message = f"{bundle['context']}\n\nQuestion: What is the policy?"
```

The context returned in `bundle["context"]` is deterministically wrapped in
`<UNTRUSTED_MEMORY_EVIDENCE>...</UNTRUSTED_MEMORY_EVIDENCE>`. The prompt instructs
the model to treat everything within those tags strictly as data, never follow
instructions contained within them, cite document and revision IDs for every claim,
and decline to answer if the evidence is insufficient.

