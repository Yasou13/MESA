"""Compatibility import for the canonical :mod:`mesa_runtime.app` module.

The historic import path remains available through MESA 0.9.  The module is
aliased, rather than copied, so callers that patch legacy module attributes
continue to affect the canonical runtime during the compatibility window.
"""

from __future__ import annotations

import sys
import warnings
from importlib import import_module

warnings.warn(
    "mesa_memory.api.server is deprecated; import mesa_runtime.app instead",
    DeprecationWarning,
    stacklevel=2,
)

# ``import mesa_memory.api.server as server`` must expose the same mutable
# module globals as ``mesa_runtime.app`` for backwards-compatible monkeypatches.
_runtime_module = import_module("mesa_runtime.app")
sys.modules[__name__] = _runtime_module
