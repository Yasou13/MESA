# Testing and developer workflow

Install the declared environment with `uv sync --locked --extra dev`, then run
`uv pip check`. Use [CONTRIBUTING](../CONTRIBUTING.md) and [architecture](architecture-v4.md)
for module boundaries and documentation requirements.

```bash
uv lock --check
uv run ruff check .
uv run mypy mesa_memory mesa_storage mesa_workers mesa_api mesa_client \
  --ignore-missing-imports --explicit-package-bases --follow-imports=skip
make test-local
```

`test-local` selects `not optional_provider and not optional_mcp and not live_external`.
For the complete installed contract environment use `dev` and `adapters`, then
`make test-all`. Optional provider/MCP tests use mocks; live-external tests require
explicit external enablement. Do not run official TEST or production calls as
documentation smoke. Historical unsafe proof scripts are not release commands.

Focused tests cover V4 API/SDK, revision/ownership, projection fencing, provider
identity, vector representation, graph provenance, temporal/scope filtering,
recovery and bounded query planning. Start with the affected contract, then run
the established broader gates. Tests must not turn backend errors into empty
successful answers or skip failing security checks.

Lightweight authored-document checks:

```bash
.venv/bin/python scripts/check_documentation.py
```

This checks current first-party prose links/anchors and supported code-fence
syntax. Historical reports/ADRs are classified separately; links to absent
user-deleted history are reported as historical blockers. No MkDocs/Sphinx site
is configured. Dashboard build and executable quickstart are separate checks.
A static parse is not an executed example; each smoke report must name real,
mocked, skipped or unavailable backend evidence.

Build artifacts with `uv build --wheel`; inspect metadata, migration data and
fresh imports. CI coverage floor is 82 including SDK, MCP service floor is 55.
Legacy full-tree Black/Mypy debt is reported separately; do not call that debt
fixed because the narrower production checks passed.
