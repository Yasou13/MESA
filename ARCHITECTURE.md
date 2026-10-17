# MESA architecture

The maintained design is [MESA Core V4 architecture](docs/architecture-v4.md).
Start there for module ownership, storage, authorization, ingestion and recovery.

| Question | Canonical document |
|---|---|
| Which module owns a behavior? | [Architecture and module map](docs/architecture-v4.md) |
| How do source revisions and mutations progress? | [Concepts and ingestion](docs/concepts-and-ingestion.md) |
| How are evidence and relevance computed? | [Retrieval and graph](docs/retrieval-and-graph.md) |
| What can a client call? | [API and SDK](docs/api-reference.md), [MCP](README_MCP.md) |
| How is storage operated and recovered? | [Operations](docs/RUNBOOK.md), [projection rebuild](docs/v4-rebuild-runbook.md) |
| Why were these choices made? | [Architecture decisions](docs/adr/README.md) |

V4 uses SQLite as canonical authority and LanceDB/Kùzu as derived projections,
with one combined storage owner per root. V3 is a separate compatibility surface.
The API version (`v3`/`v4`) and package version (`0.8.0`) identify different things.

The obsolete V3 whitepaper has been removed from the working tree. Previous
versions remain in Git history; the ADRs explain the decisions that superseded
Saga, agent-as-tenant, alpha-reranking and PageRank-quarantine assumptions for V4.

See the [documentation index](docs/README.md) for all maintained guides.
