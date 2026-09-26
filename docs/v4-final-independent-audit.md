# MESA V4 — Final independent audit

Date: 2026-09-26. Branch: `fix/v4-hardening-final-correction`.
Baseline: `6a9d696`; prior hardening reviewed from `3552636`.

## Verdict

**PASS** for the requested V4 extraction/retrieval/evidence/context contract.
The original 211 relevant tests passed before corrections, while independent
production-path regressions reproduced missed defects. Final results below
refer to the corrected code, held unchanged throughout the final full suite.

## Previous agent assessment

| Area | Assessment | Independent finding |
| --- | --- | --- |
| Canonical fusion | PARTIALLY CORRECT | DAO fused canonical IDs, but MCP replaced evidence identity with entity identity. |
| Vector representation | CORRECT | Assertion-specific payload and document/query embedding roles retained. |
| Legal identity | PARTIALLY CORRECT | Simple reversed references worked; durations, adjacent citations and lane scoring still produced false matches. |
| Lexical/assertion ranking | PARTIALLY CORRECT | Real passage FTS existed; legal substring scoring remained in three places. |
| RRF | PARTIALLY CORRECT | Weights/ranks correct for unique inputs; repeated lane IDs voted twice. |
| Scope | PARTIALLY CORRECT | Canonical filters existed; HTTP/SDK/MCP jurisdiction propagation and timezone comparison were incomplete. |
| Extraction/object typing | INCORRECT | Durable serialization and worker conversion each dropped object_type; compatibility paths dropped metadata. |
| Graph semantics | INCORRECT | Merged assertion paths were paired with one node path; provider omitted per-edge directions. |
| Evidence materialization | INCORRECT | First supporting assertion could overwrite matched source document/chunk. |
| Context budgeting | PARTIALLY CORRECT | Hard count/atomic removal existed; an oversized top item could evict every fitting item. |
| Final test sufficiency | INCORRECT | Four-lane helper test and copied multi-path aggregation did not prove production behavior. |
| Mutation administration / MCP recall | MISSED | Wrong-agent state transition and final evidence conversion defects were reproduced. |

Standards review confirmed missing public scope propagation and duplicated legal
matching; spec review independently found both durable type drops. After fixes,
a second audit found mutation-owner validation, timezone comparison and zero-
confidence graph corroboration defects. Full-suite review additionally found
the MCP recall boundary defect. All confirmed defects below were corrected.

## Confirmed bugs / Fixes implemented

| Priority | Root cause / affected path | Files/functions; before → after |
| --- | --- | --- |
| P0 | State update selected the owner but ignored a missing match. | `MemoryDAO.set_mutation_state`: another agent could transition a known mutation → missing owner match returns false without mutation. DAO-level reproduction; no HTTP authorization bypass is claimed. |
| P1 | object_type was omitted twice; raw tails were pre-created as entities. | `record_mutation_extraction`, worker `_triplets`, extractor/compatibility conversion, parser/writer: explicit types and source/time/metadata survive to SQL, LanceDB and Kuzu; values are classified before entity creation. |
| P1 | Short alias and overlapping reverse citation parsing. | `LegalEntityResolver.extract_citations`: `3 ay 15 gün` and `TBK 117 CMK 86` no longer fabricate citations; Turkish I variants resolve consistently. |
| P1 | Alias substring and free article-number boosts. | `search_v4_memory`, `search_v4_graph`: compare canonical statute/article identities; `onay`, CMK/TMK and cross-statute article numbers no longer supply wrong legal relevance. |
| P1 | Jurisdiction stopped at the builder boundary; None dates became empty HTTP parameters. | V4 router, sync/async SDK, MCP adapter/service/tool schemas: TR scope reaches SQL and formatted context; omitted date parameters are omitted on the wire. |
| P1 | Temporal scope compared ISO strings. | `search_v4_memory`: compare SQLite Julian instants, preserving offset equivalence before ranking. |
| P1 | Multi-path union was treated as one path; target aggregation hid distinct evidence. | Kuzu provider and DAO: retain aligned paths, predicates, directions, seeds and support; fuse once per terminal assertion, preserving distinct terminal assertions. |
| P1 | Primary source was copied from first support. | DAO materialization: matched assertion first, matched/supporting outputs distinct, source chunk/document retained from the matched assertion. |
| P1 | Zero graph confidence was treated as default confidence or counted as support. | Kuzu provider: preserve zero and reject low-confidence paths before corroboration. |
| P1 | Oversized top evidence evicted fitting evidence. | `ContextBuilder.build_context`: remove individually impossible units first; keep atomic proofs complete and count final rendered text. |
| P1 | MCP used entity ID/name instead of evidence ID/content. | `_typed_result`: preserve canonical evidence identity, primary source and evidence text through recall and restart. |
| P2 | RRF duplicate lane IDs counted twice; lane insertion order affected addition. | `rrf_fuse_lanes`: deduplicate before rank assignment and sum in fixed lane order. |
| P2 | Repeated support hydration caused per-assertion catalog queries. | DAO: reuse scoped assertion snapshot and resolve public catalog identifiers in batches of 500; path dedup uses a set. |

