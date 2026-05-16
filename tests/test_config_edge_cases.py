import os
from unittest.mock import MagicMock, mock_open, patch

from mesa_memory.config import (
    _SAFE_MODE_RAM_BYTES,
    MesaConfig,
    _read_cgroup_ram_limit,
    _read_env_ram_limit,
    calculate_dynamic_limits,
)


def test_read_env_ram_limit_missing():
    with patch.dict(os.environ, {}, clear=True):
        assert _read_env_ram_limit() is None


def test_read_env_ram_limit_invalid():
    with patch.dict(os.environ, {"MESA_MAX_RAM_MB": "not-an-int"}):
        assert _read_env_ram_limit() is None


def test_read_env_ram_limit_zero_or_negative():
    with patch.dict(os.environ, {"MESA_MAX_RAM_MB": "0"}):
        assert _read_env_ram_limit() is None
    with patch.dict(os.environ, {"MESA_MAX_RAM_MB": "-5"}):
        assert _read_env_ram_limit() is None


def test_read_env_ram_limit_valid():
    with patch.dict(os.environ, {"MESA_MAX_RAM_MB": "100"}):
        assert _read_env_ram_limit() == 100 * 1024 * 1024


def test_read_cgroup_ram_limit_max_sentinel():
    m = mock_open(read_data="max\n")
    with patch("builtins.open", m):
        assert _read_cgroup_ram_limit() is None


def test_read_cgroup_ram_limit_valid():
    m = mock_open(read_data="1048576\n")  # 1 MB
    with patch("builtins.open", m):
        assert _read_cgroup_ram_limit() == 1048576


def test_read_cgroup_ram_limit_invalid():
    m = mock_open(read_data="not-a-number\n")
    with patch("builtins.open", m):
        assert _read_cgroup_ram_limit() is None


def test_read_cgroup_ram_limit_implausible():
    m = mock_open(read_data=str(1 << 62) + "\n")
    with patch("builtins.open", m):
        assert _read_cgroup_ram_limit() is None


def test_calculate_dynamic_limits_safe_mode():
    config = MesaConfig()
    with patch(
        "mesa_memory.config.psutil.virtual_memory", side_effect=Exception("mock err")
    ):
        with patch("mesa_memory.config._read_env_ram_limit", return_value=None):
            with patch("mesa_memory.config._read_cgroup_ram_limit", return_value=None):
                res = calculate_dynamic_limits(config)
                assert res.lancedb_memory_limit_bytes == int(
                    _SAFE_MODE_RAM_BYTES * config.ram_allocation_fraction
                )


def test_calculate_dynamic_limits_psutil():
    config = MesaConfig()
    mock_vm = MagicMock()
    mock_vm.total = 2000000000
    with patch("mesa_memory.config.psutil.virtual_memory", return_value=mock_vm):
        res = calculate_dynamic_limits(config)
        assert res.lancedb_memory_limit_bytes == int(
            2000000000 * config.ram_allocation_fraction
        )
