# ADR 0013: One-way production-layer dependencies

- Status: Accepted

Layers ordered from lowest to highest are `mesa_storage`, `mesa_memory`,
`mesa_workers`, then `mesa_api`/`mesa_mcp`. Imports may point down this list:
API/MCP → workers → memory → storage. Lower layers cannot import a higher
layer. The API and worker startup modules are explicit composition roots and
may assemble the complete runtime; they are the only documented exception.

`scripts/check_layer_imports.py` is a CI ratchet with a negative contract
test. New composition roots require an ADR update and an explicit entry in the
guard, rather than an unreviewed reverse import.
