# MESA — Memory Engine for Structured Agents

[![MESA CI](https://github.com/Yasou13/MESA/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Yasou13/MESA/actions/workflows/ci.yml?query=branch%3Amain)
[![Release](https://img.shields.io/github/v/release/Yasou13/MESA)](https://github.com/Yasou13/MESA/releases/latest)
[![License: MIT](https://img.shields.io/github/license/Yasou13/MESA)](https://github.com/Yasou13/MESA/blob/main/LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://github.com/Yasou13/MESA/blob/main/pyproject.toml)

MESA is an LLM-independent, source-linked memory engine for AI applications. It stores documents with exact provenance (document, revision, passage) and returns verifiable, scoped evidence over HTTP, Python SDK, and MCP. MESA returns evidence; it never writes the final answer or hallucinates sources.

Package version: **0.9.0**. V4 is the current API. Production qualification remains at the release candidate stage: **NO-GO** until the [release gates](https://github.com/Yasou13/MESA/blob/main/docs/release.md) pass.

## Installation

```bash
pip install "mesa-memory[adapters] @ git+https://github.com/Yasou13/MESA"
```

Supported on Linux and macOS natively. On Windows, run via Docker or WSL.

## Start the server

```bash
mesa start
```

Starts the local server on `http://127.0.0.1:8000`, initializes storage at `~/.mesa/data`, and creates credentials at `~/.mesa/credentials.json` (mode 0600).

## Add and search documents

In another terminal, add and search documents immediately:

```bash
mesa add document.txt --source file://document.txt
mesa search "your query"
```

## Python SDK

```python
from mesa_client import Mesa

mesa = Mesa()
mesa.add(open("document.txt").read(), source="file://document.txt")
print(mesa.context("your query"))
```

`Mesa()` discovers local credentials automatically from `~/.mesa/credentials.json` or `MESA_API_KEY`.

## Connect MCP clients

Generate a ready-to-paste configuration block for Claude Desktop or any MCP client:

```bash
mesa mcp-config
```

## Internal models (optional)

Models stay off by default. Documents are stored and found through BM25 lexical search without any model running. `FactExtractionService` owns canonical V4 extraction when models are enabled.

To enable semantic vector search and fact extraction with local Ollama, add to `~/.mesa/.env`:

```bash
MESA_MODEL_ENABLED=true
MESA_LLM_MODEL=ollama:qwen2.5:7b
MESA_EMBEDDING_MODEL=ollama:bge-m3
```

Local `openai_compatible` servers (such as vLLM on `http://127.0.0.1:8000/v1`) are also allowed with `MESA_EXTERNAL_PROVIDER_ENABLED=false` on loopback and require no API key (D31). For cloud model providers (OpenAI, Anthropic), set `MESA_EXTERNAL_PROVIDER_ENABLED=true` and provide the required API keys.

## Graph toggle

To run without the Kùzu graph database, set:

```bash
MESA_GRAPH_ENABLED=false
```

GRAPH lanes end `SKIPPED`, and turning the flag back on later backfills the graph at startup.

## Docker

Run MESA in a single-user local container (D32):

```bash
docker compose -f docker-compose.v4.yml up -d
```

Credentials are written to `/var/lib/mesa/credentials.json` in the container volume:

```bash
docker compose -f docker-compose.v4.yml exec mesa cat /var/lib/mesa/credentials.json
docker compose -f docker-compose.v4.yml exec mesa mesa search "your query"
```

## Google Colab

Run MESA with GPU-accelerated Ollama in Colab:
[Colab Quickstart](https://github.com/Yasou13/MESA/blob/main/examples/colab_quickstart.ipynb)

## Development

```bash
uv sync --locked --python 3.11
```

See [testing](https://github.com/Yasou13/MESA/blob/main/docs/testing.md) for local tests, and [docs/testing.md](https://github.com/Yasou13/MESA/blob/main/docs/testing.md) for test markers.

## Documentation

- [Getting Started Guide](https://github.com/Yasou13/MESA/blob/main/docs/getting-started.md)
- [Configuration Reference](https://github.com/Yasou13/MESA/blob/main/docs/configuration.md)
- [API Reference](https://github.com/Yasou13/MESA/blob/main/docs/api-reference.md)
- [MCP Integration Guide](https://github.com/Yasou13/MESA/blob/main/README_MCP.md)
- [Architecture](https://github.com/Yasou13/MESA/blob/main/ARCHITECTURE.md)
- [Documentation Index](https://github.com/Yasou13/MESA/blob/main/docs/README.md)

## License

[MIT](https://github.com/Yasou13/MESA/blob/main/LICENSE).
