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

An application with dataset write access submits text or a supported file
with a source reference. Applications can use a session or supply the
tenant, workspace and dataset directly. MESA splits the text into chunks
and records the revision and its chosen chunking plan. PDF page and XLSX
sheet and row locations are stored with the revision. A retry can resume
from the saved chunks. Background workers process each chunk, and the new
revision becomes visible after all its chunks commit.
[Whole-document API](docs/api-reference.md#whole-documents-post-v4documents) ·
[File upload API](docs/api-reference.md#upload-a-file-with-post-v4documentsupload)

## How search works

MESA starts with committed evidence the caller may read. It applies tenant,
dataset and agent boundaries and any document, date or jurisdiction filters.
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

Applications integrate through the HTTP API, the Python SDK or the MCP
tools. Data producers acquire source files. MESA extracts text from supported
uploads and exposes revision status, text and citation spans so a viewer
can display the evidence. Applications handle answer generation and the user
interface. [HTTP API](docs/api-reference.md) ·
[Python SDK](docs/api-reference.md#v4-python-sdk) · [MCP tools](README_MCP.md#tools) ·
[Ecosystem responsibilities](docs/architecture-v4.md#ecosystem-and-ownership)

## What MESA does not do yet

- MESA does not generate answers with an LLM. Built-in answering is planned
  for Round 26; until then, applications write answers from the passages MESA
  returns.
- MESA has no graph database. Legal citations and article identities are
  stored and looked up in SQLite.
- LLM fact extraction is off by default and is not covered by the MVP
  documentation.
