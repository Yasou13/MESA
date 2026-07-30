"""Compatibility alias for :mod:`mesa_contracts.validation`."""

from __future__ import annotations

import sys
import warnings
from importlib import import_module

warnings.warn(
    "mesa_memory.security.input_validation is deprecated; "
    "import mesa_contracts.validation instead",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = import_module("mesa_contracts.validation")
