# Deprecation Notice

The `mesa_memory` package is being deprecated and superseded by new modular packages.

**Target Removal:** v0.6.1

## Migration Guide

Please update your codebase to use the new parallel packages:
- `mesa_memory.storage` -> Migrate to `mesa_storage`
- `mesa_memory.api` -> Migrate to `mesa_api`
- `mesa_memory.workers` -> Migrate to `mesa_workers`
- `mesa_memory.mcp` -> Migrate to `mesa_mcp`
