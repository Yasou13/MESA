from __future__ import annotations

import sys
from types import ModuleType

import pytest

from mesa_storage.vector_engine import VectorEngine


def test_default_vector_storage_does_not_import_or_load_model(monkeypatch) -> None:
    sys.modules.pop("sentence_transformers", None)
    imported = False
    real_import = __import__

    def guarded_import(name, *args, **kwargs):
        nonlocal imported
        if name == "sentence_transformers":
            imported = True
            raise AssertionError("model module must not be imported")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    engine = VectorEngine(
        "/storage/mesa-lab/storage/MASTER-CLOSURE/model-isolation/default"
    )
    assert imported is False
    with pytest.raises(RuntimeError, match="embedding runtime is disabled"):
        engine._sync_compute_embedding("must stay offline")


def test_explicit_model_loading_is_local_files_only(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []
    module = ModuleType("sentence_transformers")

    class FakeModel:
        def __init__(self, name: str, *, local_files_only: bool):
            calls.append((name, local_files_only))

    module.SentenceTransformer = FakeModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    engine = VectorEngine(
        "/storage/mesa-lab/storage/MASTER-CLOSURE/model-isolation/explicit",
        allow_model_loading=True,
    )
    assert calls == [("all-MiniLM-L6-v2", True)]
    assert engine._fallback_embedder is False
