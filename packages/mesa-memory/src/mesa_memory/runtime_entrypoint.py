"""Compatibility wrapper for :mod:`mesa_runtime.cli`.

This module remains executable through MESA 0.9 so existing containers and
operator commands keep their current entrypoint.
"""

from __future__ import annotations

import sys
import warnings
from importlib import import_module

warnings.warn(
    "mesa_memory.runtime_entrypoint is deprecated; use mesa_runtime.cli",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":
    import_module("mesa_runtime.cli").main()
else:
    # Preserve monkeypatch/import behaviour for operators and downstream tests
    # that still use the old module path.
    sys.modules[__name__] = import_module("mesa_runtime.cli")
