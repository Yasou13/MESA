# MESA Architecture

This file is the current architecture index for MESA 0.8. Historical designs
are retained under [`docs/history/`](docs/history/).

## Repository layout

```text
MESA/
├── packages/
│   ├── mesa-memory/
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── mesa_contracts/
│   │       ├── mesa_storage/
│   │       ├── mesa_memory/
│   │       ├── mesa_workers/
│   │       ├── mesa_api/
│   │       ├── mesa_client/
│   │       ├── mesa_mcp/
│   │       └── mesa_runtime/
│   └── mesa-benchmark/
│       ├── pyproject.toml
│       ├── src/mesa_benchmark/
│       └── tests/
├── apps/
│   ├── control-dashboard/
│   └── benchmark-dashboard/
├── tests/
├── docs/
├── deploy/
└── tools/
```

The root `pyproject.toml` owns the `uv` workspace and shared development
configuration. `mesa-memory` and `mesa-benchmark` have independent manifests
and wheels. The main wheel never contains `mesa_benchmark`; the benchmark wheel
depends on the main package and never copies its private implementations.

## Dependency direction

```text
contracts
   ↑
storage → memory → workers
              ↑       ↑
             api     runtime ← mcp
              ↑       ↑
            client  dashboards
```

`mesa_runtime.app:create_app` is the canonical application factory and
`mesa_runtime` is the only composition root. Lower layers must not import the
runtime, FastAPI routers, or concrete worker implementations. The automated
layer check rejects reverse imports and package cycles.

`mesa_memory.ports` defines application-facing storage capabilities.
Implementations live in `mesa_storage`. `MemoryDAO` remains a compatibility
facade during 0.8 and 0.9 while behavior is migrated capability by capability.

## Runtime and distribution

- The API, worker-only and combined profiles are selected with
  `MESA_RUNTIME_PROFILE`.
- The Control Dashboard is built into the main wheel and Docker image and is
  served at `/dashboard/`.
- Showcase chat requires both `MESA_SHOWCASE_DEMO_ENABLED=true` and a
  development or test `MESA_ENVIRONMENT`. Production startup fails closed when
  development-only features are requested.
- V3 and V4 wire models live in `mesa_contracts`; legacy schema imports are
  temporary deprecation shims.

## Local data

Defaults follow the XDG base-directory convention:

- application data: `${XDG_DATA_HOME:-~/.local/share}/mesa`
- runtime state: `${XDG_STATE_HOME:-~/.local/state}/mesa`
- cache: `${XDG_CACHE_HOME:-~/.cache}/mesa`

Explicit configuration such as `MESA_STORAGE_ROOT` has precedence. During the
0.8–0.9 compatibility window, an existing repository-local directory is used
only when the XDG destination is absent, with a deprecation warning.

`mesa-local-state` reports the migration plan without writing. Its `--apply`
mode copies only to absent destinations, rejects symlinks, never overwrites,
and never deletes legacy sources.

## Detailed decisions

- [Canonical runtime, workspace, dashboard and local data](docs/adr/0014-canonical-runtime-workspace-and-local-data.md)
- [V4 architecture](docs/architecture-v4.md)
- [Storage capability boundaries](docs/adr/0012-memory-dao-repository-boundaries.md)
- [One-way package dependencies](docs/adr/0013-one-way-production-layer-dependencies.md)
- [Historical v3 whitepaper](docs/history/architecture-v3.md)
