from __future__ import annotations

import asyncio

import pytest

from mesa_workers.supervision import WorkerState, WorkerSupervisor


@pytest.mark.asyncio
async def test_worker_start_is_running_and_shutdown_stops_new_claims():
    supervisor = WorkerSupervisor(max_restarts=1)
    claimed = asyncio.Event()

    async def worker():
        await claimed.wait()

    await supervisor.start("queue", worker)
    assert supervisor.readiness()["status"] == "healthy"
    await supervisor.shutdown()
    assert supervisor.readiness()["workers"]["queue"] == WorkerState.STOPPED
    with pytest.raises(RuntimeError):
        await supervisor.start("new-queue", worker)


@pytest.mark.asyncio
async def test_crash_is_restarted_only_within_bound():
    supervisor = WorkerSupervisor(max_restarts=1)
    attempts = 0

    async def unstable():
        nonlocal attempts
        attempts += 1
        raise RuntimeError("synthetic crash")

    await supervisor.start("queue", unstable)
    for _ in range(20):
        if supervisor.readiness()["status"] == "blocked":
            break
        await asyncio.sleep(0)
    status = supervisor.readiness()
    assert attempts == 2
    assert status["workers"]["queue"] == WorkerState.BLOCKED
    assert status["restart_counts"]["queue"] == 1
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_health_init_is_not_ready_when_required_worker_is_blocked(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from mesa_memory.api import server

    async def storage_health():
        return {
            "sqlite": {"status": "healthy"},
            "vector": {"status": "healthy"},
            "graph": {"status": "healthy"},
        }

    monkeypatch.setattr(server.state, "is_ready", True, raising=False)
    monkeypatch.setattr(
        server.state, "dao", SimpleNamespace(health_check=storage_health), raising=False
    )
    monkeypatch.setattr(
        server.state,
        "runtime_profile",
        SimpleNamespace(worker_enabled=True),
        raising=False,
    )
    monkeypatch.setattr(
        server.state,
        "worker_supervisor",
        SimpleNamespace(readiness=lambda: {"status": "blocked"}),
        raising=False,
    )
    with pytest.raises(HTTPException) as rejected:
        await server.health_init()
    assert rejected.value.status_code == 503


@pytest.mark.asyncio
async def test_health_init_allows_intentionally_workerless_api_profile(monkeypatch):
    from types import SimpleNamespace

    from mesa_memory.api import server

    async def storage_health():
        return {
            "sqlite": {"status": "healthy"},
            "vector": {"status": "healthy"},
            "graph": {"status": "healthy"},
        }

    monkeypatch.setattr(server.state, "is_ready", True, raising=False)
    monkeypatch.setattr(
        server.state, "dao", SimpleNamespace(health_check=storage_health), raising=False
    )
    monkeypatch.setattr(
        server.state,
        "runtime_profile",
        SimpleNamespace(worker_enabled=False),
        raising=False,
    )
    monkeypatch.setattr(
        server.state,
        "worker_supervisor",
        SimpleNamespace(readiness=lambda: {"status": "blocked"}),
        raising=False,
    )
    assert (await server.health_init())["status"] == "ready"


@pytest.mark.asyncio
async def test_api_only_readiness_requires_configured_external_worker(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from mesa_memory.api import server

    async def storage_health():
        return {
            "sqlite": {"status": "healthy"},
            "vector": {"status": "healthy"},
            "graph": {"status": "healthy"},
        }

    monkeypatch.setattr(server.state, "is_ready", True, raising=False)
    monkeypatch.setattr(
        server.state, "dao", SimpleNamespace(health_check=storage_health), raising=False
    )
    monkeypatch.setattr(
        server.state,
        "runtime_profile",
        SimpleNamespace(
            worker_enabled=False,
            require_worker_readiness=True,
            storage_root=tmp_path,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        server.state,
        "worker_supervisor",
        SimpleNamespace(readiness=lambda: {"status": "blocked"}),
        raising=False,
    )
    monkeypatch.setattr(server, "worker_is_ready", lambda storage_root: False)
    with pytest.raises(HTTPException) as rejected:
        await server.health_init()
    assert rejected.value.status_code == 503

    monkeypatch.setattr(server, "worker_is_ready", lambda storage_root: True)
    assert (await server.health_init())["status"] == "ready"
