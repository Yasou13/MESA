# MESA — Memory Engine for Structured Agents

[![MESA CI](https://github.com/Yasou13/MESA/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Yasou13/MESA/actions/workflows/ci.yml?query=branch%3Amain)
[![Release](https://img.shields.io/github/v/release/Yasou13/MESA)](https://github.com/Yasou13/MESA/releases/latest)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](https://github.com/Yasou13/MESA/blob/main/LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://github.com/Yasou13/MESA/blob/main/pyproject.toml)

MESA stores source documents and finds passages an application is authorized to read. Every search hit links to its document, revision and location. Ingestion and BM25 search work without models. Add an embedding model for semantic search or a chat model for answers with citations and document summaries.

Package version: **0.10.0**. V4 is the current API. Production qualification remains at the release candidate stage: **NO-GO** until the [release gates](https://github.com/Yasou13/MESA/blob/main/docs/release.md) pass.

## Installation

```bash
pip install mesa-memory
```

Supported natively on Linux, macOS (Apple silicon), and Windows 10/11 (Python 3.12–3.14), with no WSL or Docker required. Install this checkout with `pip install -e .` to try unreleased features.

## Start the server

```bash
mesa start
```

Starts the local server on `http://127.0.0.1:8000`, initializes storage at `~/.mesa/data`, and creates credentials at `~/.mesa/credentials.json` (mode 0600).

## Add and search documents

In another terminal, add and search documents immediately:

```bash
mesa add document.pdf
mesa search "your query"
```

## Python SDK

```python
from mesa_client import Mesa

with Mesa() as mesa:
    document = mesa.add_file("document.pdf", wait=True)
    print(mesa.search("your query", document_ids=[document["document_id"]]))
```

`Mesa()` uses `MESA_API_KEY` and `MESA_BASE_URL` when they are set, otherwise the local credentials in `~/.mesa/credentials.json`.

See [Law versions](docs/getting-started.md#law-versions) to store revisions of a law and search the wording in force on a date.

## Optional models and answers

Run `mesa setup` to choose embeddings and a chat model. Restart `mesa start`
to apply the saved settings. With a chat model configured, run
`mesa ask "your question"` for a cited answer or `mesa summarize <document-id>`
for a single-document summary. Applications can call `POST /v1/chat/completions`
with a MESA API key and model `mesa`.

The SDK also exports OpenAI-format `search`, `read_text` and `list_documents`
tools for applications that handle their own model calls.
[Model configuration](docs/configuration.md) ·
[Answering and tools](docs/api-reference.md#llm-answers-and-summaries)

## Connect MCP clients

Run `mesa mcp config codex` or select a supported client to print its
configuration. MCP supports agent memory insert and recall, plus model-backed
`mesa_ask` and `mesa_summarize`.
[MCP guide](README_MCP.md)

Canonical V4 fact extraction is owned by `FactExtractionService` and can be
enabled separately with `MESA_EXTRACTION_ENABLED=true` and a configured chat
model.

## Docker

Run MESA on your own server with one command (non-root container, persistent volume, rate limits enabled by default, bootstrap API key printed once on first boot):

```bash
docker compose -f docker-compose.v4.yml up --build -d
```

Read the one-time bootstrap key from first-start logs (`docker compose -f docker-compose.v4.yml logs mesa-v4`) or `/var/lib/mesa/credentials.json` in the persistent volume:

```bash
docker compose -f docker-compose.v4.yml exec mesa-v4 cat /var/lib/mesa/credentials.json
docker compose -f docker-compose.v4.yml exec -T mesa-v4 mesa add - --source file://document.txt < document.txt
docker compose -f docker-compose.v4.yml exec mesa-v4 mesa search "your query"
```

No variables are required; models and external providers default off. See [Install on your own server](docs/self-hosted-docker.md) for hardware minimums, optional embedding/LLM environment settings, TLS reverse proxy setup, backups, and upgrades. If you set `MESA_API_KEY` and `MESA_PRINCIPAL_ID` for existing HTTP clients, the server accepts that key too, and the `mesa` commands inside the container use it first; add `-e MESA_API_KEY=` after `exec` to use the local credentials instead. Build with `--build-arg MESA_EXTRAS=ml` to add local sentence-transformers. Ollama defaults to `http://host.docker.internal:11434` (the Docker host).

## Google Colab

Run MESA with GPU-accelerated Ollama in Colab:
[Colab Quickstart](https://github.com/Yasou13/MESA/blob/main/examples/colab_quickstart.ipynb)

## Development

```bash
uv sync --locked --python 3.11
```

See [testing](https://github.com/Yasou13/MESA/blob/main/docs/testing.md) for local tests and test markers.

## Documentation

- [Getting Started Guide](docs/getting-started.md)
- [Install on Your Own Server (Docker)](docs/self-hosted-docker.md)
- [Configuration Reference](docs/configuration.md)
- [API Reference](docs/api-reference.md)
- [MCP Integration Guide](README_MCP.md)
- [Architecture](ARCHITECTURE.md)
- [Documentation Index](docs/README.md)

## License

MESA is licensed under the [Apache License, Version 2.0](https://github.com/Yasou13/MESA/blob/main/LICENSE). See [NOTICE](https://github.com/Yasou13/MESA/blob/main/NOTICE) for copyright information.
