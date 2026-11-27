# Installation and first run

Start with document ingestion and BM25 search. You can add embeddings for
semantic search and a chat model for answers and summaries whenever you need
them. The V4 runtime is a release candidate. Production qualification remains
**NO-GO** until the release gates pass. [Release policy](release.md)

## Installation

On Linux, macOS (Apple silicon), or Windows 10/11, use a Python 3.12–3.14
environment (Python 3.11+ on Linux) and run `pip install mesa-memory` for the
published package — no WSL or Docker required. The model setup and built-in
answering examples below use this checkout. From the checkout, install it in
your environment with `pip install -e .`, or run `uv sync --locked` and activate
`.venv`.

**Minimum hardware (measured under 8-client load):** 2 vCPU and **4 GB RAM**
for models-off BM25 search up to 100k chunks (measured peak server RSS: 1.2 GiB
at 25k chunks, 1.9 GiB at 100k chunks), **6 GB RAM** for 100k chunks with an
external 1024-dim embedding server such as Ollama/vLLM `BAAI/bge-m3` (measured
peak server RSS: 1.8 GiB at 25k chunks, 3.2 GiB at 100k chunks), or **8 GB RAM**
(16 GB recommended at 100k chunks) when running `local:BAAI/bge-m3` in-process
with `mesa-memory[ml]` (~2.5 GiB additional CPU model RAM). SSD storage of at
least 5 GB per 100k chunks is recommended.

## Quickstart

Run these examples in a fresh Bash shell without existing MESA overrides.
Keep using that shell throughout. The first block creates a fresh
MESA home and starts a loopback server using port 8000 by default. If that
port is in use, set `MESA_PORT` to a free port first. The server writes its
local API credentials to `$MESA_HOME/credentials.json`.

```bash
export MESA_HOME="$(mktemp -d)"
export MESA_PORT="${MESA_PORT:-8000}"
export MESA_MODEL_ENABLED=false
export MESA_EXTERNAL_PROVIDER_ENABLED=false
unset MESA_STORAGE_ROOT MESA_API_KEY MESA_BASE_URL

stop_mesa() {
  if [ -n "${mesa_pid:-}" ]; then
    kill "$mesa_pid" 2>/dev/null || true
    wait "$mesa_pid" 2>/dev/null || true
    unset mesa_pid
  fi
}

start_mesa() {
  mesa start >"$MESA_HOME/server.log" 2>&1 &
  mesa_pid=$!
  trap stop_mesa EXIT
  curl --fail --silent --retry 20 --retry-connrefused \
    --retry-delay 1 --max-time 3 "http://127.0.0.1:$MESA_PORT/health/init" >/dev/null || {
    printf 'MESA did not become ready. See %s/server.log\n' "$MESA_HOME" >&2
    return 1
  }
}

start_mesa
printf '%s\n' 'Section 1. The reading room opens at nine.' >"$MESA_HOME/guide.txt"
mesa add "$MESA_HOME/guide.txt"
mesa search "reading room opens"
mesa status
```

