import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import networkx as nx

from mesa_memory.retrieval.hybrid import HybridRetriever


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

    storage.vector.search.return_value = [
        {"cmb_id": "vec_1", "content_payload": "content 1", "fitness_score": 0.8, "_distance": 0.1},
        {"cmb_id": "vec_2", "content_payload": "content 2", "fitness_score": 0.5, "_distance": 0.3},
    ]

    analyzer = MagicMock()
    analyzer.extract_entities.return_value = ["unknown_entity"]

    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 768

    retriever = HybridRetriever(
        storage_facade=storage,
        analyzer=analyzer,
        embedder=embedder,
    )

    results = await retriever.retrieve("unknown_entity query", top_n=5)

    assert len(results) > 0
    assert "vec_1" in results
    assert "vec_2" in results


@pytest.mark.asyncio
async def test_rrf_ranking_logic():
    storage = MagicMock()

    graph = nx.MultiDiGraph()
    graph.add_node("node_b", name="B_entity", type="ENTITY")
    graph.add_node("node_c", name="C_entity", type="ENTITY")
    storage.graph.get_active_graph.return_value = graph

    storage.vector.search.return_value = [
        {"cmb_id": "A", "content_payload": "a", "fitness_score": 0.9, "_distance": 0.05},
        {"cmb_id": "B", "content_payload": "b", "fitness_score": 0.7, "_distance": 0.15},
    ]

    analyzer = MagicMock()
    analyzer.extract_entities.return_value = ["test"]

    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 768

    retriever = HybridRetriever(
        storage_facade=storage,
        analyzer=analyzer,
        embedder=embedder,
    )

    vector_ranks = [
        {"cmb_id": "A", "rank": 1, "source": "vector"},
        {"cmb_id": "B", "rank": 2, "source": "vector"},
    ]
    graph_ranks = [
        {"cmb_id": "B", "rank": 1, "source": "graph"},
        {"cmb_id": "C", "rank": 2, "source": "graph"},
    ]

    fused_ids = retriever._apply_rrf(vector_ranks, graph_ranks, k=60)

    assert fused_ids[0] == "B"
    assert "A" in fused_ids
    assert "C" in fused_ids
