# MESA Memory

MESA is a durable, tenant- and dataset-isolated memory engine for autonomous
AI agents. This distribution contains the canonical runtime, V3/V4 API
contracts, Python client, MCP integration, storage implementations, workers
and the built Control Dashboard.

The source repository is an independent-package `uv workspace`. The benchmark
suite is distributed separately as `mesa-benchmark` and is not included in
this wheel.

## Runtime

The canonical application factory is:

```text
mesa_runtime.app:create_app
```

Select `api-only`, `worker-only` or `combined` with
`MESA_RUNTIME_PROFILE`, then start:

```bash
python -m mesa_runtime.cli
```

The dashboard is served at `/dashboard/`. Showcase chat is disabled by default
and cannot be enabled in production.

## Local data

Application data defaults to `${XDG_DATA_HOME:-~/.local/share}/mesa`.
`MESA_STORAGE_ROOT` overrides this location. Legacy repository-local state can
be inspected and copied without deletion:

```bash
mesa-local-state --repository .
mesa-local-state --repository . --apply
```

See the [repository README](https://github.com/Yasou13/MESA), architecture
index and installation guide for deployment, compatibility and security
requirements.
