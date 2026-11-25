# MESA 0.9.6 release notes

MESA 0.9.6 can upload documents and serve searchable evidence without an
LLM. Desktop integrations can use the API to follow the state of an upload
and open the exact passage behind a search result.

## Upload and search

- Upload text-layer PDFs, DOCX, UYAP UDF, XLSX, UTF-8 text, and Markdown
  using `mesa add <file>`, the Python SDK's `add_file`, or
  `POST /v4/documents/upload`. File uploads share the text ingest path's
  revision, retry, and validity rules. [Upload API](api-reference.md#upload-a-file-with-post-v4documentsupload)
- Use tenant, workspace, and dataset IDs to upload and search without
  starting a session. Dataset roles and the agent's `SESSION_CREATE`
  permission govern access. Use `document_ids` to
  scope the search before ranking. [Quickstart](getting-started.md#quickstart)
- Use `GET /v4/documents/{document_id}` to check revision stages, chunk
  states, and readable ingest errors. Search and context results include
  revision and chunk IDs and character spans. A PDF result identifies
  the page, and an XLSX result identifies the sheet and row range. The text
  endpoint opens the full revision or the cited slice.
  [Document text API](api-reference.md#document-text-get-v4documentsdocument_idtext)

## CLI and MCP

`mesa mcp config <client>` prints a configuration snippet for the local
`mesa-mcp` stdio server. This release removes the Antigravity bridge and
the Codex installer, local profile commands, and session hooks. The optional
HTTP gateway remains available.
Use the corresponding client snippet when upgrading a bridge or installer
configuration. [MCP setup](../README_MCP.md)

## Upgrade

The SQLite upgrade backfills character spans for existing revisions and
adds storage for upload locations. `GET /health` now reports the running
package version. [API reference](api-reference.md)
