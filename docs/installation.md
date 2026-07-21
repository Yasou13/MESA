# Installation and operator runbook

This guide uses the current fail-closed runtime profiles. It never loads a
repository `.env` automatically, starts Ollama, or enables an external model
provider. Keep production credentials in a secret manager; do not commit them.

## Requirements and clean install

- Python 3.10 or newer.
- Docker and Docker Compose are optional and required only for the Compose
  deployment instructions.

```bash
git clone https://github.com/Yasou13/MESA.git
cd MESA
python3 -m venv venv
. venv/bin/activate
python -m pip install -e ".[dev]"
python -m pip check
```

`pyproject.toml` is the canonical dependency definition. Optional adapters and
benchmark integrations are not needed for the core runtime or CI profile.
`uv.lock` freezes the resolved graph used by CI and Docker; use
`uv sync --locked --extra dev` when `uv` is available and an exact development
environment is required.

## Environment template

Copy the template only to define variable names; replace the placeholders using
your secret manager and do not check the result into version control.

```bash
cp .env.example .env
```

The runtime requires these explicit values:

```bash
export MESA_RUNTIME_PROFILE=api-only
export MESA_STORAGE_ROOT=/srv/mesa/data
export MESA_LOAD_DOTENV=false
export MESA_MODEL_ENABLED=false
export MESA_EXTERNAL_PROVIDER_ENABLED=false
export MESA_API_KEY="$(secret-manager read mesa-api-key)"
export MESA_PRINCIPAL_ID=service-api
export MESA_PRINCIPAL_TYPE=SERVICE
export MESA_PRINCIPAL_STATUS=active
```

`MESA_STORAGE_ROOT` must be an application-owned writable directory, not the
repository root, home directory, or filesystem root. The `test-isolated`
profile is reserved for paths under `/storage/mesa-lab`.

## API-only and worker-only processes

Start the API role without workers:

```bash
export MESA_RUNTIME_PROFILE=api-only
python -m mesa_memory.runtime_entrypoint
```

In a separate terminal, start the durable cold-path worker with the same
storage root and credentials:

```bash
export MESA_RUNTIME_PROFILE=worker-only
python -m mesa_memory.runtime_entrypoint
```

Check the API with an authenticated request:

```bash
curl --fail -H "X-API-Key: $MESA_API_KEY" http://127.0.0.1:8000/health
```

The worker writes `worker-readiness.json` below `MESA_STORAGE_ROOT`. Do not run
the API profile as a worker or the worker profile as an HTTP server.

Set `MESA_REQUIRE_WORKER_READINESS=true` when the API must fail readiness if a
separate worker has no fresh heartbeat on the shared storage root. Compose sets
this for the API role by default; a deliberately workerless standalone API can
leave it unset.

## Compose deployment

Compose creates separate `mesa-api` and `mesa-worker` roles sharing only the
named `mesa-data` volume. The API readiness probe requires the worker's fresh
shared-volume heartbeat. Model, provider, and dotenv loading remain disabled.

```bash
export MESA_API_KEY="$(secret-manager read mesa-api-key)"
export MESA_PRINCIPAL_ID=service-api
docker compose config --quiet
docker compose up --build -d
docker compose ps
curl --fail -H "X-API-Key: $MESA_API_KEY" http://127.0.0.1:8000/health
```

Use `docker compose restart mesa-api` or `docker compose restart mesa-worker`
for a controlled role restart. The named volume survives a normal `down`.

## Migration, backup, and restore

Run migrations while the application is stopped:

```bash
alembic -c mesa_storage/alembic.ini upgrade head
```

The backup CLI requires an explicit trusted parent and an offline source root:

```bash
mesa-recovery --trusted-root /srv/mesa backup \
  --source-root /srv/mesa/data --backup-root /srv/mesa/backups/2026-07-20 \
  --stores-stopped
mesa-recovery --trusted-root /srv/mesa validate \
  --backup-root /srv/mesa/backups/2026-07-20
mesa-recovery --trusted-root /srv/mesa restore \
  --backup-root /srv/mesa/backups/2026-07-20 --restore-root /srv/mesa/restore-test
```

Restore into a new empty directory, validate it, and then perform the
application's post-restore reconciliation before any production cutover.

## Smoke, shutdown, rollback, and external gates

For a local, model-disabled smoke check use the canonical contracts:

```bash
python -m pytest -q tests/test_deployment_assets.py tests/test_runtime_profiles_contract.py \
  tests/test_migration_closure.py tests/test_recovery_contract.py
```

On failure, collect `docker compose logs`, stop roles gracefully with
`docker compose down`, and preserve the named volume for investigation. Use
`docker compose down -v` only for a disposable test deployment.

After a push, run **MESA CI** and **MESA external release gates** from GitHub
Actions. The latter provides manual inputs for flow, bounded capacity, Docker,
and documentation gates; it does not push an image or use production secrets.
