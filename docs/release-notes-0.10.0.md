# MESA 0.10.0 release notes (draft)

## Upload, search, and citations

- Upload text-layer PDFs, DOCX, UYAP UDF, XLSX, UTF-8 text, and Markdown
  with `mesa add`, the Python SDK, or `POST /v4/documents/upload`.
- Use tenant, workspace, and dataset IDs to upload and search without an
  existing session. Dataset access and the agent's `SESSION_CREATE`
  permission apply. Use `document_ids` to limit what gets ranked.
- Follow ingest progress and failures with `GET /v4/documents/{document_id}`.
  Search results identify the revision, chunk, and character span. PDF and
  XLSX hits also identify the page or sheet and row range. Open the cited
  slice through the document text endpoint. [API reference](api-reference.md)

## Grounded answers and model setup

- Ask a question with `mesa ask` or `POST /v4/ask` and open the returned
  citations. When MESA cannot answer from the retrieved sources, the response
  identifies the result as `not_found`. Summarize a single document with
  `mesa summarize` or `POST /v4/documents/{document_id}/summary`. The Python
  SDK and MCP expose both operations. [Getting started](getting-started.md)
- Use `mesa setup` to configure and verify local models. Configure chat
  providers through `MESA_LLM_BASE_URL` and `MESA_LLM_MODEL`, including a
  local Ollama endpoint. Applications can use `POST /v1/chat/completions`
  for OpenAI-compatible chat. [Model configuration](configuration.md#chat-models)
- Search and grounded answers default to `expand=none` and
  `retrieval_mode=single`. Passage expansion and adaptive retrieval are
  opt-in. Expanded passages merge overlapping source spans to avoid repeating
  text in context. [Retrieval options](retrieval-and-graph.md)

## Installation, operation, and upgrades

- Run the local `mesa` CLI on Windows, macOS, or Linux. For a self-hosted
  installation, the Docker Compose profile starts with a one-time bootstrap
  key and request rate limits. [Docker installation](self-hosted-docker.md)
- Run `mesa mcp config <client>` to configure the local `mesa-mcp` stdio
  server. Replace configurations that used the Antigravity bridge or Codex
  installer. This release removes those tools, local profile commands, and
  session hooks. The optional HTTP gateway remains available.
  [MCP setup](../README_MCP.md)
- Back up the SQLite store before upgrading. The upgrade backfills citation
  offsets and preserves existing revisions. The migration checks cover stores
  written by 0.9.3, 0.9.4, and 0.9.5. For a 0.9.3 ranking comparison, carry
  forward `MESA_RRF_ASSERTION_WEIGHT=1.0` to match that release's setting.
  Removed graph settings produce a startup warning. [Upgrade procedure](release.md)
