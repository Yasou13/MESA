from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .models import DashboardJob, DatasetOperation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRegistry:
    """Small durable registry; benchmark evidence remains in native artifacts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    eta_seconds INTEGER,
                    eta_confidence TEXT NOT NULL DEFAULT 'düşük',
                    current_task TEXT,
                    archived INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    plan_path TEXT NOT NULL,
                    event_path TEXT NOT NULL,
                    pid INTEGER,
                    result_json TEXT
                )
                """)
            existing = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(dashboard_jobs)"
                ).fetchall()
            }
            additions = {
                "queue_position": "INTEGER",
                "started_at": "TEXT",
                "active_elapsed_seconds": "REAL NOT NULL DEFAULT 0",
                "time_limit_minutes": "REAL",
                "pause_reason": "TEXT",
                "progress_json": "TEXT",
                "provisional_json": "TEXT",
            }
            for name, declaration in additions.items():
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE dashboard_jobs ADD COLUMN {name} {declaration}"
                    )
            connection.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS dataset_operations (
                    id TEXT PRIMARY KEY,
                    dataset_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    pid INTEGER,
                    error TEXT,
                    result_json TEXT
                )
                """)

    def create(self, plan: dict[str, Any]) -> DashboardJob:
        root = Path(plan["tasks"][0]["config"]).parents[1]
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dashboard_jobs (
                    id, name, profile, status, created_at, updated_at,
                    plan_path, event_path, time_limit_minutes
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    plan["id"],
                    plan["name"],
                    plan["profile"],
                    now,
                    now,
                    str(root / "plan.json"),
                    str(root / "events.jsonl"),
                    plan.get("request", {}).get("time_limit_minutes"),
                ),
            )
        job = self.get(plan["id"])
        assert job is not None
        return job

    def update(self, job_id: str, **values: Any) -> DashboardJob:
        allowed = {
            "status",
            "progress",
            "eta_seconds",
            "eta_confidence",
            "current_task",
            "archived",
            "error",
            "pid",
            "result",
            "queue_position",
            "started_at",
            "active_elapsed_seconds",
            "time_limit_minutes",
            "pause_reason",
            "progress_snapshot",
            "provisional_result",
        }
        unknown = set(values).difference(allowed)
        if unknown:
            raise ValueError(f"unsupported registry fields: {sorted(unknown)}")
        normalized = dict(values)
        if "archived" in normalized:
            normalized["archived"] = int(bool(normalized["archived"]))
        if "result" in normalized:
            normalized["result_json"] = json.dumps(
                normalized.pop("result"), ensure_ascii=False
            )
        if "progress_snapshot" in normalized:
            value = normalized.pop("progress_snapshot")
            normalized["progress_json"] = (
                json.dumps(value, ensure_ascii=False) if value is not None else None
            )
        if "provisional_result" in normalized:
            value = normalized.pop("provisional_result")
            normalized["provisional_json"] = (
                json.dumps(value, ensure_ascii=False) if value is not None else None
            )
        normalized["updated_at"] = _now()
        assignments = ", ".join(f"{name} = ?" for name in normalized)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE dashboard_jobs SET {assignments} WHERE id = ?",  # noqa: S608
                (*normalized.values(), job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> DashboardJob | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dashboard_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._to_model(row) if row else None

    def list(self, *, include_archived: bool = False) -> list[DashboardJob]:
        query = "SELECT * FROM dashboard_jobs"
        if not include_archived:
            query += " WHERE archived = 0"
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [self._to_model(row) for row in rows]

    def get_setting(self, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM dashboard_settings WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["value_json"]) if row else None

    def set_setting(self, key: str, value: dict[str, Any]) -> None:
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dashboard_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), now),
            )

    def delete_setting(self, key: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM dashboard_settings WHERE key = ?", (key,))

    def create_dataset_operation(
        self, operation_id: str, dataset_id: str
    ) -> DatasetOperation:
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dataset_operations (
                    id, dataset_id, status, created_at, updated_at
                ) VALUES (?, ?, 'queued', ?, ?)
                """,
                (operation_id, dataset_id, now, now),
            )
        operation = self.get_dataset_operation(operation_id)
        assert operation is not None
        return operation

    def update_dataset_operation(
        self, operation_id: str, **values: Any
    ) -> DatasetOperation:
        allowed = {"status", "progress", "pid", "error", "result"}
        unknown = set(values).difference(allowed)
        if unknown:
            raise ValueError(f"unsupported operation fields: {sorted(unknown)}")
        normalized = dict(values)
        if "result" in normalized:
            normalized["result_json"] = json.dumps(
                normalized.pop("result"), ensure_ascii=False
            )
        normalized["updated_at"] = _now()
        assignments = ", ".join(f"{name} = ?" for name in normalized)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE dataset_operations SET {assignments} WHERE id = ?",  # noqa: S608
                (*normalized.values(), operation_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(operation_id)
        operation = self.get_dataset_operation(operation_id)
        assert operation is not None
        return operation

    def get_dataset_operation(self, operation_id: str) -> DatasetOperation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dataset_operations WHERE id = ?", (operation_id,)
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        result_json = value.pop("result_json")
        value["result"] = json.loads(result_json) if result_json else None
        return DatasetOperation.model_validate(value)

    def list_dataset_operations(self) -> Sequence[DatasetOperation]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM dataset_operations ORDER BY created_at DESC"
            ).fetchall()
        return [
            operation
            for row in rows
            if (operation := self.get_dataset_operation(str(row["id"]))) is not None
        ]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> DashboardJob:
        value = dict(row)
        value["archived"] = bool(value["archived"])
        result_json = value.pop("result_json")
        value["result"] = json.loads(result_json) if result_json else None
        progress_json = value.pop("progress_json", None)
        value["progress_snapshot"] = (
            json.loads(progress_json) if progress_json else None
        )
        provisional_json = value.pop("provisional_json", None)
        value["provisional_result"] = (
            json.loads(provisional_json) if provisional_json else None
        )
        return DashboardJob.model_validate(value)
