#!/usr/bin/env python3
"""Deprecated launcher for the opt-in canonical Showcase demo mode."""

from __future__ import annotations

import os
import warnings

import uvicorn


def main() -> None:
    warnings.warn(
        "scripts/run_demo_rag.py is deprecated; configure mesa_runtime directly",
        DeprecationWarning,
        stacklevel=2,
    )
    os.environ.setdefault("MESA_RUNTIME_PROFILE", "api-only")
    os.environ.setdefault("MESA_ENVIRONMENT", "development")
    os.environ.setdefault("MESA_SHOWCASE_DEMO_ENABLED", "true")
    os.environ.setdefault("MESA_MODEL_ENABLED", "true")
    os.environ.setdefault("MESA_EXTERNAL_PROVIDER_ENABLED", "true")
    uvicorn.run(
        "mesa_runtime.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("MESA_PORT", "8000")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
