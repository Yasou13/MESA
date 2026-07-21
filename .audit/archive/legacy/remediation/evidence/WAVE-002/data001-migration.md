# DATA-001 additive migration

`c4f1a8e2d9b0_add_purge_journal.py` adds `nodes.purge_id`, `purge_journal`, and additive indexes. It contains no delete, reset, data rewrite or production execution. `initialize_schema()` was executed twice against a disposable SQLite database below `/storage/mesa-lab/storage/WAVE-002/`; Alembic completed both invocations successfully.
