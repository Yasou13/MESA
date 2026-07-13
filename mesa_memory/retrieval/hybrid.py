import asyncio
import logging
from typing import Any

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.retrieval.core import QueryAnalyzer, normalize_query
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_Retrieval")


class HybridRetriever:
    """Hybrid retriever combining vector, lexical, and graph search.

    All storage I/O is routed exclusively through ``MemoryDAO`` —
    the single source of truth for the MESA system.  Graph traversal
    (multi-hop spreading activation) is performed via KùzuDB through
    the DAO's ``get_neighbors`` and ``get_node_degree`` methods,
    eliminating the in-memory NetworkX snapshot and its OOM
    vulnerability.
    """

    def __init__(
        self,
        dao: MemoryDAO,
        analyzer: QueryAnalyzer,
        embedder: BaseUniversalLLMAdapter | None = None,
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

        vector_results: list[Any] = []
        graph_results: list[Any] = []
        gather_results = await asyncio.gather(
            vector_task, graph_task, return_exceptions=True
        )
        for i, result in enumerate(gather_results):
            if isinstance(result, BaseException):
                label = "vector" if i == 0 else "graph"
                logger.error(
                    "HYBRID_RETRIEVAL_%s_FAILED | agent_id=%s error=%s",
                    label.upper(),
                    agent_id,
                    result,
                    exc_info=result,
                )  # pragma: no cover
            elif i == 0:
                vector_results = result  # type: ignore[assignment]
            else:
                graph_results = result  # type: ignore[assignment]
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
            logger.warning(
                "FTS5_SEARCH_FAILED | agent_id=%s — falling back to empty lexical results",
                agent_id,
                exc_info=True,
            )
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
        # Uses KùzuDB's variable-length path traversal via DAO instead
        # of the legacy NetworkX snapshot.  Zero OOM risk.
        multi_hop_path: list[str] = []
        if len(seed_nodes) >= 1:
            source_id = seed_nodes[0]["id"]
            try:
                neighbors = await self.dao.get_neighbors(
                    agent_id,
                    node_id=source_id,
                    max_hops=3,
                )
                if len(seed_nodes) >= 2:
                    # Build the path: source → all reachable neighbors sorted by hops
                    target_id = seed_nodes[1]["id"]
                    # Check if target is reachable within the neighbor set
                    target_neighbors = [n for n in neighbors if n["id"] == target_id]
                    if target_neighbors:
                        # Reconstruct a path-like list: source + intermediate + target
                        hop_depth = target_neighbors[0]["hops"]
                        intermediates = sorted(
                            [n for n in neighbors if n["hops"] < hop_depth],
                            key=lambda x: x["hops"],
                        )
                        multi_hop_path = (
                            [source_id] + [n["id"] for n in intermediates] + [target_id]
                        )
                    else:
                        # Target not reachable — return source + sorted neighbors
                        multi_hop_path = [source_id] + [
                            n["id"] for n in sorted(neighbors, key=lambda x: x["hops"])
                        ]
                else:
                    # Single seed - just return source + sorted neighbors
                    multi_hop_path = [source_id] + [
                        n["id"] for n in sorted(neighbors, key=lambda x: x["hops"])
                    ]
            except Exception:
                logger.warning(
                    "Multi-hop traversal failed from %s",
                    source_id,
                    exc_info=True,
                )

        return {"cmb_ids": cmb_ids, "multi_hop_path": multi_hop_path}

    async def get_vector_results(
        self, agent_id: str, query_text: str, k: int = 10
    ) -> list[dict]:
        """Search via MemoryDAO vector search (LanceDB + RLS)."""
        # Use the VectorEngine to compute the embedding instead of the LLM adapter
        embedding = await self.dao.vector_engine.compute_embedding(query_text)

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
        """Compute cognitive salience directly via KùzuDB through the DAO.

        Delegates completely to the database engine via `get_cognitive_salience`,
        eliminating legacy Python spreading activation math.
        """
        seed_nodes = await self.dao.find_nodes_by_name(
            agent_id, names=entities, case_insensitive=True
        )
        seed_ids = [n["id"] for n in seed_nodes]

        if not seed_ids:
            return []

        if not self.dao.graph_provider:
            return []

        # Collect cognitive salience from all seeds (max score fusion)
        all_salience: dict[str, float] = {}
        for sid in seed_ids:
            try:
                # Direct KuzuDB Cypher query inside get_cognitive_salience
                salience_results = await self.dao.graph_provider.get_cognitive_salience(
                    seed_id=sid,
                    agent_id=agent_id,
                    max_hops=3,
                    limit=15,
                )
                for item in salience_results:
                    nid = item["node_id"]
                    # Exclude seeds from results (same as PPR behaviour)
                    if nid in seed_ids:
                        continue

                    score = item["score"]
                    if nid not in all_salience or score > all_salience[nid]:
                        all_salience[nid] = score
            except Exception as exc:
                logger.warning(
                    "Cognitive salience query failed for seed %s: %s", sid, exc
                )

        if not all_salience:
            return []

        # Sort by score descending, truncate to top_k (15)
        ranked = [
            {"cmb_id": nid, "score": score, "source": "graph"}
            for nid, score in sorted(
                all_salience.items(), key=lambda x: x[1], reverse=True
            )
        ][:15]

        # Assign ranks
        for i, item in enumerate(ranked):
            item["rank"] = i + 1

        return ranked

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
            # Graph scores from spreading activation, cap at 1.0.
            s_graph_norm = min(s_graph_raw * 10.0, 1.0)
            # FTS5 Lexical normalization via empirical constant
            s_lex_norm = min(s_lex_raw / 10.0, 1.0)

            final_scores[cmb_id] = s_vec + (alpha * s_graph_norm) + (beta * s_lex_norm)

        sorted_ids = sorted(
            final_scores.keys(), key=lambda cid: final_scores[cid], reverse=True
        )
        return sorted_ids

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
        remaining = budget - self._count_tokens(header)
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
            entry_tokens = self._count_tokens(entry)
            if entry_tokens > remaining:
                # Node does not fit — discard entirely, stop processing.
                break

            included.append(entry)
            remaining -= entry_tokens

        if not included:
            return "Retrieved Context: None"

        return header + "".join(included)

    def _count_tokens(self, text: str) -> int:
        """Count tokens using embedder if available, otherwise word-count estimate."""
        if self.embedder is not None:
            return self.embedder.get_token_count(text)
        # Lightweight fallback: ~1.3 tokens per word (conservative estimate)
        return int(len(text.split()) * 1.3)
