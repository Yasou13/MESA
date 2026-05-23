from unittest.mock import AsyncMock, MagicMock, patch

import networkx as nx
import pytest

from mesa_memory.retrieval.hybrid import HybridRetriever
from tests.conftest import deterministic_embedding


def test_query_analyzer_fallback():
    with patch("mesa_memory.retrieval.core.spacy") as mock_spacy:
        mock_nlp = MagicMock()
        mock_doc = MagicMock()
        mock_doc.ents = []

        token_how = MagicMock()
        token_how.text = "how"
        token_how.pos_ = "ADV"
        token_how.is_stop = True
        token_how.is_punct = False

        token_going = MagicMock()
        token_going.text = "going"
        token_going.pos_ = "VERB"
        token_going.is_stop = False
        token_going.is_punct = False

        mock_doc.__iter__ = MagicMock(return_value=iter([token_how, token_going]))
        mock_nlp.return_value = mock_doc
        mock_spacy.load.return_value = mock_nlp

        from mesa_memory.retrieval.core import QueryAnalyzer

        analyzer = QueryAnalyzer.__new__(QueryAnalyzer)
        analyzer.nlp = mock_nlp

        result = analyzer.extract_entities("how is it going?")
        assert len(result) > 0
        assert isinstance(result, list)


@pytest.mark.asyncio
async def test_hybrid_retrieval_cold_start():
    storage = MagicMock()

    empty_graph = nx.MultiDiGraph()
    storage.graph.get_active_graph.return_value = empty_graph
    storage.graph.find_nodes_by_name = AsyncMock(return_value=[])
    storage.graph.get_all_active_nodes = AsyncMock(return_value=[])

    storage.vector.search.return_value = [
        {
            "cmb_id": "vec_1",
            "content_payload": "content 1",
            "fitness_score": 0.8,
            "_distance": 0.1,
        },
        {
            "cmb_id": "vec_2",
            "content_payload": "content 2",
            "fitness_score": 0.5,
            "_distance": 0.3,
        },
    ]

    analyzer = MagicMock()
    analyzer.extract_entities.return_value = ["unknown_entity"]

    embedder = MagicMock()
    embedder.embed.side_effect = lambda text: deterministic_embedding(text, 768)

    from mesa_memory.security.rbac import AccessControl

    ac = AccessControl()
    await ac.initialize()
    await ac.grant_access("test_agent", "test_session", "READ")

    retriever = HybridRetriever(
        storage_facade=storage,
        analyzer=analyzer,
        embedder=embedder,
        access_control=ac,
    )

    results = await retriever.retrieve(
        "unknown_entity query",
        agent_id="test_agent",
        session_id="test_session",
        top_n=5,
    )

    assert len(results) > 0
    assert "vec_1" in results
    assert "vec_2" in results


@pytest.mark.asyncio
async def test_alpha_reranking_logic():
    storage = MagicMock()

    graph = nx.MultiDiGraph()
    graph.add_node("node_b", name="B_entity", type="ENTITY")
    graph.add_node("node_c", name="C_entity", type="ENTITY")
    storage.graph.get_active_graph.return_value = graph

    storage.vector.search.return_value = [
        {
            "cmb_id": "A",
            "content_payload": "a",
            "fitness_score": 0.9,
            "_distance": 0.05,
        },
        {
            "cmb_id": "B",
            "content_payload": "b",
            "fitness_score": 0.7,
            "_distance": 0.15,
        },
    ]

    analyzer = MagicMock()
    analyzer.extract_entities.return_value = ["test"]

    embedder = MagicMock()
    embedder.embed.side_effect = lambda text: deterministic_embedding(text, 768)

    retriever = HybridRetriever(
        storage_facade=storage,
        analyzer=analyzer,
        embedder=embedder,
    )

    vector_ranks = [
        {"cmb_id": "A", "score": 0.9},
        {"cmb_id": "B", "score": 0.7},
    ]
    graph_ranks = [
        {"cmb_id": "B", "score": 0.05},
        {"cmb_id": "C", "score": 0.20},
    ]
    lexical_ranks = [
        {"cmb_id": "D", "score": 20.0},
    ]

    from mesa_memory.config import config

    original_alpha = getattr(config, "hybrid_alpha", 0.0)
    original_beta = getattr(config, "hybrid_beta", 0.0)
    config.hybrid_alpha = 0.5
    config.hybrid_beta = 0.2

    try:
        fused_ids = retriever._apply_alpha_reranking(
            vector_ranks, graph_ranks, lexical_ranks
        )

        # S_vec + (alpha * S_graph_norm) + (beta * S_lex_norm)
        # A: 0.9 + 0 + 0 = 0.9
        # B: 0.7 + 0.5 * min(0.05*10, 1) = 0.7 + 0.25 = 0.95
        # C: 0.0 + 0.5 * min(0.2*10, 1) = 0.0 + 0.5 = 0.5
        # D: 0.0 + 0 + 0.2 * min(20/10, 1) = 0.2
        assert fused_ids[0] == "B"
        assert fused_ids[1] == "A"
        assert fused_ids[2] == "C"
        assert fused_ids[3] == "D"
    finally:
        config.hybrid_alpha = original_alpha
        config.hybrid_beta = original_beta


# ===================================================================
# Missing Coverage Tests
# ===================================================================


def test_query_analyzer_regex_fallback():
    from mesa_memory.retrieval.core import QueryAnalyzer

    analyzer = QueryAnalyzer.__new__(QueryAnalyzer)
    analyzer.nlp = None

    entities = analyzer.extract_entities("Tesla acquires Twitter in major deal")
    assert isinstance(entities, list)
    assert len(entities) > 0


def test_query_analyzer_regex_all_stopwords():
    from mesa_memory.retrieval.core import QueryAnalyzer

    analyzer = QueryAnalyzer.__new__(QueryAnalyzer)
    analyzer.nlp = None

    entities = analyzer.extract_entities("the is at")
    assert isinstance(entities, list)
    assert len(entities) >= 1


def test_normalize_query():
    from mesa_memory.retrieval.core import normalize_query

    assert normalize_query("  Hello   World  ") == "hello world"
