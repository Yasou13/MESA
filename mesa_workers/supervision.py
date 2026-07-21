"""Bounded asyncio worker supervision for the WAVE-004 queue boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable


class WorkerState(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    CRASHED = "CRASHED"
    BLOCKED = "BLOCKED"


Runner = Callable[[], Awaitable[None]]


@dataclass
class WorkerStatus:
    state: WorkerState = WorkerState.STOPPED
    restart_count: int = 0
    required: bool = True


class WorkerSupervisor:
    """Observe required worker tasks and retry crashes only within a fixed budget."""

    def __init__(self, *, max_restarts: int = 3) -> None:
        if max_restarts < 0:
            raise ValueError("max_restarts must be non-negative")
        self._max_restarts = max_restarts
        self._stopping = False
        self._runners: dict[str, Runner] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._status: dict[str, WorkerStatus] = {}

    async def start(
        self, name: str, runner: Runner, *, required: bool = True
    ) -> asyncio.Task[None]:
        if self._stopping:
            raise RuntimeError("worker supervisor is stopping")
        if name in self._tasks and not self._tasks[name].done():
            return self._tasks[name]
        status = self._status.setdefault(name, WorkerStatus(required=required))
        status.required = required  # type: ignore[None,var-annotated]
        status.state = WorkerState.STARTING
        self._runners[name] = runner
        import typing  # type: ignore[misc]

        task: asyncio.Task[None] = asyncio.create_task(
            typing.cast(typing.Coroutine[typing.Any, typing.Any, None], runner()),
            name=f"mesa:{name}",
        )
        self._tasks[name] = task
        status.state = WorkerState.RUNNING

        def _on_done(t: asyncio.Task[None], w: str = name) -> None:
            asyncio.create_task(self._observe(w, t))

        task.add_done_callback(_on_done)
        return task

    async def _observe(self, name: str, task: asyncio.Task[None]) -> None:
        status = self._status[name]
        if self._stopping or task.cancelled():
            status.state = WorkerState.STOPPED
            return
        try:
            failure = task.exception()
        except asyncio.CancelledError:
            status.state = WorkerState.STOPPED
            return
        if failure is None:
            status.state = WorkerState.STOPPED
            return
        status.state = WorkerState.CRASHED
        if status.restart_count >= self._max_restarts:
            status.state = WorkerState.BLOCKED
            return
        status.restart_count += 1
        status.state = WorkerState.DEGRADED
        await self.start(name, self._runners[name], required=status.required)

    def readiness(self) -> dict[str, object]:
        required = {
            name: status for name, status in self._status.items() if status.required
        }
        states = {name: status.state.value for name, status in self._status.items()}
        if not required or any(
            status.state is WorkerState.BLOCKED for status in required.values()
        ):
            overall = "blocked"
        elif any(
            status.state is not WorkerState.RUNNING for status in required.values()
        ):
            overall = "degraded"
        else:
            overall = "healthy"
        return {
            "status": overall,
            "workers": states,
            "restart_counts": {
                name: item.restart_count for name, item in self._status.items()
            },
        }

    async def shutdown(self) -> None:
        self._stopping = True
        for status in self._status.values():
            if status.state is WorkerState.RUNNING:
                status.state = WorkerState.STOPPING
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for status in self._status.values():
            status.state = WorkerState.STOPPED
