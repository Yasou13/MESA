"""Canonical MESA runtime package.

Only this package may compose storage, memory, worker, API, MCP, and dashboard
implementations into a runnable process.  Import ``create_app`` from
``mesa_runtime.app`` so the package does not shadow its ``app`` submodule.
"""

__all__: list[str] = []
