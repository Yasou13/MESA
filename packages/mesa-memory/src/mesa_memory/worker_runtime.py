"""Compatibility alias for :mod:`mesa_runtime.worker`."""

from __future__ import annotations

import sys
import warnings
from importlib import import_module

warnings.warn(
    "mesa_memory.worker_runtime is deprecated; use mesa_runtime.worker",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":
    import_module("mesa_runtime.worker").main()
else:
    sys.modules[__name__] = import_module("mesa_runtime.worker")
