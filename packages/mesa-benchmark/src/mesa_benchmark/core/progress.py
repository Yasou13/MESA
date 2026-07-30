"""Structured progress events and cooperative benchmark controls."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProgressPhase = Literal[
    "preflight",
    "setup",
    "purge",
    "ingest",
    "retrieval",
    "generation",
    "evaluation",
    "reporting",
    "verification",
    "control",
]


class ProgressEvent(BaseModel):
    """Stable event envelope consumed by CLI and dashboard clients."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    sequence: int = Field(ge=1)
    timestamp: str
    run_id: str
    phase: ProgressPhase
    status: str
    message: str = ""
    iteration: int | None = None
    scenario_index: int | None = None
    scenario_total: int | None = None
    context_index: int | None = None
    context_total: int | None = None
    question_index: int | None = None
    question_total: int | None = None
    scenario_id: str | None = None
    question_id: str | None = None
    elapsed_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkControlRequested(RuntimeError):
    """Raised at a safe checkpoint when the dashboard requests a control action."""

    def __init__(self, action: Literal["pause", "cancel"]) -> None:
        super().__init__(action)
        self.action = action


class ProgressSink:
    """Append-only JSONL progress emitter with a dashboard control channel."""

    def __init__(
        self,
        run_id: str,
        *,
        event_file: str | Path | None = None,
        control_file: str | Path | None = None,
    ) -> None:
        self.run_id = run_id
        configured_event = event_file or os.environ.get("MESA_BENCHMARK_EVENT_FILE")
        configured_control = control_file or os.environ.get(
            "MESA_BENCHMARK_CONTROL_FILE"
        )
        self.event_file = Path(configured_event) if configured_event else None
        self.control_file = Path(configured_control) if configured_control else None
        self._sequence = 0
        self._lock = threading.Lock()

    def emit(
        self,
        phase: ProgressPhase,
        status: str,
        *,
        message: str = "",
        **values: Any,
    ) -> ProgressEvent:
        with self._lock:
            self._sequence += 1
            event = ProgressEvent(
                sequence=self._sequence,
                timestamp=datetime.now(timezone.utc).isoformat(),
                run_id=self.run_id,
                phase=phase,
                status=status,
                message=message,
                **values,
            )
            if self.event_file is not None:
                self.event_file.parent.mkdir(parents=True, exist_ok=True)
                with self.event_file.open("a", encoding="utf-8") as handle:
                    handle.write(event.model_dump_json() + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            return event

    def check_control(self) -> None:
        if self.control_file is None or not self.control_file.exists():
            return
        try:
            value = json.loads(self.control_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        action = value.get("action")
        if action in {"pause", "cancel"}:
            raise BenchmarkControlRequested(action)
