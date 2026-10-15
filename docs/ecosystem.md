# Ecosystem and ownership boundaries

| Project | Ownership | Core release implication |
|---|---|---|
| MESA Core | Runtime, catalog/mutations, projections, scoped retrieval, SDK/MCP | This repository's package and GitHub release only |
| MESA_Data | Source acquisition, metadata/identity, revisions/chunking, native publisher | Producer must satisfy Core HTTP contract; no verified checkout in this audit |
| MESA_E2E_Certification | Freeze, official TEST, sealed answer context, scorer and verdict | Independent certification; Core unit tests cannot change its verdict |
| mesa_benchmark | Comparative methodology, runner, external datasets/judges | Core `mesa_evals` is historical diagnostics plus deterministic RRF contract tooling |
| MESA_Law | Legal product/integration work outside Core | No Core legal-advice or legal-certification guarantee |
| mesa-model-tests | Independent DEV model qualification | Model quality and pending human review are separate from Core release |

The source consumer contract is [concepts and ingestion](concepts-and-ingestion.md)
and [API](api-reference.md). Supply exact content, immutable revision/chunk IDs,
source references and verified legal identity where appropriate. Core validates
schema/alias consistency and linkage; it does not fetch official laws or establish
that producer metadata is legally correct. SDK insertion currently lacks the HTTP
`legal_identity` parameter; use reviewed HTTP payloads for this contract.

The certification repository was inspected read-only at
`3b7562cb65800cde27877dc3a7cd505a70822bf8`; its README assigns official certification
ownership separately. No official TEST, holdout tuning, VM or remote Ollama run
was performed in this documentation refresh. Previously reported Profile B
failures remain historical failures. No new sealed PASS is claimed.

Core `ContextBuilder` budget/provenance behavior and Profile B's independently
sealed Top-5/context/scorer settings are distinct contracts. A successful Core
DEV example proves the exercised path only, not native ecosystem delivery or
model accuracy. Live Qwen/Magibu pairing quality is `NOT_MEASURED` here.
