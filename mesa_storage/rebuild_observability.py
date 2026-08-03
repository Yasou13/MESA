"""Strict, content-safe structured logging for the offline rebuild runner."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

_OPERATION_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ERROR_CLASS = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_EVENTS = frozenset(
    {
        "claimed",
        "prepared",
        "replayed",
        "completed",
        "failed",
        "rejected",
        "writer_blocked",
    }
)
_STATES = frozenset(
    {
        "PENDING",
        "CLAIMED",
        "RUNNING",
        "VERIFYING",
        "READY_TO_CUTOVER",
        "COMPLETED",
        "RETRYABLE_FAILED",
        "FINAL_FAILED",
        "CANCELLED",
    }
)


def _safe(value: str | None, pattern: re.Pattern[str]) -> str | None:
    if value is None:
        return None
    return value if pattern.fullmatch(value) else "invalid"


def log_rebuild_event(
    event: str,
    *,
    operation_id: str,
    state: str | None = None,
    generation: str | None = None,
    error_class: str | None = None,
    progress_completed: int | None = None,
    progress_total: int | None = None,
    duration_seconds: float | None = None,
    level: str = "info",
    logger: Any = None,
) -> None:
    """Emit only bounded identifiers, state, counts, duration and error class."""
    if event not in _EVENTS:
        raise ValueError("unknown rebuild log event")
    if state is not None and state not in _STATES:
        raise ValueError("unknown rebuild operation state")
    if level not in {"info", "warning", "error"}:
        raise ValueError("unsupported rebuild log level")
    fields: dict[str, Any] = {
        "rebuild_event": event,
        "operation_id": _safe(operation_id, _OPERATION_ID),
    }
    if state is not None:
        fields["state"] = state
    if generation is not None:
        fields["generation"] = _safe(generation, _GENERATION_ID)
    if error_class is not None:
        fields["error_class"] = _safe(error_class, _ERROR_CLASS)
    if progress_completed is not None:
        fields["progress_completed"] = max(0, int(progress_completed))
    if progress_total is not None:
        fields["progress_total"] = max(0, int(progress_total))
    if duration_seconds is not None:
        fields["duration_seconds"] = max(0.0, round(float(duration_seconds), 3))
    if logger is None:
        print(
            json.dumps(
                {"event": "v4_rebuild_operation", "level": level, **fields},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return
    getattr(logger, level)("v4_rebuild_operation", **fields)
