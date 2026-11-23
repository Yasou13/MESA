"""
Phase 1.1 Verification: Split-Brain Elimination — Storage Unification Tests.

Proves that:
  1. The deleted ``mesa_memory.storage`` module is unreachable at import time.
  2. No residual coupling exists between production code and the deleted
     StorageFacade / RawLogStorage / VectorStorage / NetworkXProvider.

asyncio_mode = strict → every async test requires explicit @pytest.mark.asyncio.
"""

import importlib
import sys

import pytest

# ===================================================================
# TEST 1: Old storage module is unreachable
# ===================================================================


class TestStorageFacadeDeleted:
    """Verify the legacy mesa_memory.storage module is physically gone."""

    def test_import_mesa_memory_storage_raises_import_error(self):
        """Importing mesa_memory.storage must raise ImportError or
        ModuleNotFoundError — the directory has been deleted."""
        # Ensure any stale __pycache__ doesn't mask the deletion
        for key in list(sys.modules.keys()):
            if key.startswith("mesa_memory.storage"):
                del sys.modules[key]

        with pytest.raises((ImportError, ModuleNotFoundError)):
            importlib.import_module("mesa_memory.storage")

    def test_import_storage_facade_raises_import_error(self):
        """StorageFacade class is unreachable."""
        for key in list(sys.modules.keys()):
            if key.startswith("mesa_memory.storage"):
                del sys.modules[key]

        with pytest.raises((ImportError, ModuleNotFoundError)):
            from mesa_memory.storage import StorageFacade  # noqa: F401

    def test_import_raw_log_storage_raises_import_error(self):
        """RawLogStorage is unreachable."""
        for key in list(sys.modules.keys()):
            if key.startswith("mesa_memory.storage"):
                del sys.modules[key]

        with pytest.raises((ImportError, ModuleNotFoundError)):
            from mesa_memory.storage.raw_log import RawLogStorage  # noqa: F401

    def test_import_vector_storage_raises_import_error(self):
        """VectorStorage is unreachable."""
        for key in list(sys.modules.keys()):
            if key.startswith("mesa_memory.storage"):
                del sys.modules[key]

        with pytest.raises((ImportError, ModuleNotFoundError)):
            from mesa_memory.storage.vector_index import VectorStorage  # noqa: F401

    def test_import_networkx_provider_raises_import_error(self):
        """NetworkXProvider is unreachable."""
        for key in list(sys.modules.keys()):
            if key.startswith("mesa_memory.storage"):
                del sys.modules[key]

        with pytest.raises((ImportError, ModuleNotFoundError)):
            from mesa_memory.storage.graph.networkx_provider import (
                NetworkXProvider,  # noqa: F401
            )


# ===================================================================
# TEST 2: No production module imports from mesa_memory.storage
# ===================================================================


class TestNoProductionImportsFromDeletedStorage:
    """Static analysis: verify no production module has a live import
    from the deleted ``mesa_memory.storage`` package."""

    PRODUCTION_MODULES = [
        "mesa_workers.ingestion_worker",
        "mesa_memory.api.server",
        "mesa_storage.dao",
    ]

    @pytest.mark.parametrize("module_name", PRODUCTION_MODULES)
    def test_no_storage_facade_import(self, module_name: str):
        """Module must not import from mesa_memory.storage."""
        import inspect

        mod = importlib.import_module(module_name)
        source = inspect.getsource(mod)

        # Must not contain a live import of StorageFacade
        assert "from mesa_memory.storage import" not in source, (
            f"{module_name} still imports from the deleted mesa_memory.storage package"
        )
        assert "import mesa_memory.storage" not in source, (
            f"{module_name} still imports the deleted mesa_memory.storage package"
        )