## Previously unknown blockers discovered

Wrong-agent state mutation, timezone-offset eligibility, empty SDK date filters,
zero-confidence graph support, oversized top-evidence starvation, and MCP
identity/content loss were found beyond the initial correction hypotheses.

## Tests added

- `test_v4_final_extraction_legal_audit.py`: actual FactExtractionService → both
  primary/additional conversion routes → durable outbox → real SQL/LanceDB/Kuzu;
  28 values including quantities, currency, dates, legal references, real names
  and explicit types that downstream heuristics cannot reconstruct.
- `test_v4_final_legal_audit.py`: duration/embedded-alias negatives, adjacent and
  reversed citations, Turkish I/İ/ı/i normalization.
- `test_v4_independent_retrieval_audit.py`: actual four-lane search, exact RRF,
  repeated deterministic results, primary/source identity, aligned parallel
  paths, real three-hop forward/reverse/forward traversal, legal collisions,
  shared-entity tenant/dataset/agent/status/time/jurisdiction exclusions in all
  lanes, proof-budget sweep, oversized evidence, offsets and zero confidence.
- `test_v4_context_jurisdiction_boundary.py`: HTTP, async SDK and MCP through real
  SQLite retrieval and ContextBuilder; sync SDK request serialization.
- `test_v4_mutation_agent_isolation.py`: wrong agent cannot transition a mutation;
  the owning agent can.
- MCP recall regression: two assertions on one entity remain distinct and select
  their primary evidence. Restart test now requires identical evidence content
  and IDs before/after restart, rather than an object entity label.

Existing tests were corrected only where they encoded obsolete contracts:
explicit object_type is now required in durable payload expectations; two
*different* terminal assertions must remain two candidates. No skip, xfail,
relaxed assertion or deleted failing test was used to obtain a green suite.

## Verification commands

Executed from repository root using the existing `.venv`; lockfile unchanged.

| Exact command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | PASS — 1807 passed, 128 deprecation warnings, 436.09s. |
| `.venv/bin/python -m pytest -q tests/test_v4* tests/test_r6_context_security_correctness.py tests/test_p0_context_builder.py` | PASS — 242 passed, 82.11s. |
| `.venv/bin/python -m pytest -q mesa-benchmark/tests` | PASS — 125 passed; 4 pre-existing skips. |
| `.venv/bin/python -m pytest -q tests/test_operator_approval_lifecycle.py tests/test_mcp_v4_service.py tests/test_mcp_v4_tools.py` | PASS — 10 passed. |
| `.venv/bin/python -m ruff check .` | PASS. |
| `.venv/bin/python -m compileall -q mesa_memory mesa_storage mesa_workers mesa_api mesa_client` | PASS. |
| `.venv/bin/python -m mypy mesa_memory mesa_storage mesa_workers mesa_api mesa_client --ignore-missing-imports --explicit-package-bases --follow-imports=skip` | PASS — 136 source files. |
| `.venv/bin/python -m mypy mesa-benchmark/mesa_benchmark` | PASS — 59 source files. |
| `.venv/bin/python scripts/check_layer_imports.py` | PASS. |
| `.venv/bin/python scripts/check_mypy_override_ratchet.py` | PASS. |
| `UV_CACHE_DIR=/tmp/mesa-uv-cache uv pip check --python .venv/bin/python` | PASS — 159 packages compatible. |
| `UV_CACHE_DIR=/tmp/mesa-uv-cache uv lock --check --offline` | PASS — 263 packages resolved. |
| `git diff --check` | PASS. |

