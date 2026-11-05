"""Opus's writer-controlled scope and API-sized token scoring repros."""

import asyncio
import itertools
import re
import time

import pytest

import mesa_storage.dao as dao_module
import mesa_storage.kuzu_provider as graph_module
from mesa_storage.kuzu_provider import KuzuGraphProvider
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime


def _query_tokens():
    letters = "abcçdefghıijklmnoöprsştuüvy0123456789"
    return [a + b for a, b in itertools.product(letters, repeat=2)][:1359]


async def _insert(env, index, content, title="Law", legal=False):
    payload = dict(
        session_id=env.session_id,
        dataset_id="main",
        document_id=f"doc-{index}",
        revision_id=f"rev-{index}",
        chunk_id=f"chunk-{index}",
        title=title,
        source_ref=f"file://{index}",
        content=content,
    )
    if legal:
        payload["legal_identity"] = {"law_id": "6098", "article_id": "1"}
    response = await env.client.post("/v4/memory/insert", json=payload)
    assert response.status_code == 202, response.text
    await _consume(env, 3)


async def _search(env, query):
    return await env.dao.search_v4_memory(
        tenant_id="model-tenant",
        agent_id="model-agent",
        dataset_ids=["main"],
        query=query,
        limit=50,
        graph_enabled=False,
    )


@pytest.mark.asyncio
async def test_ten_crafted_law_headers_do_not_block_after_each_write(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        for index in range(10):
            await _insert(
                env,
                index,
                "1" * 2975 + "x Kanun Numarası: 6098\nMADDE 1- Kural.",
                legal=True,
            )
        gaps = []

        async def heartbeat():
            last = time.perf_counter()
            while True:
                await asyncio.sleep(0.002)
                now = time.perf_counter()
                gaps.append(now - last - 0.002)
                last = now

        for index in range(3):
            await _insert(env, 10 + index, "Benign note to invalidate scope.")
            pulse = asyncio.create_task(heartbeat())
            await asyncio.sleep(0.005)
            try:
                assert await _search(env, "TBK m. 49")
                await asyncio.sleep(0.005)
            finally:
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)
        assert max(gaps) < 0.2, f"scope build blocked the loop for {max(gaps):.3f}s"


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["123  ", "١٢٣\t", "１２３\n", "1 2 ", "  "])
async def test_header_cleanup_keeps_previous_output(model_runtime, suffix):
    prefix = "Özel Zümrüt Kanunu " + suffix
    expected = dao_module.normalize_law_name(re.sub(r"\d+\s*$", "", prefix).strip())
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        await _insert(
            env, 0, prefix + "Kanun Numarası: 6098\nMADDE 1- Kural.", legal=True
        )
        await _search(env, "TBK m. 1")
        scope = next(iter(env.dao._search_scope_cache.values()))
        assert scope["legal_context"]["names"][expected] == "6098"


@pytest.mark.asyncio
async def test_skip_overlong_stored_names_but_keep_200_character_names(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        for index, size in enumerate((200, 201, 2970)):
            name = "a" * (size - len(" kanunu")) + " kanunu"
            await _insert(
                env,
                index,
                name + " Kanun Numarası: 6098\nKural.",
                title=name if size <= 512 else "a" * 193 + " kanunu",
                legal=True,
            )
        await _search(env, "TBK m. 1")
        scope = next(iter(env.dao._search_scope_cache.values()))
        names = scope["legal_context"]["names"]
        assert len(names) == 1
        assert len(next(iter(names))) == 200


@pytest.mark.asyncio
async def test_40_large_memories_bound_both_dao_scoring_loops(
    model_runtime, monkeypatch
):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        for index in range(40):
            await _insert(env, index, ("zz " * 11000)[:32768])
        original = dao_module._normalize_identity_text
        scans = []
        tokens = set(_query_tokens()) | {"zz"}

        class TrackedText(str):
            def __contains__(self, token):
                if token in tokens:
                    scans.append(token)
                return super().__contains__(token)

        def track(text):
            normalized = original(text)
            return TrackedText(normalized) if len(normalized) > 10000 else normalized

        monkeypatch.setattr(dao_module, "_normalize_identity_text", track)
        query = ("zz " + " ".join(_query_tokens())).ljust(4096)
        assert len(query) == 4096
        assert await _search(env, query)
        # Both passage and assertion evidence are visited. No candidate scan may
        # admit tokens past the first 64 after the existing stopword filters.
        assert scans
        assert len(set(scans)) == 64
        assert "zz" in scans
        assert _query_tokens()[-1] not in scans


def test_graph_scoring_deduplicates_and_caps_tokens_in_order(monkeypatch):
    original = graph_module._normalize_text
    scans = []

    class TrackedText(str):
        def __contains__(self, token):
            scans.append(token)
            return super().__contains__(token)

    monkeypatch.setattr(
        graph_module, "_normalize_text", lambda text: TrackedText(original(text))
    )
    tokens = ["zz", "zz", *_query_tokens()]
    KuzuGraphProvider._compute_path_score(
        hop=1,
        seed_id="seed",
        seed_scores=None,
        query_tokens=tokens,
        predicates=["!"],
        evidence_spans=["!" * 32768],
        jurisdictions=[""],
        confidences=[1.0],
        edge_directions=["forward"],
        target_statutes=set(),
        competing_statutes=[],
        resolver=None,
    )
    assert list(dict.fromkeys(scans)) == list(dict.fromkeys(tokens))[:64]
    assert len(scans) == 128  # Each token checks predicate, then evidence.
