# MESA 0.9.5 release notes

MESA 0.9.5 supports point-in-time search over document revisions and improves
local retrieval and ingestion on large stores.

## Versioned documents

Upload each version under the same document ID and supply a timezone-aware
`metadata.valid_from`. MESA orders dated revisions by validity, so uploading
an older revision adds to history while the newest revision stays active. A
search with `valid_at` includes the start of a revision's validity and excludes
its end. Search without a date returns active revisions. HTTP, SDK and MCP
results expose the revision number and validity dates. [Law versions example](getting-started.md#law-versions) ·
[Versioned documents API](api-reference.md#versioned-documents)

In the four-law Turkish temporal fixture, the correct revision ranked first on
all 18 dated and all eight undated questions. The dated queries included five
exact amendment instants and adjacent timestamps. Every undated result came
from an active revision.

## Storage and retrieval

- SQLite keeps the canonical documents and evidence. The persistent FTS5 BM25
  index serves eligible dataset-wide queries and updates in the same transaction
  as revision changes. Dated and narrower queries build a scoped temporary index.
- The lockfile pins LanceDB to 0.41.0. Vector search prefilters eligible rows
  by scope and verifies ownership in SQLite. Startup adds scope keys to older
  vector tables. Batched writes retain one receipt per mutation.
- Whole-document retries resume from saved chunks using the recorded
  chunking plan. BM25 index maintenance uses the committing mutation's rows,
  reducing SQLite writer contention during large ingests.
- In the Round 23 local scale run, a fresh store reached 100,820 committed
  chunks. The warm models-disabled HTTP search median fell from 1,223 ms to
  753 ms. The final deterministic vector lane median was 287 ms on the same
  scale workload.
- Kùzu, the graph worker and the graph search lane were removed. The upgrade
  clears legacy graph bookkeeping. Old `kuzu_db` files are ignored and can
  be removed after verifying backups. [Upgrade details](../CHANGELOG.md)
