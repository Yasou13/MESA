# Compatibility namespace notice

`mesa_memory` is still the supported runtime/orchestration package. It is not
scheduled for removal in v0.6.1; that historical target is obsolete.

Public boundaries that already have dedicated packages should be imported from
their versioned homes:

- REST routers and schemas: `mesa_api`
- sync/async SDKs: `mesa_client`
- storage engines and DAO: `mesa_storage`
- workers/projectors: `mesa_workers`
- MCP integration: `mesa_mcp`

`mesa_memory` continues to own runtime entrypoints, configuration, model
adapters, validation, retrieval orchestration, security and observability.
Moving another module out of this namespace requires a new deprecation window,
release note and compatibility tests; no implicit removal version is active.
