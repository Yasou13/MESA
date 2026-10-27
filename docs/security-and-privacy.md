# Security and privacy

## Threat model and deployment boundary

Core enforces authenticated principals, scoped catalog/session access and
ownership-aware storage operations. An operator with filesystem access or
control over the process/credentials is outside that application boundary.
Embedded SQLite/LanceDB/Kùzu data is not encrypted at rest by Core; use reviewed
volume encryption and restrict OS permissions, backups and network access.

V4 keys are `key_id.secret`; the registry stores salted scrypt hashes, supports
rotation/revocation and binds principal identity server-side. Provision tenant
roles, agent permission and dataset purge/rollback permission explicitly.
Revalidate roles for each request; user metadata grants no authority.

Tenant → workspace → dataset roles inherit downwards. Sessions bind immutable
dataset membership and agent context. Negative isolation contracts cover SQL,
vectors, graph and historical mutation management; passing tests is not an
absolute guarantee against every possible leak. V3 agent/session checks are a
separate compatibility contract.

## Network and authentication

Versioned memory routes, `/health` and `/metrics` require an API
key. `/health/init` is a public readiness probe. OpenAPI, Swagger and Redoc are
not protected by the memory-router authentication dependency. Configure an
authenticated reverse proxy, TLS and access policy for nonlocal deployments.
Compose publishes loopback only; the normal runtime launcher binds `0.0.0.0`
inside the process/container. Do not expose its port without a network boundary.

Control-plane routes require an active principal with `ADMIN`; MCP gateway
credentials additionally bind a client/workspace. Gateway encrypted approvals
and bridge spools are separate from canonical memory storage. Protect the
Fernet key and local XDG environment files; do not reuse a shared gateway token.

Approvals and the control-plane audit belong to the HTTP MCP gateway. Direct
`mesa-mcp` stdio creates no local database and relies on the MCP client's tool
approval and the MESA API key's grants for access to Core.

## Untrusted data and logs

Exact source content remains data. Context uses `UNTRUSTED_MEMORY_EVIDENCE`
JSON records; prompt injection detection/rendering is not proof that a downstream
model will ignore malicious instructions. Review consumers, tool authority and
output handling. API bounds and source-file containment checks reduce abuse;
they do not establish document truth or a legal opinion.

Document purge erases its source and extracted text while retaining identifiers and hashes; shared assertion/entity text remains while another document still owns it.

Avoid query/source content, credentials, raw model output and claim tokens in
logs. IDs and aggregate diagnostics still reveal operational metadata and need
access/retention policy. Historical reports may contain deployment details;
they are not public defaults or evidence that those endpoints are safe to use.

`MESA_EXTERNAL_PROVIDER_ENABLED=false` fences canonical external model/provider
calls; local model provisioning/download and browser fonts are separate network
concerns. `MESA_ZERO_COST_MODE` is provider selection, not a complete air-gap or
exfiltration guarantee. Preserve validation assurance when selecting local models.

## Responsible reporting and supply chain

Follow [SECURITY.md](../SECURITY.md) for private reporting. CI checks secrets,
dependency/image vulnerabilities and SBOMs; release requires same-commit evidence
and artifact attestation verification. A local string scan does not replace those
checks. Store real keys in a secret manager and keep `.env` out of Git.
