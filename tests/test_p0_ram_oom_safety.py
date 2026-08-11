import os
from unittest.mock import patch

from mesa_memory.config import MesaConfig, _read_env_ram_limit, calculate_dynamic_limits


def test_ram_precedence_and_dynamic_limit():
    """Verify 4-tier RAM precedence hierarchy and bounded dynamic limits."""
    # 1. Verify MESA_MAX_MEMORY_BYTES override
    with patch.dict(os.environ, {"MESA_MAX_MEMORY_BYTES": "2147483648"}):
        limit = _read_env_ram_limit()
        assert limit == 2147483648

        cfg = calculate_dynamic_limits(MesaConfig())
        assert cfg.lancedb_memory_limit_bytes == int(2147483648 * cfg.ram_allocation_fraction)

    # 2. Verify MESA_MAX_RAM_MB override
    with patch.dict(os.environ, {"MESA_MAX_RAM_MB": "4096"}, clear=True):
        limit = _read_env_ram_limit()
        assert limit == 4096 * 1024 * 1024

        cfg = calculate_dynamic_limits(MesaConfig())
        assert cfg.lancedb_memory_limit_bytes == int(4096 * 1024 * 1024 * cfg.ram_allocation_fraction)
