# Performance, capacity and known limitations

No v0.8.0 throughput, recall, accuracy, latency or 10–20 GB corpus-capacity result
has been measured in this documentation release. A unit/fixture smoke is not a
production soak, and bounded graph traversal does not guarantee complete recall.

The preserved [2026-08-27 Profile A report](../SOAK_TEST_REPORT_20260827.md)
describes an interrupted 6.47-hour run at commit
`b2ffc0bac6b1b417f53bc86f8e69e7b490fa88a5`, with 4,044 operations and growing RSS.
It is not a completed 24-hour release gate. Its future RAM-stabilization suggestion
is a hypothesis, not a measured repair. Old benchmark pages retain their original
conditions and must not be shown as current v0.8.0 proof.

`VectorEngine` maintenance defaults to 128 mutations and cleans old versions
older than seven days. Compaction, deduplication and executor cleanup mitigate
growth; they do not establish bounded long-soak RSS for every workload. Watch
fragments, version metadata, RSS, disk, mutation latency and maintenance failure
counts. Retained projection generations/backups are not automatically deleted.

Queue admission defaults are 50,000 pending records / 2 GiB, with tenant-aware
bounds and bounded in-flight/retry capacity. They are safety limits, not tested
throughput commitments. See [configuration](configuration.md). SQL writers are
serialized; one combined owner is required per storage root. Advisory local
locking is not a distributed/NFS coordinator.

## Compatibility evidence

| Area | Declared/configured | Current audit evidence |
|---|---|---|
| Python | >=3.10; CI matrix 3.10–3.13 | Local 3.10.12; other versions require CI |
| OS | No full OS support matrix declared | Local Linux; CI Ubuntu 24.04; macOS/Windows not exercised here |
| Dependencies | Ranges in pyproject, exact graph in uv.lock | Locked graph check and installed compatibility check |
| Storage | SQLite FTS5, LanceDB, Kùzu | Local disposable contracts; production-size gates pending |
| Local/external models | Explicit configurable identity | No new real-model quality measurement |
| Docker | Non-root full-cognitive image and two Compose topologies | CI/external deployment gates required |

## Unsupported or constrained

Bitemporal queries, associative PPR, human legal certification, distributed
writers/hosted storage, Ladybug backend, online or tenant/dataset-scoped rebuild,
automatic retained-generation cleanup and guaranteed citation correctness are
not current Core guarantees. V4 retrieval performs no answer generation.
CrossEncoder/legacy REBEL controls concern V3 paths, not canonical V4 ranking
or extraction. Adaptive planning is opt-in, bounded and not recursive.
