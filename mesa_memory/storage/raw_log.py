import json
from datetime import datetime, timezone

import aiosqlite

from mesa_memory.schema.cmb import CMB


class RawLogStorage:
    def __init__(self, db_path: str = "./storage/raw_log.db"):
        self.db_path = db_path

    async def initialize(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_log (
                    cmb_id                  TEXT PRIMARY KEY,
                    schema_version          INTEGER NOT NULL DEFAULT 1,
                    created_at              TEXT    NOT NULL,
                    content_payload         TEXT    NOT NULL,
                    source                  TEXT    NOT NULL,
                    performative            TEXT    NOT NULL,
                    cat7_focus              REAL    NOT NULL DEFAULT 0.5,
                    cat7_mood_valence       REAL    NOT NULL DEFAULT 0.0,
                    cat7_mood_arousal       REAL    NOT NULL DEFAULT 0.0,
                    prediction_error_score  REAL    NOT NULL DEFAULT 0.0,
                    resource_cost_token_count INTEGER NOT NULL DEFAULT 0,
                    resource_cost_latency_ms  REAL    NOT NULL DEFAULT 0.0,
                    fitness_score           REAL    NOT NULL DEFAULT 0.0,
                    embedding               TEXT    NOT NULL DEFAULT '[]',
                    parent_cmb_id           TEXT    DEFAULT NULL,
                    consolidated            INTEGER NOT NULL DEFAULT 0,
                    tier3_deferred          INTEGER NOT NULL DEFAULT 0,
                    expired_at              TEXT    DEFAULT NULL,
                    invalid_at              TEXT    DEFAULT NULL
                )
            """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_raw_log_active
                ON raw_log(expired_at) WHERE expired_at IS NULL
            """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_raw_log_unconsolidated
                ON raw_log(consolidated) WHERE consolidated = 0 AND expired_at IS NULL
            """
            )
            await db.commit()

    async def insert_cmb(self, cmb: CMB):
        data = cmb.model_dump()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO raw_log (
                    cmb_id, schema_version, created_at, content_payload,
                    source, performative, cat7_focus,
                    cat7_mood_valence, cat7_mood_arousal,
                    prediction_error_score,
                    resource_cost_token_count, resource_cost_latency_ms,
                    fitness_score, embedding, parent_cmb_id, tier3_deferred
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    data["cmb_id"],
                    data["schema_version"],
                    data["created_at"].isoformat(),
                    data["content_payload"],
                    data["source"],
                    data["performative"],
                    data["cat7_focus"],
                    data["cat7_mood"]["valence"],
                    data["cat7_mood"]["arousal"],
                    data["prediction_error_score"],
                    data["resource_cost"]["token_count"],
                    data["resource_cost"]["latency_ms"],
                    data["fitness_score"],
                    json.dumps(data["embedding"]),
                    data["parent_cmb_id"],
                    int(data.get("tier3_deferred", False)),
                ),
            )
            await db.commit()

    async def get_cmb(self, cmb_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM raw_log WHERE cmb_id = ? AND expired_at IS NULL",
                (cmb_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                return dict(row)

    async def fetch_unconsolidated(self, limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM raw_log WHERE consolidated = 0 AND expired_at IS NULL ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def mark_consolidated(self, cmb_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE raw_log SET consolidated = 1 WHERE cmb_id = ? AND expired_at IS NULL",
                (cmb_id,),
            )
            await db.commit()

    async def clear_tier3_deferred(self, cmb_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE raw_log SET tier3_deferred = 0 WHERE cmb_id = ? AND expired_at IS NULL",
                (cmb_id,),
            )
            await db.commit()

    async def soft_delete(self, cmb_id: str):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE raw_log SET expired_at = ? WHERE cmb_id = ? AND expired_at IS NULL",
                (now, cmb_id),
            )
            await db.commit()

    async def fetch_all_active_ids(self) -> list[str]:
        """Return all cmb_ids that are active (not expired). Used by reconciliation."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT cmb_id FROM raw_log WHERE expired_at IS NULL"
            ) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
