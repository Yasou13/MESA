Controlled repairs (3):
1. `tests/test_dao.py`: add missing awaitable `insert_node` fixture method.
2. `tests/test_dao.py`: make neighbor mock use generated tenant-scoped SQLite ID.
3. `mesa_workers/ingestion_worker.py`: retain positional status call when error reason is absent so existing mock contract remains valid.

Additionally, an obsolete source-inspection test now inspects `rollback_purge`, the approved WAVE-002 compensation boundary. All repairs reran their tests and final regression sets.
