# MESA — Memory Engine for Structured Agents

[![MESA CI](https://github.com/Yasou13/MESA/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Yasou13/MESA/actions/workflows/ci.yml?query=branch%3Amain)
[![Release](https://img.shields.io/github/v/release/Yasou13/MESA)](https://github.com/Yasou13/MESA/releases/latest)
[![License: MIT](https://img.shields.io/github/license/Yasou13/MESA)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)

MESA Core stores source-backed agent memory and returns scoped evidence over
HTTP, Python clients and MCP. SQLite owns the catalog, assertions, mutation
ledger and artifact ownership. LanceDB and Kùzu provide derived projections.

Package version is **0.8.0**. V4 is the current API; V3 remains a compatibility
surface. Production qualification remains at the release candidate stage:
**NO-GO** until the [release gates](docs/release.md) pass for the intended commit.
The [v0.8.0 publication exception](CHANGELOG.md#v080-publication) is separate from
production certification.

## Start here

```bash
git clone https://github.com/Yasou13/MESA.git
cd MESA
uv sync --locked --python 3.10
uv pip check
```

Python 3.10–3.13 are the CI matrix; package metadata declares Python >=3.10.
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first.
The command selects a supported interpreter; substitute 3.11–3.13 if needed.
The base install includes native storage and HTTP/SDK support; `dev`, provider
SDKs and local model dependencies are optional extras.
Then follow the [local quickstart](docs/getting-started.md), which provisions
a disposable root, credentials and an authorized session before insertion.

| Goal | Read |
|---|---|
| Try storage without model calls | [Model-disabled V3 quickstart](docs/getting-started.md) |
| Run canonical V4 ingestion | [V4 provider prerequisites](docs/getting-started.md#v4-canonical-example-provider-prerequisites) and [installation](docs/getting-started.md#installation) |
| Use Docker or choose providers | [Installation](docs/getting-started.md#installation), [configuration](docs/configuration.md) |
| Connect a client | [API and SDK](docs/api-reference.md), [MCP](README_MCP.md) |
| Understand or change the system | [Architecture](ARCHITECTURE.md), [contributing](CONTRIBUTING.md) |
| Find any other guide | [Documentation index](docs/README.md) |

## Runtime model

V4 runs one `combined` storage owner per root. An authenticated session binds
a principal, tenant, workspace, agent and immutable dataset set. Insertion
admits durable work; validation and ordered projections precede `COMMITTED`.
Retrieval combines vector, BM25, assertion and graph lanes with weighted rank
fusion, then packs source evidence into a bounded context.

`FactExtractionService` owns canonical V4 extraction. REBEL is not part of
canonical V4 extraction. Validation can use deterministic checks (mode 0), a
single LLM (mode 1), or dual LLM consensus (mode 2). Mode 0 disables LLM
validation, not canonical extraction and embedding requirements.

`docker-compose.v4.yml` defines the V4 single-owner topology.
`docker-compose.yml` defines V3's separate API/worker topology. Use separate
storage roots. Model-disabled V4 inserts return
`503 canonical_processing_unavailable`; the V3 demo does not prove V4 ingestion.

### Legacy/V3 CPU-Only REBEL Extraction

REBEL is an optional V3 extraction path requiring the ML extra. Its resource
cost and model quality need separate measurement. It does not replace the V4
canonical extraction service.

## Operational limits

`GET /v4/capability` reports runtime capabilities. Embedded storage requires a
single writer and workload-specific capacity testing. Projection rebuild is
offline, full-root and disabled by default. Historical benchmarks and audits
are not current production measurements. Evidence linkage and model consensus
do not establish source truth or legal correctness.

See [limitations](docs/architecture-v4.md#limitations),
[operations](docs/RUNBOOK.md), [security](docs/security-and-privacy.md) and
[vulnerability reporting](SECURITY.md).

## Development

Use [testing](docs/testing.md) for focused checks, optional dependencies and
document validation. Changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE).