`mesa add` waits for the document revision to become searchable. The runtime
stores V4 records and finds them through BM25 with models off. It accepts
PDF, DOCX, UDF, XLSX, TXT and Markdown uploads, and text on stdin with a source
reference. [File upload API](api-reference.md#upload-a-file-with-post-v4documentsupload)

## Python SDK

`Mesa()` reads the local credentials created by `mesa start`. Use
`document_ids` to restrict a search to specific documents and
`get_document_text` to reopen an exact cited span. Offsets are zero-based,
with an exclusive end.

```bash
python - <<'PY'
from mesa_client import Mesa

with Mesa() as mesa:
    doc = mesa.add(
        "Section 2. Borrowed books are due in fourteen days.",
        source="demo://library/loan-rules",
        title="Loan rules",
        wait=True,
    )
    hits = mesa.search("borrowed books are due", document_ids=[doc["document_id"]])
    citation = hits[0]
    span = citation["span"]
    source = mesa.get_document_text(
        citation["document_id"],
        revision_id=citation["revision_id"],
        start=span["start"],
        end=span["end"],
    )
    print(source["text"])
PY
```

See [scope and SDK methods](api-reference.md#v4-python-sdk) for explicit
tenant, workspace, dataset and session calls.

### Law versions

Give each version the same source reference. MESA orders revisions by
`metadata.valid_from` and uses `valid_at` to select the wording in force on
the requested date.

```bash
python - <<'PY'
from mesa_client import Mesa

with Mesa() as mesa:
    source = "demo://library/article-1"
    first = mesa.add(
        "Article 1. The reading room closes at five.",
        source=source,
        metadata={"valid_from": "2024-01-01T00:00:00Z"},
        wait=True,
    )
    mesa.add(
        "Article 1. The reading room closes at six.",
        source=source,
        metadata={"valid_from": "2025-01-01T00:00:00Z"},
        wait=True,
    )
    hits = mesa.search(
        "reading room closes",
        document_ids=[first["document_id"]],
        valid_at="2024-06-01T00:00:00Z",
    )
    print(hits[0]["evidence_span"])
PY
```

See [versioned documents](api-reference.md#versioned-documents) for supported
validity intervals and revision behavior.

## Set up a model and ask a question

Run `mesa setup` for the interactive prompts. The example below uses the
non-interactive form and keeps embeddings off. Before running it, set
`CHAT_BASE_URL` to your OpenAI-compatible model server's API base URL, such
as `http://127.0.0.1:11434/v1`, and `CHAT_MODEL` to a model it serves. Set
`MESA_LLM_API_KEY` privately if the endpoint requires a key. Setup verifies
the selected model and writes the configuration to `$MESA_HOME/.env`. The
server reads it on the next start.

```bash
stop_mesa
mesa setup --non-interactive --embedding none --llm openai-compatible \
  --llm-base-url "${CHAT_BASE_URL:?Set CHAT_BASE_URL to the model API base URL}" \
  --llm-model "${CHAT_MODEL:?Set CHAT_MODEL to a model served by that endpoint}"
start_mesa

mesa ask "When does the reading room open?"
mesa ask "What is the library's annual budget?"
```

A grounded answer includes citations. When the retrieved evidence cannot
answer the question, MESA returns `not_found`. The library documents in this
example contain opening hours and loan rules.

## Summarize a document

Pass the document ID reported by `mesa add`. Here, adding the same file again
returns the existing document ID.

```bash
document_id="$(mesa add "$MESA_HOME/guide.txt" --json | python -c 'import json, sys; print(json.load(sys.stdin)["document_id"])')"
mesa summarize "$document_id"
```

Use `--revision-id` to summarize a specific revision. The default is the
active revision. [Summary API](api-reference.md#summarize-a-document)

## Use the chat endpoint

Point an OpenAI-compatible client at the MESA server's `/v1` base URL with
the **MESA API key**. The model value `mesa` selects the server's default
dataset scope. The configured chat model supplies the answer.

```bash
python - <<'PY'
import json
import os
from pathlib import Path

from openai import OpenAI

credentials = json.loads((Path(os.environ["MESA_HOME"]) / "credentials.json").read_text())
with OpenAI(base_url=credentials["base_url"] + "/v1", api_key=credentials["api_key"]) as client:
    reply = client.chat.completions.create(
        model="mesa",
        messages=[{"role": "user", "content": "When does the reading room open?"}],
    )
    print(reply.choices[0].message.content)
    print(reply.model_dump()["mesa_citations"])
PY
```

[Chat request and response fields](api-reference.md#openai-compatible-chat)

## Give your application LLM retrieval tools

Pass `get_openai_tools()` to your application's own tool-calling model.
Execute an approved tool call with the SDK and return its result to your
application. The tools work with the no-model MESA server as well.

```bash
python - <<'PY'
from mesa_client import Mesa, execute_tool_call, get_openai_tools

tools = get_openai_tools()
print([tool["function"]["name"] for tool in tools])

with Mesa() as mesa:
    result = execute_tool_call(
        mesa,
        {"function": {"name": "search", "arguments": '{"query":"reading room opens"}'}},
    )
    print(result["results"][0]["evidence_span"])
PY
```

[MESA tool schemas](api-reference.md#openai-format-retrieval-tools)

## Connect an MCP client

Generate the configuration snippet for Codex in an owner-only file.

```bash
(umask 077; mesa mcp config codex > "$MESA_HOME/mesa-mcp.toml")
```

Paste the snippet into your MCP configuration and keep the embedded MESA
API key private. Recall works without an LLM. `mesa_ask` and
`mesa_summarize` use the server's configured chat model. [MCP guide](../README_MCP.md)

To finish the example, run `stop_mesa` in the same shell. The demo store and
server log remain in `$MESA_HOME`.

## Deployment and operations

The local `mesa start` command provisions credentials for you. For a direct
combined runtime, use a dedicated writable storage root and follow credential
bootstrap for an existing key or for `MESA_API_KEY` and `MESA_PRINCIPAL_ID`.
The authenticated health check and the repository's
`python -m examples.v4_quickstart` are covered in the operational guides.
[Credential bootstrap](RUNBOOK.md#credential-bootstrap) ·
[Configuration precedence](configuration.md#startup-and-precedence) ·
[Scoped V4 example](../examples/v4_quickstart.py)

Run `docker compose up --build -d` with
`COMPOSE_FILE=docker-compose.v4.yml` from the checkout. The supplied service
binds the published port on loopback, provisions local credentials and keeps
`MESA_MODEL_ENABLED=false` and `MESA_EXTERNAL_PROVIDER_ENABLED=false` by
default. Use `MESA_MODEL_ENABLED=true` to enable embeddings through the
combined model flag, or set `MESA_EMBEDDING_ENABLED=true` directly. Allocate
at least **4 GB RAM** for models-off containers up to 100k chunks, **6 GB RAM**
with external 1024-dim embeddings, or **8 GB RAM** (16 GB recommended at 100k)
for in-process `local:BAAI/bge-m3` (`MESA_EXTRAS=ml`).
[Install on your own server (Docker)](self-hosted-docker.md) ·
[Docker configuration](../docker-compose.v4.yml) ·
[Model configuration](configuration.md)

[Backup and migration](RUNBOOK.md) ·
[Offline projection rebuild](v4-rebuild-runbook.md) ·
[Developer checks](testing.md) ·
[Colab notebook](../examples/colab_quickstart.ipynb)