The initial sandbox run stalled in SQLite thread wakeup. The same tests ran
successfully outside the sandbox with temporary local databases. Initial full
suite: 1794 passed/7 failed; three outdated type expectations, three source-
inspection errors during active file edits, and one real MCP defect. All were
resolved and the final full run used stable source files.

## Phase 1–10 final status

| Phase | Status | Evidence |
| --- | --- | --- |
| 1 — evidence identity | PASS | Exact production four-lane fusion; matched/supporting source separation; MCP identity. |
| 2 — vector representation | PASS | Semantic assertion payload, embedding roles and real LanceDB projection tests. |
| 3 — legal normalization | PASS | Forward/reverse, Turkish variants, duration and collision regressions. |
| 4 — passage lexical | PASS | Real SQLite FTS; canonical legal matching; scope before limit. |
| 5 — assertion lane | PASS | Multi-signal scoring; canonical citation matching; deduplicated query terms. |
| 6 — RRF | PASS | Exact four-lane score, duplicate vote prevention, fixed ordering and existing phase suite. |
| 7 — scope | PASS | All-lane shared-entity negative matrix, API→context jurisdiction, offset and mutation isolation. |
| 8 — object semantics | PASS | 28-value real outbox matrix for both conversion routes and all three stores. |
| 9 — graph | PASS | Actual Kuzu mixed directions, aligned paths, corroboration and canonical dedup. |
| 10 — bounded context | PASS | Evidence-first rendering, atomic proof budgets, fitting evidence retained, counted final text. |

## Remaining P0/P1

No confirmed V4 P0/P1 blocker remains within the verified scope.

## Readiness

**READY** for the explicitly audited V4 MVP extraction/retrieval/context contract.
General production-release gates in `docs/release.md` (including a real model
rehearsal and 24-hour soak) were not executed by this audit.

## Verification confidence

- Static: high; compile, lint, type and dependency checks executed.
- Unit: high; exact invariants and negative cases executed.
- Integration: high; real production conversion, outbox, DAO, API, SDK and MCP paths.
- Adversarial: high within the stated fixtures; two audit rounds plus full-suite correction.
- Full suite: high; 1807 passing tests on stable final source, no unexplained regression.
- Real LanceDB: verified, version 0.34.0 (PyArrow 25.0.0), deterministic embedding adapter.
- Real Kuzu: verified, version 0.11.3; real traversal/projection, including mixed three-hop paths.
- Real LLM/model quality and production-load performance: not certified; deterministic adapters used.

## Commits

- `0f770d0` fix(legal): prevent duration and adjacent citation false positives
- `1cf875c` fix(extraction): preserve object semantics through durable projection
- `89cd157` fix(scope): carry jurisdiction through HTTP SDK and MCP context
- `35ea434` fix(scope): enforce owning agent on mutation state transitions
- `367f8e5` fix(mcp): preserve canonical evidence identity and recalled content
- `d360aa6` fix(retrieval): preserve scoped evidence and graph proof semantics

The user's untracked `mesa_v4_hardering_loop_prompt.md` was preserved and excluded
from all commits. No force operation or main/master merge was performed.

## Push status

**BLOCKED by automatic approval review.** The reviewer did not accept the
attached request's §46 as authorization to export code/history to
`https://github.com/Yasou13/MESA.git`. A normal push of
`fix/v4-hardening-final-correction` was rejected twice; no push occurred.
Explicit confirmation for that destination has been requested. This does not
change the local verification results above.
