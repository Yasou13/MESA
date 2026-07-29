# ADR 0012: MemoryDAO repository boundaries

`MemoryDAO` remains the compatibility façade for existing V3/V4 callers, but
it no longer owns the tenant/workspace/dataset hierarchy directly.
`CatalogRepositoryPort` is the first focused boundary: it owns catalog scope
transactions and immutable identity-collision checks while exposing no raw
SQLite connection.

Subsequent extractions must preserve the same shape: a narrow typed port,
one transaction owner per aggregate, compatibility delegation in `MemoryDAO`,
and a repository-level contract test before callers are moved. Candidate next
boundaries are ingestion/mutation, projection recovery, and retrieval; they
must not be extracted as a broad mechanical rewrite.
