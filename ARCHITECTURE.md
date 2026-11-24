# MESA architecture

MESA Core is a memory engine for applications that need to find evidence in
source documents. A search returns relevant passages with their source,
revision and location, so an application can show where its evidence came
from. [API reference](docs/api-reference.md)

## What MESA stores

SQLite is the source of truth. It keeps the catalog, documents, revisions,
text chunks and the ledger that records whether each change completed. When
an embedding model is enabled, LanceDB stores a derived index for finding
passages with similar meaning. MESA can rebuild that index from SQLite.
[Storage and module map](docs/architecture-v4.md#module-ownership) ·
[Vector rebuild](docs/v4-rebuild-runbook.md)

## How a document becomes searchable

An application submits text and a source reference through an authorized V4
session. MESA splits the text into chunks and records the revision and its
chosen chunking plan. A retry can resume from the saved chunks. Background
workers process each chunk, and the new revision becomes visible after all
its chunks commit. [Whole-document API](docs/api-reference.md#whole-documents-post-v4documents)

## How search works

MESA starts with the committed evidence the session may read. It applies
tenant, dataset and agent boundaries and any date or jurisdiction filters.
BM25 ranks text matches and supports prefix matching. When the authorized
corpus is exactly one dataset's active committed chunks, search uses that
dataset's persistent text index. Other scopes get a temporary index built
from eligible evidence. [Scoring and scope](docs/retrieval-and-graph.md#lanes-and-scoring)

When a compatible embedding model is available, vector search adds matches
by meaning. It filters to the authorized scope and checks ownership against
SQLite. MESA combines the available rankings. Assertion scoring has zero
weight by default. Built-in legal rules are on by default and help rank
recognized statute and article matches. Operators can enable a reranker.
[Retrieval details](docs/retrieval-and-graph.md#lanes-and-scoring) ·
[Configuration](docs/configuration.md)

## How applications use it

Applications integrate through the V4 HTTP API, the Python SDK or the MCP
tools. Data producers acquire and parse source files. Applications handle
answer generation and the user interface. [HTTP API](docs/api-reference.md) ·
[Python SDK](docs/api-reference.md#v4-python-sdk) · [MCP tools](README_MCP.md#tools) ·
[Ecosystem responsibilities](docs/architecture-v4.md#ecosystem-and-ownership)
