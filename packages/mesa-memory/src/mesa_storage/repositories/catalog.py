"""Deprecated compatibility alias for :mod:`mesa_storage.catalog_store`."""

from __future__ import annotations

import sys
import warnings
from importlib import import_module

warnings.warn(
    "mesa_storage.repositories.catalog is deprecated; "
    "import mesa_storage.catalog_store instead",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = import_module("mesa_storage.catalog_store")
