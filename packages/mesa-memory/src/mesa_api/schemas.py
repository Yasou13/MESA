"""Deprecated compatibility alias for :mod:`mesa_contracts.v3`."""

from __future__ import annotations

import sys
import warnings
from importlib import import_module

warnings.warn(
    "mesa_api.schemas is deprecated; import mesa_contracts.v3 instead",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = import_module("mesa_contracts.v3")
