import asyncio

import networkx as nx
import numpy as np

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.storage import StorageFacade
from mesa_memory.retrieval.core import QueryAnalyzer, normalize_query
from mesa_memory.security.rbac import AccessControl


RRF_K = 60
COLD_START_MIN_NODES = 10
PPR_ALPHA = 0.15


class HybridRetriever:
    def __init__(
        self,
        storage_facade: StorageFacade,
        analyzer: QueryAnalyzer,
        embedder: BaseUniversalLLMAdapter,
        access_control: AccessControl | None = None,
    ):
        self.storage = storage_facade
        self.analyzer = analyzer
        self.embedder = embedder
        self.access_control = access_control or AccessControl()

    async def retrieve(self, query_text: str, agent_id: str, session_id: str, top_n: int = 5) -> list[str]:
        if not self.access_control.check_access(agent_id, session_id, "READ"):
            raise PermissionError(
                f"Agent '{agent_id}' lacks READ access for session '{session_id}'"
            )
        normalized = normalize_query(query_text)
        entities = self.analyzer.extract_entities(normalized)

        active_graph = self.storage.graph.get_active_graph()
        seed_ids = self._match_entities_to_nodes(entities, active_graph)
        is_cold_start = len(seed_ids) == 0 or len(active_graph.nodes) < COLD_START_MIN_NODES

        vector_task = self.get_vector_results(normalized, k=top_n * 2)
        graph_task = self.get_graph_results(entities)

        vector_results, graph_results = await asyncio.gather(vector_task, graph_task)

        if is_cold_start or not graph_results:
            if not vector_results:
                return []
            return [r["cmb_id"] for r in self._cold_start_rerank(vector_results, top_n)]

        fused_ids = self._apply_rrf(vector_results, graph_results, k=RRF_K)
        return fused_ids[:top_n]

    async def get_vector_results(self, query_text: str, k: int = 10) -> list[dict]:
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(None, self.embedder.embed, query_text)

        raw_results = self.storage.vector.search(embedding, limit=k)

        results = []
        for i, r in enumerate(raw_results):
            results.append({
                "cmb_id": r.get("cmb_id", ""),
                "content_payload": r.get("content_payload", ""),
                "fitness_score": r.get("fitness_score", 0.0),
                "score": 1.0 / (1.0 + r.get("_distance", 0.0)),
                "source": "vector",
                "rank": i + 1,
            })
        return results

    async def get_graph_results(self, entities: list[str]) -> list[dict]:
        active_graph = self.storage.graph.get_active_graph()
        seed_ids = self._match_entities_to_nodes(entities, active_graph)

        if not seed_ids:
            return []

        return await self._run_ppr(active_graph, seed_ids)

    def _apply_rrf(self, vector_ranks: list[dict], graph_ranks: list[dict], k: int = 60) -> list[str]:
        rrf_scores = {}

        for item in vector_ranks:
            cmb_id = item.get("cmb_id", "")
            if not cmb_id:
                continue
            rank = item.get("rank", len(vector_ranks))
            rrf_scores[cmb_id] = rrf_scores.get(cmb_id, 0.0) + 1.0 / (k + rank)

        for item in graph_ranks:
            cmb_id = item.get("cmb_id", "")
            if not cmb_id:
                continue
            rank = item.get("rank", len(graph_ranks))
            rrf_scores[cmb_id] = rrf_scores.get(cmb_id, 0.0) + 1.0 / (k + rank)

        sorted_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)
        return sorted_ids

    def _match_entities_to_nodes(self, entities: list[str], graph: nx.MultiDiGraph) -> list[str]:
        matched = []
        entity_set = {e.lower() for e in entities}
        for node_id, data in graph.nodes(data=True):
            name = data.get("name", "").lower()
            if name in entity_set:
                matched.append(node_id)
        return matched

    async def _run_ppr(self, graph: nx.MultiDiGraph, seed_ids: list[str], top_k: int = 15) -> list[dict]:
        if not seed_ids or len(graph.nodes) == 0:
            return []

        personalization = {node: 0.0 for node in graph.nodes}
        weight = 1.0 / len(seed_ids)
        for sid in seed_ids:
            if sid in personalization:
                personalization[sid] = weight

        def _compute_pagerank():
            return nx.pagerank(
                graph,
                alpha=PPR_ALPHA,
                personalization=personalization,
                max_iter=100,
                tol=1e-6,
            )

        loop = asyncio.get_running_loop()
        try:
            ppr_scores = await loop.run_in_executor(None, _compute_pagerank)
        except nx.NetworkXError:
            return []

        seed_set = set(seed_ids)
        ranked = [
            {"cmb_id": node, "score": score, "source": "graph"}
            for node, score in ppr_scores.items()
            if node not in seed_set and score > 0
        ]
        ranked.sort(key=lambda x: x["score"], reverse=True)

        for i, item in enumerate(ranked):
            item["rank"] = i + 1

        return ranked[:top_k]

    def _cold_start_rerank(self, vector_results: list[dict], top_k: int) -> list[dict]:
        reranked = []
        for r in vector_results:
            fitness = r.get("fitness_score", 0.0)
            distance_score = r.get("score", 0.0)
            combined = fitness * distance_score if fitness > 0 else distance_score
            entry = dict(r)
            entry["rrf_score"] = combined
            entry["source"] = "vector_cold_start"
            reranked.append(entry)

        reranked.sort(key=lambda x: x["rrf_score"], reverse=True)
        return reranked[:top_k]
