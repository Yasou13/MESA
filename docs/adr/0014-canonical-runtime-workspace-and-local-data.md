# ADR-0014: Canonical runtime, workspace, dashboards and local data

- Status: Accepted
- Date: 2026-07-30
- Target release: 0.8.0

## Context

MESA previously had more than one application assembly path, server-owned API
schemas, a root distribution that also discovered benchmark code, dashboards
outside the package artifacts, and repository-local runtime data. These
couplings made imports, deployments and local development depend on the
checkout layout.

The migration must preserve existing HTTP contracts, CLI behavior, import
names and the database schema through the 0.8–0.9 compatibility window.

## Decision

1. `mesa_runtime.app:create_app` is the sole canonical FastAPI factory.
   `mesa_runtime` may compose concrete storage, memory, worker, MCP and
   dashboard components; lower layers may not import it.
2. V3 and V4 request/response types are owned by `mesa_contracts`. API, client
   and MCP consume these contracts without importing one another.
3. Storage seams are capability-oriented (`CatalogStore`, `MutationLedger`,
   `ProjectionStore`, `IngestionQueue`, `LegacyMemoryStore`,
   `PurgeCoordinator`) and are declared in `mesa_memory.ports`. Concrete
   persistence belongs to `mesa_storage`.
4. The repository root is a non-distributable `uv workspace`.
   `packages/mesa-memory` and `packages/mesa-benchmark` own independent
   manifests, source trees and wheels.
5. Control Dashboard assets are mandatory contents of the main wheel and
   runtime image. Benchmark Dashboard assets are mandatory contents of the
   benchmark wheel and image. Generated assets are not committed.
6. Runtime data, state and cache default to XDG directories. Explicit settings
   win. Legacy repository-local paths are warning-backed fallbacks only when
   the preferred destination is absent.
7. Local data is never moved or deleted automatically. `mesa-local-state` is a
   dry-run-first, copy-only migration command.

## Compatibility

The following paths remain shims in 0.8 and 0.9:

- `mesa_memory.api.server`
- `mesa_memory.runtime_entrypoint`
- `mesa_memory.worker_runtime`
- `mesa_api.schemas`
- `MemoryDAO`
- `scripts/run_server.py`
- `scripts/run_demo_rag.py`

Deprecation warnings become prominent in 0.9. Removal is scheduled for 0.10.
No database migration is part of this decision.

## Consequences

- Package boundaries can be tested from built wheels rather than inferred from
  source layout.
- The client and MCP layers share wire types without depending on FastAPI
  implementation packages.
- Dashboard behavior is the same in editable installs, wheels and images.
- A source checkout no longer receives runtime files during ordinary tests or
  builds.
- Storage extraction remains incremental: `MemoryDAO` delegates capability by
  capability until its compatibility window ends.

## Enforcement

- `scripts/check_layer_imports.py` rejects reverse dependencies and cycles.
- Packaging tests inspect both wheel manifests.
- Runtime tests compare compatibility imports and profile behavior.
- Dashboard tests cover fail-closed production configuration.
- Local-state tests cover override precedence, XDG defaults, legacy warnings,
  dry-run, non-overwrite and non-deletion behavior.
