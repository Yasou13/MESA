# Contributing to MESA

MESA v3 is the lexical-core compatibility surface. V4 is an unreleased,
versioned full-cognitive runtime and remains `NO-GO` until its release gates
pass. Contributions must preserve that distinction.

## Workflow

1. Fork the repository and create a focused feature branch.
2. Inspect `AGENTS.md` and the current architecture in
   `docs/architecture-v4.md`.
3. Preserve unrelated user/worktree changes.
4. Add or update a regression/contract test for behavioral changes.
5. Run the narrow test first, then the related package and repository gates.
6. Update the canonical documentation and changelog when a public contract
   changes.
7. Open a pull request with behavior, risk, migration and test evidence.

Do not modify the preserved historical reports or root `ARCHITECTURE.md` to
describe v4. Add a current versioned document or ADR instead.

## Required checks

```bash
uv sync --locked --extra dev
uv run ruff check .
uv run mypy mesa_memory mesa_storage mesa_workers mesa_api mesa_client \
  --ignore-missing-imports --explicit-package-bases --follow-imports=skip
uv run pytest -q
uv run pytest -q mesa-benchmark/tests
uv run mypy mesa-benchmark/mesa_benchmark
```

V4 API/storage/retrieval changes must also run the relevant
`tests/test_v4_*.py`, Graph V2 and API-key tests. Benchmark changes must keep
dataset/checksum/evidence validity and may not convert provider errors into
empty successful answers.

## Security and data rules

- Never commit API keys, tokens, `.env`, production data or benchmark result
  bundles.
- V4 authorization is principal → tenant → workspace → dataset → session.
  Agent IDs are not standalone tenant boundaries.
- Tier-3 rejection may not create an active SQL/vector/graph artifact.
- Projection/rollback code must remain idempotent and source-owner aware.
- PageRank is observation-only and cannot quarantine or delete data.
- Migration and recovery tests use copies and new restore roots; they never
  alter production storage in place.

## Pull request evidence

Include the commands and pass/fail counts, affected API/schema versions,
migration/rollback impact and remaining limitations. Do not use
“production-ready” or GO language without the full release evidence described
in `docs/release.md`.
