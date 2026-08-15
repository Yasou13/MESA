"""Runtime startup failures release every acquired storage resource."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from mesa_memory.api import server
from mesa_memory.config import RuntimeProfile, RuntimeProfileConfig


@pytest.mark.asyncio
async def test_api_startup_failure_closes_partial_storage_and_writer_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    runtime = RuntimeProfileConfig(
        profile=RuntimeProfile.COMBINED,
        storage_root=tmp_path,
        load_dotenv=False,
        dotenv_path=None,
        model_enabled=False,
        external_provider_enabled=False,
        api_enabled=True,
        worker_enabled=True,
        require_worker_readiness=False,
    )
    writer_lock = SimpleNamespace(release=MagicMock())
    engine = SimpleNamespace(
        initialize=AsyncMock(
            side_effect=RuntimeError("injected sqlite startup failure")
        ),
        close=AsyncMock(),
    )

    monkeypatch.setattr(server, "load_runtime_profile", lambda: runtime)
    monkeypatch.setattr(server, "load_explicit_dotenv", MagicMock())
    monkeypatch.setattr(server, "_refresh_auth_config", MagicMock())
    monkeypatch.setattr(server, "setup_telemetry_tracing", MagicMock())
    monkeypatch.setattr(
        server, "_acquire_runtime_writer_lock", lambda _runtime: writer_lock
    )
    monkeypatch.setattr(server, "AsyncEngine", lambda *_args, **_kwargs: engine)

    with pytest.raises(RuntimeError, match="injected sqlite startup failure"):
        async with server.lifespan(FastAPI()):
            pass

    engine.close.assert_awaited_once()
    writer_lock.release.assert_called_once()


@pytest.mark.asyncio
async def test_api_request_lifespan_failure_still_closes_storage_and_writer_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    runtime = RuntimeProfileConfig(
        profile=RuntimeProfile.COMBINED,
        storage_root=tmp_path,
        load_dotenv=False,
        dotenv_path=None,
        model_enabled=False,
        external_provider_enabled=False,
        api_enabled=True,
        worker_enabled=True,
        require_worker_readiness=False,
    )
    writer_lock = SimpleNamespace(release=MagicMock())
    engine = SimpleNamespace(close=AsyncMock())
    isolated_state = server.AppState()
    isolated_state.sqlite_engine = engine  # type: ignore[assignment]
    isolated_state.api_key_store = object()  # type: ignore[assignment]

    @asynccontextmanager
    async def initialized_runtime(_app, _runtime):  # type: ignore[no-untyped-def]
        yield

    monkeypatch.setattr(server, "state", isolated_state)
    monkeypatch.setattr(server, "load_runtime_profile", lambda: runtime)
    monkeypatch.setattr(server, "load_explicit_dotenv", MagicMock())
    monkeypatch.setattr(server, "_refresh_auth_config", MagicMock())
    monkeypatch.setattr(server, "setup_telemetry_tracing", MagicMock())
    monkeypatch.setattr(
        server, "_acquire_runtime_writer_lock", lambda _runtime: writer_lock
    )
    monkeypatch.setattr(server, "_runtime_lifespan", initialized_runtime)

    with pytest.raises(RuntimeError, match="injected serving failure"):
        async with server.lifespan(FastAPI()):
            raise RuntimeError("injected serving failure")

    engine.close.assert_awaited_once()
    writer_lock.release.assert_called_once()
    assert not hasattr(isolated_state, "api_key_store")
