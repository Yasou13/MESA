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


def test_ollama_llm_missing_extra_message() -> None:
    code = """
import builtins
real_import = builtins.__import__

def block_ollama(name, *args, **kwargs):
    if name == 'ollama' or name.startswith('ollama.'):
        raise ImportError('simulated missing ollama')
    return real_import(name, *args, **kwargs)

builtins.__import__ = block_ollama
from mesa_memory.adapter.ollama import OllamaAdapter
try:
    OllamaAdapter()
except RuntimeError as exc:
    assert 'pip install "mesa-memory[adapters]"' in str(exc), f"Wrong message: {exc}"
else:
    raise AssertionError("must raise on missing dependency")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_ollama_and_openai_embedding_missing_extra_message() -> None:
    code = """
import builtins
real_import = builtins.__import__

def block_openai(name, *args, **kwargs):
    if name == 'openai' or name.startswith('openai.'):
        raise ImportError('simulated missing openai')
    return real_import(name, *args, **kwargs)

builtins.__import__ = block_openai
from mesa_memory.embedding.service import _OpenAICompatibleEmbeddingBackend, EmbeddingIdentity, EmbeddingCompositionError

# Test Ollama embedding
try:
    _OpenAICompatibleEmbeddingBackend(EmbeddingIdentity(provider="ollama", model="bge-m3", dimension=1024))
except EmbeddingCompositionError as exc:
    assert 'pip install "mesa-memory[adapters]"' in str(exc), f"Wrong message: {exc}"
else:
    raise AssertionError("must raise on missing dependency")

# Test OpenAI-compatible embedding
try:
    _OpenAICompatibleEmbeddingBackend(EmbeddingIdentity(provider="openai_compatible", model="text-embedding-3-small", dimension=1536))
except EmbeddingCompositionError as exc:
    assert 'pip install "mesa-memory[adapters]"' in str(exc), f"Wrong message: {exc}"
else:
    raise AssertionError("must raise on missing dependency")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_local_embedding_missing_extra_message() -> None:
    code = """
import builtins
real_import = builtins.__import__

def block_st(name, *args, **kwargs):
    if name == 'sentence_transformers' or name.startswith('sentence_transformers.'):
        raise ImportError('simulated missing sentence_transformers')
    return real_import(name, *args, **kwargs)

builtins.__import__ = block_st
from mesa_memory.embedding.service import get_embedding_service, EmbeddingIdentity, EmbeddingCompositionError

try:
    get_embedding_service(
        identity=EmbeddingIdentity(provider="local", model="all-MiniLM-L6-v2", dimension=384),
        allow_model_loading=True,
        force_refresh=True,
    )
except EmbeddingCompositionError as exc:
    assert 'pip install "mesa-memory[ml]"' in str(exc), f"Wrong message: {exc}"
else:
    raise AssertionError("must raise on missing dependency")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

