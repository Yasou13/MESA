import asyncio
import logging

import networkx as nx

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.retrieval.core import QueryAnalyzer, normalize_query
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_Retrieval")


def find_path(
    graph: nx.Graph, source_entity: str, target_entity: str, max_hops: int = 3
) -> list:
    """Find the shortest path between two entities in the knowledge graph."""
    try:
        path = nx.shortest_path(graph, source=source_entity, target=target_entity)
        if len(path) - 1 <= max_hops:
            return path
        return []
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []


class HybridRetriever:
    """Hybrid retriever combining vector, lexical, and graph search.

    All storage I/O is routed exclusively through ``MemoryDAO`` —
    the single source of truth for the MESA system.  Graph traversal
    (PPR, multi-hop) is performed over an in-memory NetworkX snapshot
    constructed from DAO edge data.
    """

    def __init__(
        self,
        dao: MemoryDAO,
        analyzer: QueryAnalyzer,
        embedder: BaseUniversalLLMAdapter,
        access_control: AccessControl | None = None,
        agent_id: str = "__unset__",
    ):
        self.dao = dao
        self.analyzer = analyzer
        self.embedder = embedder
        self.access_control = access_control or AccessControl()
        self._agent_id = agent_id

    async def retrieve(
        self,
        query_text: str,
        agent_id: str,
        session_id: str,
        top_n: int = 5,
        enable_multi_hop: bool = False,
    ) -> list[str] | dict:
        if not await self.access_control.check_access(agent_id, session_id, "READ"):
            raise PermissionError(
                f"Agent '{agent_id}' lacks READ access for session '{session_id}'"
            )
        normalized = normalize_query(query_text)
        entities = self.analyzer.extract_entities(normalized)

        # Graph node lookup via DAO
        seed_nodes = await self.dao.find_nodes_by_name(
            agent_id, names=entities, case_insensitive=True
        )
        seed_ids = [n["id"] for n in seed_nodes]

        # Cold-start detection via DAO
        all_nodes = await self.dao.get_memories(agent_id)
        is_cold_start = (
            len(seed_ids) == 0 or len(all_nodes) < config.cold_start_min_nodes
        )

        vector_task = self.get_vector_results(agent_id, normalized, k=100)
        graph_task = self.get_graph_results(agent_id, entities)

        vector_results, graph_results = await asyncio.gather(vector_task, graph_task)
        lexical_results: list[dict] = []

        # Try FTS5 lexical search via DAO if available
        try:
            lexical_results = await self.dao.search_memory_fts(
                agent_id, query=normalized, limit=100
            )
            # Normalize to ranking format
            lexical_results = [
                {
                    "cmb_id": r.get("id", ""),
                    "content_payload": r.get("entity_name", ""),
                    "score": abs(r.get("rank", 0.0)),
                    "source": "lexical",
                    "rank": i + 1,
                }
                for i, r in enumerate(lexical_results)
            ]
        except Exception:
            lexical_results = []

        if is_cold_start or not graph_results:
            if not vector_results:
                cmb_ids: list[str] = []
            else:
                cmb_ids = [
                    r["cmb_id"] for r in self._cold_start_rerank(vector_results, top_n)
                ]
        else:
            fused_ids = self._apply_alpha_reranking(
                vector_results,
                graph_results,
                lexical_results,
            )
            cmb_ids = fused_ids[:top_n]

        if not enable_multi_hop:
            return cmb_ids

        # --- Multi-hop graph traversal between top 2 seed entities ---
        multi_hop_path: list[str] = []
        if len(seed_nodes) >= 2:
            source_id = seed_nodes[0]["id"]
            target_id = seed_nodes[1]["id"]
            try:
                graph_snapshot = await self._build_graph_snapshot(agent_id)
                multi_hop_path = find_path(graph_snapshot, source_id, target_id)
            except Exception:
                logger.warning(
                    "Multi-hop traversal failed between %s and %s",
                    source_id,
                    target_id,
                    exc_info=True,
                )

        return {"cmb_ids": cmb_ids, "multi_hop_path": multi_hop_path}

    async def get_vector_results(
        self, agent_id: str, query_text: str, k: int = 10
    ) -> list[dict]:
        """Search via MemoryDAO vector search (LanceDB + RLS)."""
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(None, self.embedder.embed, query_text)

        raw_results = await self.dao.search_memory(
            agent_id, query_vector=embedding, limit=k
        )

        results = []
        for i, r in enumerate(raw_results):
            results.append(
                {
                    "cmb_id": r.get("node_id", ""),
                    "content_payload": r.get("content_hash", ""),
                    "fitness_score": 0.0,
                    "score": 1.0 / (1.0 + r.get("_distance", 0.0)),
                    "source": "vector",
                    "rank": i + 1,
                }
            )
        return results

    async def get_graph_results(self, agent_id: str, entities: list[str]) -> list[dict]:
        """Look up graph neighbours via MemoryDAO."""
        seed_nodes = await self.dao.find_nodes_by_name(
            agent_id, names=entities, case_insensitive=True
        )
        seed_ids = [n["id"] for n in seed_nodes]

        if not seed_ids:
            return []

        return await self._run_ppr(agent_id, seed_ids)

    def _apply_alpha_reranking(
        self,
        vector_ranks: list[dict],
        graph_ranks: list[dict],
        lexical_ranks: list[dict],
    ) -> list[str]:
        """Apply Score-Based Bonus (Alpha-Reranking) with deterministic normalization."""
        alpha = getattr(config, "hybrid_alpha", 0.0)
        beta = getattr(config, "hybrid_beta", 0.0)

        # Union Set Candidate Pool
        union_ids = set()
        for ranks in (vector_ranks, graph_ranks, lexical_ranks):
            for item in ranks:
                if cmb_id := item.get("cmb_id"):
                    union_ids.add(cmb_id)

        # Index raw scores by ID
        vector_scores = {
            item.get("cmb_id", ""): item.get("score", 0.0)
            for item in vector_ranks
            if item.get("cmb_id", "")
        }
        graph_scores = {
            item.get("cmb_id", ""): item.get("score", 0.0)
            for item in graph_ranks
            if item.get("cmb_id", "")
        }
        lexical_scores = {
            item.get("cmb_id", ""): item.get("score", 0.0)
            for item in lexical_ranks
            if item.get("cmb_id", "")
        }

        final_scores: dict[str, float] = {}

        # Alpha Reranking Formula: S_vec + (alpha * S_graph_norm) + (beta * S_lex_norm)
        for cmb_id in union_ids:
            s_vec = vector_scores.get(cmb_id, 0.0)
            s_graph_raw = graph_scores.get(cmb_id, 0.0)
            s_lex_raw = lexical_scores.get(cmb_id, 0.0)

            # Deterministic Normalization
            # PPR scores are probabilities, cap at 1.0. Scaling by 10 to bring up sparse values.
            s_graph_norm = min(s_graph_raw * 10.0, 1.0)
            # FTS5 Lexical normalization via empirical constant
            s_lex_norm = min(s_lex_raw / 10.0, 1.0)

            final_scores[cmb_id] = s_vec + (alpha * s_graph_norm) + (beta * s_lex_norm)

        sorted_ids = sorted(
            final_scores.keys(), key=lambda cid: final_scores[cid], reverse=True
        )
        return sorted_ids

    async def _build_graph_snapshot(self, agent_id: str) -> nx.DiGraph:
        """Construct a NetworkX directed graph from DAO node/edge data.

        This constructs the graph on-demand from the DAO's relational layer
        to enable multi-hop traversal and PPR without external graph providers.
        """
        G = nx.DiGraph()

        # Add all active nodes
        nodes = await self.dao.get_memories(agent_id)
        for node in nodes:
            G.add_node(
                node["id"],
                **{
                    "entity_name": node.get("entity_name", ""),
                    "type": node.get("type", "ENTITY"),
                },
            )

        # Add edges for each node
        for node in nodes:
            edges = await self.dao.get_neighbors(
                agent_id, node_id=node["id"], direction="out"
            )
            for edge in edges:
                G.add_edge(
                    edge["source_id"],
                    edge["target_id"],
                    relation_type=edge.get("relation_type", "RELATED_TO"),
                    weight=edge.get("weight", 1.0),
                )

        return G

    async def _run_ppr(
        self, agent_id: str, seed_ids: list[str], top_k: int = 15, max_depth: int = 2
    ) -> list[dict]:
        """Personalized PageRank via DAO-constructed graph snapshot."""
        graph_snapshot = await self._build_graph_snapshot(agent_id)

        if not seed_ids or len(graph_snapshot.nodes) == 0:
            return []

        # Strict semantic bound: Calculate maximum depth from seeds to prevent drift
        bounded_nodes: set[str] | None = set()
        try:
            for sid in seed_ids:
                if sid in graph_snapshot:
                    subgraph = nx.ego_graph(graph_snapshot, sid, radius=max_depth)
                    if bounded_nodes is not None:
                        bounded_nodes.update(subgraph.nodes())
        except Exception as exc:
            logger.warning("Failed to bound graph traversal: %s", exc)
            bounded_nodes = None

        personalization = {node: 0.0 for node in graph_snapshot.nodes()}
        weight = 1.0 / len(seed_ids)
        for sid in seed_ids:
            if sid in personalization:
                personalization[sid] = weight

        try:
            ppr_scores = nx.pagerank(
                graph_snapshot,
                alpha=config.ppr_alpha,
                personalization=personalization,
                max_iter=100,
                tol=1e-6,
            )
        except nx.PowerIterationFailedConvergence:
            logger.warning("PageRank failed to converge, returning empty results")
            return []

        if not ppr_scores:
            return []

        seed_set = set(seed_ids)
        ranked = []
        for node, score in ppr_scores.items():
            if node in seed_set or score <= 0:
                continue

            # Apply graph noise reduction: reject nodes outside the max_depth bounds
            if bounded_nodes is not None and node not in bounded_nodes:
                continue

            ranked.append({"cmb_id": node, "score": score, "source": "graph"})

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
