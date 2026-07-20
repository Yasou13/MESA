# GitHub external gates readiness

Run: `rem-20260720-134653-github-gates-prep`
Branch: `audit/production-readiness`
HEAD: `c69d1f9c18844c393c26291db6c67628d82167f1`

| ID | Existing implementation | Existing workflow | Missing part | Action |
|---|---|---|---|---|
| FLOW-001 | Durable dispatch, fenced completion, finalization, principal and worker contracts | None | GitHub lifecycle gate and artifacts | CREATE_MINIMAL |
| PERF-003 | Queue budgets, worker supervision, PageRank and consolidation contracts | None | Bounded capacity gate and factual report | CREATE_MINIMAL |
| DOCKER-001 | Non-root Dockerfile, volume, health check, disabled providers | `docker-build` only | Runtime/volume/non-root smoke | FIX_EXISTING |
| DOCKER-003 | Split API/worker Compose topology | Static Compose render only | Compose build/up/restart/log gate | FIX_EXISTING |
| DOC-002 | Installation guide existed | None | Current executable runbook and smoke gate | FIX_EXISTING |
| CI-002 | Canonical CI workflow existed | `ci.yml` | Valid paths, branch coverage, package/migration/core jobs | FIX_EXISTING |
| COVERAGE-001 | pytest-cov and threshold existed | None | SDK inclusion, reports/artifacts, verified baseline | FIX_EXISTING |

## FLOW-001

Status: READY_AFTER_CREATE
Files: `.github/workflows/external-release-gates.yml`
Local validation: durable dispatch, receipt/fence, finalization, restart, and foreign-principal target tests passed.
GitHub job: `flow-e2e`
Pass criteria: all lifecycle tests pass; receipt/fence, restart, terminal finalization, and foreign scope denial remain correct; `flow-logs` is uploaded.

## PERF-003

Status: READY_AFTER_CREATE
Files: `.github/workflows/external-release-gates.yml`
Local validation: queue admission, bounded worker, PageRank, and entity consolidation targets passed.
GitHub job: `performance-capacity`
Pass criteria: bounded admission/restart/worker/graph contracts pass; factual JUnit-derived `performance-capacity.json` and log are uploaded. This is not a production throughput SLO.

## DOCKER-001

Status: READY_AFTER_FIX
Files: `.github/workflows/external-release-gates.yml`
Local validation: Dockerfile deployment contract and `docker compose config --quiet` passed.
GitHub job: `docker-image`
Pass criteria: build, non-root inspect, health smoke, and named-volume restart persistence pass; `docker-logs` is uploaded.

## DOCKER-003

Status: READY_AFTER_FIX
Files: `.github/workflows/external-release-gates.yml`
Local validation: Compose topology contract and render passed.
GitHub job: `docker-compose`
Pass criteria: split roles build/start, authenticated API health, worker health, role restart, and volume path checks pass; `compose-logs` is uploaded.

## DOC-002

Status: READY_AFTER_FIX
Files: `docs/installation.md`, `.github/workflows/external-release-gates.yml`
Local validation: documented runtime-profile, recovery CLI, migration/recovery and deployment contract commands were checked without starting a service or migration.
GitHub job: `docs-smoke`
Pass criteria: canonical install/profile/recovery commands and deployment/migration/recovery contract tests pass; `docs-smoke` is uploaded.

## CI-002

Status: READY_AFTER_FIX
Files: `.github/workflows/ci.yml`
Local validation: YAML parse, all 21 referenced test paths, `pip check`, and focused gate tests passed.
GitHub job: `quality`, `core-tests`, `migration-dr`, `package`
Pass criteria: pinned actions, canonical install, pip check, compile/lint, core/migration/DR tests, reproducible wheel, clean install, imports, and CLI pass; `package-report` is uploaded.

## COVERAGE-001

Status: READY_AFTER_FIX
Files: `pyproject.toml`, `.github/workflows/ci.yml`
Local validation: 903 tests passed with all production packages including `mesa_client`; measured total was 82.19%. SDK auth/MCP tests collect.
GitHub job: `coverage`
Pass criteria: threshold 82% passes, terminal/XML/HTML/JSON reports exist, SDK remains included, and `coverage-report` is uploaded.

The only remaining evidence is the actual GitHub-hosted execution; no source or configuration gap remains. No commit or push was created. The final combined local read-only check was not started because the sandbox approval transport disconnected; earlier YAML, Compose, target-test, coverage, dependency, and protected-hash checks are recorded above.
