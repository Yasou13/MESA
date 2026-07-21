"""SEC-003 contracts for rate-limit identity and credential lifecycle."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from starlette.requests import Request

from mesa_memory.api.middleware import check_daily_limit, get_rate_limit_subject

_PRE_SEC003_HEAD = "b2e3f4a5c6d7"


class _RecordingDAO:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def increment_and_check_daily_limit(
        self, subject_id: str, limit: int
    ) -> bool:
        self.calls.append((subject_id, limit))
        return True


def _request(
    app: FastAPI,
    *,
    credential: str = "synthetic-credential-not-for-persistence",
    client_host: str = "198.51.100.19",
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v3/memory/insert",
            "headers": [(b"x-api-key", credential.encode())],
            "query_string": b"agent_id=forged-agent",
            "client": (client_host, 12345),
            "app": app,
        }
    )


def _config(database: Path) -> Config:
    config = Config(str(Path(__file__).parents[1] / "mesa_storage" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database}")
    return config


def test_minute_limit_uses_verified_principal_not_credential_or_agent() -> None:
    app = FastAPI()
    request = _request(app)
    request.state.principal = SimpleNamespace(
        principal_id="principal-rate-a", status="active"
    )

    subject = get_rate_limit_subject(request)

    assert subject == "principal-rate-a"
    assert "credential" not in subject
    assert "forged-agent" not in subject


@pytest.mark.asyncio
async def test_daily_limit_persists_only_verified_principal_and_skips_unauthenticated(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    app = FastAPI()
    dao = _RecordingDAO()
    app.state.dao = dao
    monkeypatch.setenv("MESA_DAILY_REQUEST_LIMIT", "17")

    authenticated = _request(app)
    authenticated.state.principal = SimpleNamespace(
        principal_id="principal-rate-a", status="active"
    )
    await check_daily_limit(authenticated)

    assert dao.calls == [("principal-rate-a", 17)]
    assert "synthetic-credential-not-for-persistence" not in caplog.text

    unauthenticated = _request(app)
    await check_daily_limit(unauthenticated)
    assert dao.calls == [("principal-rate-a", 17)]
    assert get_rate_limit_subject(unauthenticated) == "ip:198.51.100.19"


def test_daily_limit_migration_discards_legacy_credential_rows_and_resets_counter(
    tmp_path: Path,
) -> None:
    database = tmp_path / "daily-limits.db"
    credential = "synthetic-credential-not-for-persistence"
    config = _config(database)
    command.upgrade(config, _PRE_SEC003_HEAD)

    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO daily_limits (agent_id, date, request_count) VALUES (?, ?, ?)",
        (credential, "2026-07-21", 9),
    )
    connection.commit()
    connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(daily_limits)")}
    assert columns == {"subject_id", "date", "request_count"}
    assert connection.execute("SELECT count(*) FROM daily_limits").fetchone()[0] == 0
    backup = tmp_path / "daily-limits-backup.db"
    backup_connection = sqlite3.connect(backup)
    connection.backup(backup_connection)
    backup_connection.close()
    connection.close()

    for copy in (database, backup):
        dump_connection = sqlite3.connect(copy)
        dump = "\n".join(dump_connection.iterdump())
        dump_connection.close()
        assert credential not in dump
