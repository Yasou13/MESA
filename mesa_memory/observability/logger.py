"""Process-wide, vendor-neutral structured logging configuration."""

from __future__ import annotations

import logging
import os
import re
import sys
import traceback
from collections.abc import Mapping
from typing import Any

import structlog

_REDACTED = "[REDACTED]"
_ALLOWED_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "claim_token",
    "content",
    "cookie",
    "credential",
    "credentials",
    "headers",
    "metadata",
    "password",
    "payload",
    "prompt",
    "query",
    "raw",
    "raw_log",
    "secret",
    "token",
}
_SAFE_TOKEN_KEYS = {
    "input_tokens",
    "max_tokens",
    "output_tokens",
    "token_count",
    "token_length",
    "tokens",
}
_SECRET_KEY_SUFFIXES = (
    "_api_key",
    "_credential",
    "_credentials",
    "_password",
    "_secret",
    "_token",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def _resolve_level(explicit: int | str | None) -> int:
    if isinstance(explicit, int):
        return explicit
    raw = str(explicit or os.getenv("MESA_LOG_LEVEL", "INFO")).upper()
    try:
        return _ALLOWED_LEVELS[raw]
    except KeyError as exc:
        allowed = ", ".join(_ALLOWED_LEVELS)
        raise RuntimeError(f"MESA_LOG_LEVEL must be one of: {allowed}") from exc


def _resolve_format(explicit_json: bool | None) -> str:
    if explicit_json is not None:
        return "json" if explicit_json else "console"
    raw = os.getenv("MESA_LOG_FORMAT", "json").lower()
    if raw not in {"json", "console"}:
        raise RuntimeError("MESA_LOG_FORMAT must be one of: json, console")
    return raw


def _redact_string(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_REDACTED, redacted)
    return redacted


def _is_forbidden_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _SAFE_TOKEN_KEYS:
        return False
    return normalized in _FORBIDDEN_KEYS or normalized.endswith(_SECRET_KEY_SUFFIXES)


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_forbidden_key(key):
        return _REDACTED
    if isinstance(value, BaseException):
        return type(value).__name__
    if isinstance(value, Mapping):
        return {str(k): _redact_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _sanitize_positional_arguments(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    positional = event_dict.get("positional_args")
    if isinstance(positional, Mapping):
        event_dict["positional_args"] = {
            key: _redact_value(value, key=str(key)) for key, value in positional.items()
        }
    elif isinstance(positional, (tuple, list)):
        event_dict["positional_args"] = tuple(
            _redact_value(value) for value in positional
        )
    return event_dict


def _safe_exception_info(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    exc_info = event_dict.pop("exc_info", None)
    record = event_dict.get("_record")
    if not exc_info and isinstance(record, logging.LogRecord):
        exc_info = record.exc_info
    if exc_info is True:
        exc_info = sys.exc_info()
    if isinstance(exc_info, tuple) and len(exc_info) == 3 and exc_info[0] is not None:
        exc_type, _value, tb = exc_info
        event_dict.setdefault("exception_type", exc_type.__name__)
        event_dict["stack_frames"] = [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(tb)[-12:]
        ]
    event_dict.pop("exception", None)
    event_dict.pop("stack", None)
    return event_dict


def _add_static_fields(role: str) -> Any:
    def processor(
        _logger: Any, _method_name: str, event_dict: dict[str, Any]
    ) -> dict[str, Any]:
        event_dict.setdefault("schema_version", 1)
        event_dict.setdefault("service", "mesa")
        event_dict.setdefault("role", role)
        return event_dict

    return processor


def _redact_event(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    return {
        str(key): _redact_value(value, key=str(key))
        for key, value in event_dict.items()
    }


class _SafeLogRecordFilter(logging.Filter):
    """Remove exception values before stdlib performs %-style interpolation."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, Mapping):
            record.args = {
                key: _redact_value(value, key=str(key))
                for key, value in record.args.items()
            }
        elif isinstance(record.args, tuple):
            record.args = tuple(_redact_value(value) for value in record.args)
        if isinstance(record.msg, str):
            record.msg = _redact_string(record.msg)
        record.exc_text = None
        return True


def setup_logging(
    json_logs: bool | None = None,
    log_level: int | str | None = None,
    *,
    role: str | None = None,
) -> None:
    """Configure one JSON/console stdout pipeline for stdlib and structlog."""

    level = _resolve_level(log_level)
    output_format = _resolve_format(json_logs)
    process_role = role or os.getenv("MESA_PROCESS_ROLE") or "application"

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _sanitize_positional_arguments,
        structlog.stdlib.PositionalArgumentsFormatter(),
        _safe_exception_info,
        _add_static_fields(process_role),
        _redact_event,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if output_format == "json"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.addFilter(_SafeLogRecordFilter())
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)

    for logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(level)
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = True
    access_logger.setLevel(logging.WARNING)

    for logger_name in ("httpx", "httpcore", "qdrant_client"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
