import asyncio

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.retrieval.core import QueryAnalyzer, normalize_query
from mesa_memory.security.rbac import AccessControl
from mesa_memory.storage import StorageFacade


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

    async def retrieve(
        self, query_text: str, agent_id: str, session_id: str, top_n: int = 5
    ) -> list[str]:
        if not self.access_control.check_access(agent_id, session_id, "READ"):
            raise PermissionError(
                f"Agent '{agent_id}' lacks READ access for session '{session_id}'"
            )
        normalized = normalize_query(query_text)
        entities = self.analyzer.extract_entities(normalized)

        seed_nodes = await self.storage.graph.find_nodes_by_name(
            entities, case_insensitive=True
        )
        seed_ids = [n["node_id"] for n in seed_nodes]
        all_nodes = await self.storage.graph.get_all_active_nodes()
        is_cold_start = (
            len(seed_ids) == 0 or len(all_nodes) < config.cold_start_min_nodes
        )

        vector_task = self.get_vector_results(normalized, k=top_n * 2)
        graph_task = self.get_graph_results(entities)

        vector_results, graph_results = await asyncio.gather(vector_task, graph_task)

        if is_cold_start or not graph_results:
            if not vector_results:
                return []
            return [r["cmb_id"] for r in self._cold_start_rerank(vector_results, top_n)]

        fused_ids = self._apply_rrf(vector_results, graph_results, k=config.rrf_k)
        return fused_ids[:top_n]

    async def get_vector_results(self, query_text: str, k: int = 10) -> list[dict]:
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(None, self.embedder.embed, query_text)

        raw_results = await asyncio.to_thread(
            self.storage.vector.search, embedding, limit=k
        )

        results = []
        for i, r in enumerate(raw_results):
            results.append(
                {
                    "cmb_id": r.get("cmb_id", ""),
                    "content_payload": r.get("content_payload", ""),
                    "fitness_score": r.get("fitness_score", 0.0),
                    "score": 1.0 / (1.0 + r.get("_distance", 0.0)),
                    "source": "vector",
                    "rank": i + 1,
                }
            )
        return results

    async def get_graph_results(self, entities: list[str]) -> list[dict]:
        seed_nodes = await self.storage.graph.find_nodes_by_name(
            entities, case_insensitive=True
        )
        seed_ids = [n["node_id"] for n in seed_nodes]

        if not seed_ids:
            return []

        return await self._run_ppr(seed_ids)

    def _apply_rrf(
        self, vector_ranks: list[dict], graph_ranks: list[dict], k: int = 60
    ) -> list[str]:
        rrf_scores: dict[str, float] = {}

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

        sorted_ids = sorted(
            rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True
        )
        return sorted_ids

    async def _run_ppr(self, seed_ids: list[str], top_k: int = 15) -> list[dict]:
        all_nodes = await self.storage.graph.get_all_active_nodes()
        if not seed_ids or len(all_nodes) == 0:
            return []

        personalization = {node["node_id"]: 0.0 for node in all_nodes}
        weight = 1.0 / len(seed_ids)
        for sid in seed_ids:
            if sid in personalization:
                personalization[sid] = weight

        ppr_scores = await self.storage.graph.compute_pagerank(
            alpha=config.ppr_alpha,
            personalization=personalization,
            max_iter=100,
            tol=1e-6,
        )

        if not ppr_scores:
            return []

        seed_set = set(seed_ids)
        ranked = [
            {"cmb_id": node, "score": score, "source": "graph"}
            for node, score in ppr_scores.items()
            if node not in seed_set and score > 0
        ]
        ranked.sort(key=lambda x: float(x["score"]), reverse=True)  # type: ignore[arg-type]

        for i, item in enumerate(ranked):
            item["rank"] = i + 1

        return ranked[:top_k]

    def _cold_start_rerank(self, vector_results: list[dict], top_k: int) -> list[dict]:
        reranked = []
        for r in vector_results:
            fitness = r.get("fitness_score", 0.0)
            distance_score = r.get("score", 0.0)
            combined = (config.cold_start_fitness_weight * fitness) + (
                config.cold_start_distance_weight * distance_score
            )
            entry = dict(r)
            entry["rrf_score"] = combined
            entry["source"] = "vector_cold_start"
            reranked.append(entry)

        reranked.sort(key=lambda x: x["rrf_score"], reverse=True)
        return reranked[:top_k]

    def format_working_memory(
        self,
        nodes: list[dict],
        max_tokens: int | None = None,
    ) -> str:
        """Convert retrieved graph nodes into a token-limited string for LLM context.

        **Whole-Node Inclusion Policy**: Each node is formatted into a complete
        entry string *before* its token cost is measured.  If appending the
        entry would exceed the remaining token budget, the entire node is
        **discarded** and iteration stops immediately.  No partial content
        slicing is ever performed — the LLM receives only structurally and
        semantically complete node records.

        Args:
            nodes: List of node dicts, each expected to have at least
                ``content_payload`` and optionally ``source`` / ``cmb_id``.
            max_tokens: Hard token ceiling.  Defaults to
                ``config.context_window_limit`` when *None*.

        Returns:
            A formatted context string safe to inject into an LLM prompt.
            Returns ``"Retrieved Context: None"`` when *nodes* is empty or
            no node fits within the budget.
        """
        if not nodes:
            return "Retrieved Context: None"

        budget = max_tokens if max_tokens is not None else config.context_window_limit

        # Reserve tokens for the header line
        header = "Retrieved Context:"
        remaining = budget - self.embedder.get_token_count(header)
        if remaining <= 0:
            return "Retrieved Context: None"

        included: list[str] = []
        for idx, node in enumerate(nodes):
            # --- Build the complete entry string (never sliced) ---
            content = node.get("content_payload", "").strip()
            source = node.get("source", "unknown")
            cmb_id = node.get("cmb_id", "")

            entry = f"\n[{idx + 1}] (source={source}"
            if cmb_id:
                entry += f", id={cmb_id}"
            entry += f") {content}"

            # --- Whole-node budget gate ---
            entry_tokens = self.embedder.get_token_count(entry)
            if entry_tokens > remaining:
                # Node does not fit — discard entirely, stop processing.
                break

            included.append(entry)
            remaining -= entry_tokens

        if not included:
            return "Retrieved Context: None"

        return header + "".join(included)
