"""Reranking runs on authorized chunk text after releasing SQLite."""

import asyncio
import importlib.util
import sys
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mesa_memory.config import MesaConfig
from mesa_storage.dao import MemoryDAO
from tests.test_model_optional_runtime import model_runtime as model_runtime
from tests.test_p702_search_scope_cache import _search, _seed
from tests.test_v4_bm25_scope_isolation import _environment


def test_rerank_config_defaults_and_validation(monkeypatch):
    for name in ("MESA_RERANK_ENABLED", "MESA_RERANK_MODEL", "MESA_RERANK_DEPTH"):
        monkeypatch.delenv(name, raising=False)
    cfg = MesaConfig(_env_file=None)
    assert cfg.rerank_enabled is False
    assert cfg.rerank_model == "BAAI/bge-reranker-v2-m3"
    assert cfg.rerank_depth == 50
    monkeypatch.setenv("MESA_RERANK_DEPTH", "0")
    with pytest.raises(ValidationError):
        MesaConfig(_env_file=None)


def test_model_is_lazy_loaded_once_with_fake_cross_encoder(monkeypatch):
    from mesa_memory.reranking import CrossEncoderReranker

    loaded = []

    class FakeModel:
        def __init__(self, name, **kwargs):
            loaded.append((name, kwargs))

        def predict(self, pairs, **kwargs):
            assert pairs == [("query", "chunk")]
            return [0.75]

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(CrossEncoder=FakeModel))
    scorer = CrossEncoderReranker("fake")
    assert not loaded
    assert scorer.score("query", ["chunk"]) == [0.75]
    assert scorer.score("query", ["chunk"]) == [0.75]
    assert loaded == [("fake", {"max_length": 1024})]


def test_missing_ml_extra_fails_before_search(monkeypatch):
    from mesa_memory.reranking import CrossEncoderReranker

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RuntimeError, match=r'pip install "mesa-memory\[ml\]"'):
        CrossEncoderReranker("fake")


@pytest.mark.asyncio
async def test_missing_ml_extra_stops_runtime_startup(model_runtime, monkeypatch):
    original = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: (
        None if name == "sentence_transformers" else original(name)
    ))
    with pytest.raises(RuntimeError, match=r'pip install "mesa-memory\[ml\]"'):
        async with model_runtime(MESA_RERANK_ENABLED=True):
            pytest.fail("runtime must not start without the selected optional package")


@pytest.mark.asyncio
@pytest.mark.parametrize("scores", [[float("nan")], [], [float("inf")]])
async def test_invalid_model_scores_fail_instead_of_returning_partial_ranking(scores):
    dao = MemoryDAO(None, None, reranker=SimpleNamespace(score=lambda *_: scores))
    with pytest.raises(RuntimeError, match="invalid scores"):
        await dao._rerank_results("query", [{"_rerank_text": "passage"}], 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("cache", [False, True])
async def test_rerank_depth_full_chunk_text_stable_ties_and_released_connection(tmp_path, cache):
    sql, vector, graph, plain = await _environment(tmp_path)
    event_thread = threading.get_ident()
    try:
        for number in range(1, 5):
            await _seed(plain, number)
        # Foreign evidence must never be sent to the model.
        await _seed(plain, 5, tenant="other")
        fused = await _search(plain, limit=4)
        assert len(fused) == 4
        seen = []

        def score(query, texts):
            assert threading.get_ident() != event_thread
            assert not sql._search_lock.locked()
            assert not sql._search_db or not sql._search_db.in_transaction
            assert query == "kira bedeli"
            assert len(texts) == 4
            assert all("Article 12 privacy compliance." in text for text in texts)
            assert not any(text.endswith(" 5") for text in texts)
            seen.extend(texts)
            return [0.1, 0.9, 0.9, 0.2]

        reranked = MemoryDAO(sql, vector, graph, search_scope_cache_enabled=cache,
                             reranker=SimpleNamespace(score=score), rerank_depth=4)
        result = await _search(reranked, limit=2)
        assert [r["assertion_id"] for r in result] == [r["assertion_id"] for r in fused[1:3]]
        for result_row, fused_row in zip(result, fused[1:3]):
            assert result_row == {**fused_row, "rerank_score": 0.9}
        assert seen
        # A score call in a worker does not stall the event loop.
        await asyncio.sleep(0)
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_disabled_reranker_keeps_complete_results_and_never_imports_ml(tmp_path, monkeypatch):
    import builtins

    sql, vector, graph, dao = await _environment(tmp_path)
    original = builtins.__import__

    def guard(name, *args, **kwargs):
        assert not name.startswith("sentence_transformers")
        return original(name, *args, **kwargs)

    try:
        await _seed(dao, 1)
        expected = await _search(dao)
        monkeypatch.setattr(builtins, "__import__", guard)
        disabled = MemoryDAO(sql, vector, graph, reranker=None)
        assert await _search(disabled) == expected
    finally:
        await sql.close()
