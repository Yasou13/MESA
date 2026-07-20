"""Core regressions for optional adapter import boundaries."""

from __future__ import annotations

import subprocess
import sys


def test_openai_adapter_imports_without_openai_dependency() -> None:
    code = """
import builtins

real_import = builtins.__import__

def block_openai(name, *args, **kwargs):
    if name == 'openai' or name.startswith('openai.'):
        raise ImportError('simulated missing optional dependency')
    return real_import(name, *args, **kwargs)

builtins.__import__ = block_openai
from mesa_memory.adapter.live import OpenAICompatibleAdapter
try:
    OpenAICompatibleAdapter(api_key='test-key')
except RuntimeError as exc:
    assert 'mesa-memory[adapters]' in str(exc)
else:
    raise AssertionError('missing optional dependency must fail on adapter use')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
