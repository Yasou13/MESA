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
    # LiteLLM's import path loads a cwd-discovered ``.env`` whenever its mode
    # is DEV. MESA owns dotenv admission explicitly, so fence the import from
    # mutating process configuration behind our runtime-profile checks.
    previous_litellm_mode = os.environ.get("LITELLM_MODE")
    os.environ["LITELLM_MODE"] = "PRODUCTION"
    try:
        try:
            import litellm
        except ImportError:
            logger.warning("litellm is not installed. Tracing integration skipped.")
            return
    finally:
        if previous_litellm_mode is None:
            os.environ.pop("LITELLM_MODE", None)
        else:
            os.environ["LITELLM_MODE"] = previous_litellm_mode

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
