# Security policy

## Supported versions

Security fixes are provided for the latest release on the `main` branch. Older
tags and forks are not supported unless a release note explicitly says so.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Report it privately
to the repository maintainers using GitHub's private vulnerability reporting
feature. Include a concise description, affected version or commit, reproduction
steps, impact, and any suggested mitigation. Do not include real credentials,
personal data, or production data.

## Response targets

Maintainers target an acknowledgement within 5 business days, an initial
severity assessment within 10 business days, and coordinated remediation or a
status update within 30 days. These are targets, not a guarantee; the report
will be kept private until a coordinated disclosure is agreed.

## Scope

This policy covers the MESA source repository, published Python package, and
the Docker image built from this repository. Deployment-specific credentials,
third-party LLM accounts, and self-managed infrastructure remain the operator's
responsibility.

## V4 security boundary

The versioned v4 API resolves authorization as
principal → tenant → workspace → dataset → agent → server-created session.
Agent IDs are compute/persona context and are not standalone tenant
credentials. Sessionless search, document ingest, upload, `/v4/ask`, and
`/v1/chat/completions` require both the dataset role (`READER` or `WRITER`) and
explicit `SESSION_CREATE` permission for the target `agent_id` (default
`local-agent`). Purge and rollback require explicit dataset permissions.

V4 API credentials are issued as `key_id.secret`; only salted `scrypt` digests
are stored and compared in constant time (`secrets.compare_digest`) with
immediate revocation and expiry enforcement. Plaintext is displayed once by
`mesa-v4-admin` (or once on first boot of the Docker Compose production profile
when `MESA_API_KEY` is unset). Structured logs redact bearer tokens, `sk-*` and
`mk_*.*` keys, and configured `MESA_API_KEY` / `MESA_LLM_API_KEY` /
`MESA_EMBEDDING_API_KEY` values. Reports must never include credentials, source
content, claim tokens, or production data.

### Network, CORS, rate limits, uploads, and LLM prompts

- **CORS**: Cross-origin browser access is disabled by default (no
  `Access-Control-Allow-Origin` header is sent). Expose MESA behind an
  authenticated TLS reverse proxy and configure CORS policy there if browser
  clients are required.
- **Rate limits**: Per-minute (`MESA_RATE_LIMIT_PER_MINUTE`, default `60`) and
  SQLite-backed daily (`MESA_DAILY_REQUEST_LIMIT`, default `10000`) rate limits
  per authenticated principal are governed by `MESA_RATE_LIMIT_ENABLED`
  (`false` for localhost `mesa start`, `true` by default in
  `docker-compose.v4.yml`). There is no per-IP throttle on failed logins: behind
  a reverse proxy all clients share one IP, so such a throttle would let anyone
  lock out valid keys. Keys are 256-bit random secrets stored as scrypt digests,
  and scrypt work runs on a small fixed pool, which makes guessing impractical.
  Add connection or IP limits at your reverse proxy if you need them.
- **File uploads**: `POST /v4/documents/upload` enforces a 50 MB upload cap, a
  5,000,000 UTF-8 byte extracted-text cap, pre-parse ZIP expansion checks
  (50 MB max total uncompressed size for DOCX/XLSX/UDF), rejection of encrypted
  archives/PDFs and UDF XML `DOCTYPE` declarations, and basename-only filename
  sanitization (uploaded filenames are never opened as filesystem paths).
- **Untrusted evidence wrapping**: `/v4/ask`, `/v4/documents/{id}/summary`, and
  `/v1/chat/completions` enclose all retrieved source text inside
  `<UNTRUSTED_MEMORY_EVIDENCE>...</UNTRUSTED_MEMORY_EVIDENCE>` and neutralize
  embedded closing delimiters. Prompt wrapping reduces prompt-injection risk
  but cannot guarantee downstream model compliance.
- **Encryption at rest**: Embedded SQLite and LanceDB files are not encrypted
  by MESA Core; operators deploying on shared or self-hosted servers must use
  encrypted storage volumes, restricted file permissions, and TLS termination.
