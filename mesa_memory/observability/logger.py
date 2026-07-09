import logging
import sys
from typing import Any

import structlog

_logging_configured = False


def setup_logging(json_logs: bool = True, log_level: int = logging.INFO) -> None:
    """
    Configures structlog to intercept standard library logging and format it as JSON.
    Also enables ContextVars for tracking agent_id and session_id across async boundaries.
    """
    global _logging_configured
    if _logging_configured:
        return

    # 1. Configure standard library logging root logger
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # 2. Configure structlog
    processors: list[Any] = [
        # Add timestamp and log level
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        # Add contextvars (agent_id, session_id)
        structlog.contextvars.merge_contextvars,
        # Perform %-style formatting
        structlog.stdlib.PositionalArgumentsFormatter(),
        # If the log message is a dict, merge it into the event
        structlog.processors.EventRenamer("event"),
        # Format exceptions
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # Convert to a dict to be formatted by the renderer
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 3. Configure the actual formatter
    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.contextvars.merge_contextvars,
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # 4. Apply formatter to the root handler
    handler = logging.getLogger().handlers[0]
    handler.setFormatter(formatter)

    # 5. Suppress overly verbose third-party logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("qdrant_client").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _logging_configured = True
