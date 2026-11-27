# Install MESA on your own server

This guide shows how to run MESA on a company or law-firm server using Docker
Compose. No external cloud service is required: documents, indexes, and access
keys stay inside a persistent Docker volume on your own machine.

## Prerequisites and minimum hardware

- A Linux server with Docker Engine and Docker Compose v2 (`docker compose`).
- Minimum hardware based on measured peak RSS under 8-client concurrent load:

| Corpus size | Mode | Minimum RAM | Recommended RAM | Disk (SSD) |
|---|---|---|---|---|
| Up to 25,000 chunks | Models off (BM25 + legal rules) | 2 GB | 4 GB | 2 GB free |
| Up to 100,000 chunks | Models off (BM25 + legal rules) | 4 GB | 4–6 GB | 5 GB free |
| Up to 100,000 chunks | External 1024-dim embedding server | 6 GB | 8 GB | 10 GB free |
| Up to 100,000 chunks | In-process `local:BAAI/bge-m3` (`MESA_EXTRAS=ml`) | 8 GB | 16 GB | 15 GB free |

## 1. Start MESA with one command

Clone or copy the repository to your server and start the production Compose
profile:

```bash
docker compose -f docker-compose.v4.yml up -d --build
```

No environment variables are required for a first start. On initial boot, the
container:

1. Runs as the non-root `mesa` user with a read-only root filesystem.
2. Stores all data in the persistent Docker volume `mesa-v4-data` mounted at
   `/var/lib/mesa`.
3. Generates an API key, stores only its salted `scrypt` hash in
   `/var/lib/mesa/data/rbac_policy.db`, writes `/var/lib/mesa/credentials.json`
   (mode `0600`), and **prints the bootstrap API key once** to container logs.
4. Turns on per-minute (`60/minute`) and daily (`10,000/day`) rate limits by
   default (`MESA_RATE_LIMIT_ENABLED=true`).
5. Binds to `127.0.0.1:8000` on the host so the port is not exposed directly to
   the internet.

## 2. Read your bootstrap API key and check health

Wait for the healthcheck to turn `healthy`, then read the one-time bootstrap key
from the initial startup logs:

```bash
docker compose -f docker-compose.v4.yml ps
docker compose -f docker-compose.v4.yml logs mesa-v4 | grep "MESA bootstrap API key"
```

Save that key in your password or secret manager. Subsequent container restarts
reuse the persistent volume and will **not** print the key again.

Verify that MESA is ready and accepts your key:

```bash
curl --fail http://127.0.0.1:8000/health/init
curl --fail -H "X-API-Key: YOUR_BOOTSTRAP_API_KEY" http://127.0.0.1:8000/health
```

The authenticated `/health` response returns `{"status": "healthy", "version": "...", ...}`.
Requests without a valid `X-API-Key` return HTTP `401`.

> If your organization ships container stdout to a shared log aggregator, prefer
> pre-provisioning keys offline with `mesa-v4-admin issue-key` or supplying
> `MESA_API_KEY` and `MESA_PRINCIPAL_ID` via a secret manager so no bootstrap key
> is written to container logs. See [Credential bootstrap](RUNBOOK.md#credential-bootstrap).

## 3. Optional: enable embeddings or a chat model

By default, `MESA_MODEL_ENABLED=false` and `MESA_EXTERNAL_PROVIDER_ENABLED=false`
so MESA runs completely offline with BM25 and Turkish legal citation rules.

To connect a local Ollama server running on the Docker host (already reachable
as `host.docker.internal`) or an OpenAI-compatible LLM/embedding endpoint, create
a `.env` file next to `docker-compose.v4.yml` (mode `0600`, never commit it):

```bash
# Example: local Ollama on the Docker host (stays offline)
MESA_MODEL_ENABLED=true
MESA_EMBEDDING_MODEL=ollama:bge-m3
MESA_LLM_MODEL=qwen2.5:7b
MESA_LLM_BASE_URL=http://host.docker.internal:11434/v1

# Optional: remote OpenAI-compatible provider (explicit opt-in)
# MESA_EXTERNAL_PROVIDER_ENABLED=true
# MESA_LLM_BASE_URL=https://api.example.com/v1
# MESA_LLM_MODEL=your-model
# MESA_LLM_API_KEY=your-provider-key
```

Apply environment changes with:

```bash
docker compose -f docker-compose.v4.yml up -d
```

To run `local:BAAI/bge-m3` inside the container instead of a separate embedding
server, build the image with `--build-arg MESA_EXTRAS=ml,adapters`. See
[Configuration](configuration.md) for all model and capacity settings.

## 4. Network and security recommendations

- **TLS and reverse proxy**: `docker-compose.v4.yml` publishes `127.0.0.1:8000`
  only. Place Nginx, Caddy, or Traefik in front of `127.0.0.1:8000` with TLS
  and internal network/VPN restrictions.
- **Rate limits**: Enabled by default (`MESA_RATE_LIMIT_ENABLED=true`,
  `MESA_RATE_LIMIT_PER_MINUTE=60`, `MESA_DAILY_REQUEST_LIMIT=10000`). Raise
  `MESA_RATE_LIMIT_PER_MINUTE` if you run high-volume batch ingestion from a
  trusted internal loader.
- **Additional API keys**: Issue separate least-privilege keys per application
  or user with `mesa-v4-admin`:

```bash
docker compose -f docker-compose.v4.yml exec mesa-v4 \
  mesa-v4-admin issue-key --principal app-reader
```

- **Disk encryption**: MESA stores SQLite (`mesa.db`) and LanceDB
  (`vector.lance`) in the Docker volume without application-level encryption at
  rest. Use encrypted server volumes and restrict host Docker access. See
  [Security policy](../SECURITY.md).

## 5. Back up and restore

Always stop the container before copying the volume so SQLite WAL files and
LanceDB snapshots are consistent:

```bash
# Back up
docker compose -f docker-compose.v4.yml stop mesa-v4
docker run --rm \
  -v mesa-v4_mesa-v4-data:/source:ro \
  -v "$PWD/backups:/backup" \
  debian:bookworm-slim \
  tar -czf "/backup/mesa-backup-$(date +%Y%m%d-%H%M%S).tar.gz" -C /source .
docker compose -f docker-compose.v4.yml start mesa-v4
```

To restore onto a clean volume:

```bash
docker compose -f docker-compose.v4.yml stop mesa-v4
docker run --rm \
  -v mesa-v4_mesa-v4-data:/target \
  -v "$PWD/backups:/backup:ro" \
  debian:bookworm-slim \
  sh -c 'rm -rf /target/* && tar -xzf /backup/mesa-backup-YYYYMMDD-HHMMSS.tar.gz -C /target'
docker compose -f docker-compose.v4.yml start mesa-v4
```

Store backups encrypted off-host and test restores periodically.

## 6. Upgrade to a new MESA version

1. Take a backup using step 5.
2. Pull the new MESA release (or update `MESA_V4_IMAGE` in `.env`).
3. Rebuild and recreate the container (the `mesa-v4-data` volume is preserved
   and SQLite Alembic migrations run automatically on startup):

```bash
docker compose -f docker-compose.v4.yml up -d --build
```

4. Verify health and version:

```bash
docker compose -f docker-compose.v4.yml exec mesa-v4 python -m mesa_memory.container_health
curl --fail -H "X-API-Key: YOUR_BOOTSTRAP_API_KEY" http://127.0.0.1:8000/health
```
