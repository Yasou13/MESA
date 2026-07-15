"""
LLM Tracing Integrations.
Hooks up litellm callbacks to Langfuse or LangSmith if environment variables are set.
"""

import logging
import os
from typing import List

logger = logging.getLogger("MESA_Tracer")


def setup_telemetry_tracing() -> None:
    """Initialize LLM tracing based on environment variables."""
    try:
        import litellm
    except ImportError:
        logger.warning("litellm is not installed. Tracing integration skipped.")
        return

    callbacks: List[str] = []

    # Langfuse Integration
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        logger.info("Enabling Langfuse telemetry via litellm.")
        callbacks.append("langfuse")

    # LangSmith Integration
    if os.getenv("LANGCHAIN_API_KEY") and os.getenv("LANGCHAIN_TRACING_V2") == "true":
        logger.info("Enabling LangSmith telemetry via litellm.")
        callbacks.append("langsmith")

    if callbacks:
        # litellm.success_callback and litellm.failure_callback accept a list of strings
        if not hasattr(litellm, "success_callback"):
            litellm.success_callback = []
        if not hasattr(litellm, "failure_callback"):
            litellm.failure_callback = []

        for cb in callbacks:
            if cb not in litellm.success_callback:
                litellm.success_callback.append(cb)
            if cb not in litellm.failure_callback:
                litellm.failure_callback.append(cb)
