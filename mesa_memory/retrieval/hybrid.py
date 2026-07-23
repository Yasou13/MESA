import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.observability.metrics import PROM_RETRIEVAL_DEGRADED
from mesa_memory.retrieval.core import QueryAnalyzer, normalize_query
from mesa_memory.retrieval.legal_resolver import LegalEntityResolver
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO

if TYPE_CHECKING:
    from mesa_memory.retrieval.reranker import CrossEncoderReranker

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
        reranker: "CrossEncoderReranker | Any | None" = None,
    ):
        self.dao = dao
        self.analyzer = analyzer
        self.embedder = embedder
        self.access_control = access_control or AccessControl()
        self._agent_id = agent_id
        self.legal_resolver = LegalEntityResolver()
        self.reranker = reranker

    async def retrieve(
        self,
        query_text: str,
        agent_id: str,
        session_id: str,
        top_n: int = 5,
        enable_multi_hop: bool = False,
        collect_diagnostics: bool = False,
    ) -> list[str] | dict:
        t_start = time.perf_counter()
        stage_latencies: dict[str, float] = {}
        stage_diagnostics: dict[str, Any] = {}
        degraded_sources: set[str] = set()

        if not await self.access_control.check_access(agent_id, session_id, "READ"):
            raise PermissionError(
                f"Agent '{agent_id}' lacks READ access for session '{session_id}'"
            )
        normalized = normalize_query(query_text)
        t_decomp = time.perf_counter()
        entities = self.analyzer.extract_entities(normalized)

        # Inject ontology-based legal entities to fix multi-hop failures
        legal_entities = self.legal_resolver.extract_entities(query_text)
        entities.extend(legal_entities)
        entities = list(set(entities))
        stage_latencies["query_analysis_ms"] = (time.perf_counter() - t_decomp) * 1000

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

        vector_results: list[Any] = []
        graph_results: list[Any] = []

        t_vec_graph = time.perf_counter()
        if enable_multi_hop and self.embedder is not None:
            from mesa_memory.retrieval.decomposition import decompose_query

            subqueries = await decompose_query(normalized, self.embedder)
            vector_tasks = [
                self.get_vector_results(agent_id, sq, k=100) for sq in subqueries
            ]
        else:
            vector_tasks = [self.get_vector_results(agent_id, normalized, k=100)]

        graph_task = self.get_graph_results(agent_id, entities)

        async def _fts_with_timing():  # type: ignore[no-untyped-def]
            t0 = time.perf_counter()
            try:
                res = await self.dao.search_memory_fts(
                    agent_id, query=normalized, limit=100
                )
                return res, (time.perf_counter() - t0) * 1000
            except Exception as e:
                return e, (time.perf_counter() - t0) * 1000

        fts_task = _fts_with_timing()

        all_tasks = vector_tasks + [graph_task, fts_task]
        gather_results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # Pop in reverse order: FTS was appended last, Graph was appended before FTS
        fts_res_tuple = gather_results.pop()
        graph_res = gather_results.pop()

        stage_latencies["vector_and_graph_search_ms"] = (
            time.perf_counter() - t_vec_graph
        ) * 1000
        if isinstance(graph_res, BaseException):
            degraded_sources.add("graph")
            logger.error(
                "HYBRID_RETRIEVAL_GRAPH_FAILED | agent_id=%s error=%s",
                agent_id,
                graph_res,
                exc_info=graph_res,
            )
        else:
            graph_results = graph_res

        merged_vectors: dict[str, Any] = {}
        for res in gather_results:
            if isinstance(res, BaseException):
                degraded_sources.add("vector")
                logger.error(
                    "HYBRID_RETRIEVAL_VECTOR_FAILED | agent_id=%s error=%s",
                    agent_id,
                    res,
                    exc_info=res,
                )
            elif isinstance(res, list):
                for item in res:
                    cmb_id = item.get("cmb_id")
                    if cmb_id:
                        if (
                            cmb_id not in merged_vectors
                            or item["score"] > merged_vectors[cmb_id]["score"]
                        ):
                            merged_vectors[cmb_id] = item

        vector_results = sorted(
            merged_vectors.values(), key=lambda x: x["score"], reverse=True
        )
        for i, r in enumerate(vector_results):
            r["rank"] = i + 1
        lexical_results: list[dict] = []
        fts_res, fts_ms = (
            fts_res_tuple if isinstance(fts_res_tuple, tuple) else (fts_res_tuple, 0.0)
        )
        stage_latencies["fts_search_ms"] = fts_ms

        if isinstance(fts_res, BaseException):
            degraded_sources.add("lexical")
            logger.warning(
                "FTS5_SEARCH_FAILED | agent_id=%s — falling back to empty lexical results",
                agent_id,
                exc_info=fts_res,
            )
        else:
            # Normalize to ranking format
            lexical_results = [
                {
                    "cmb_id": r.get("id", ""),
                    "content_payload": r.get("entity_name", ""),
                    "score": abs(r.get("rank", 0.0)),
                    "source": "lexical",
                    "rank": i + 1,
                }
                for i, r in enumerate(fts_res)
            ]

        if degraded_sources:
            for source in sorted(degraded_sources):
                PROM_RETRIEVAL_DEGRADED.labels(source=source).inc()
            logger.warning(
                "HYBRID_RETRIEVAL_DEGRADED | sources=%s",
                sorted(degraded_sources),
            )

        pool_multiplier = getattr(config, "crossencoder_pool_multiplier", 3)
        pool_size = max(top_n * pool_multiplier, top_n)

        t_rerank = time.perf_counter()
        if is_cold_start or not graph_results:
            seen = set()
            combined_results = []
            for r in vector_results + lexical_results:
                if r["cmb_id"] not in seen:
                    seen.add(r["cmb_id"])
                    combined_results.append(r)

            combined_results = await self._exclude_quarantined_candidates(
                agent_id, combined_results
            )

            if not combined_results:
                candidate_ids: list[str] = []
            else:
                candidate_ids = [
                    r["cmb_id"]
                    for r in self._cold_start_rerank(
                        combined_results,
                        pool_size if self.reranker is not None else top_n,
                    )
                ]
        else:
            fused_ids = await self._apply_rrf_reranking(
                agent_id,
                vector_results,
                graph_results,
                lexical_results,
            )
            candidate_ids = (
                fused_ids[:pool_size]
                if self.reranker is not None
                else fused_ids[:top_n]
            )

        if self.reranker is not None and candidate_ids:
            contents = await self._fetch_contents_batch(agent_id, candidate_ids)
            cmb_ids = await self.reranker.rerank(
                query=query_text,
                candidates=contents,
                top_k=top_n,
            )
        else:
            cmb_ids = candidate_ids[:top_n]
        stage_latencies["rerank_ms"] = (time.perf_counter() - t_rerank) * 1000

        source_scores: dict[str, float] = {}
        for item in vector_results + graph_results + lexical_results:
            cmb_id = item.get("cmb_id")
            if not cmb_id:
                continue
            source_scores[cmb_id] = max(
                source_scores.get(cmb_id, 0.0), float(item.get("score", 0.0))
            )

        if collect_diagnostics:
            stage_diagnostics["extracted_entities"] = entities
            stage_diagnostics["vector_hits_count"] = len(vector_results)
            stage_diagnostics["graph_hits_count"] = len(graph_results)
            stage_diagnostics["lexical_hits_count"] = len(lexical_results)
            stage_diagnostics["pre_rerank_candidate_ids"] = candidate_ids[:15]
            stage_diagnostics["post_rerank_ids"] = cmb_ids
            stage_diagnostics["degraded_sources"] = sorted(degraded_sources)

        if not enable_multi_hop and not collect_diagnostics:
            return cmb_ids

        # --- Multi-hop graph traversal between top 2 seed entities ---
        # Uses KùzuDB's variable-length path traversal via DAO instead
        # of the legacy NetworkX snapshot.  Zero OOM risk.
        t_mh = time.perf_counter()
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
        stage_latencies["multi_hop_traversal_ms"] = (time.perf_counter() - t_mh) * 1000
        stage_latencies["total_retrieval_ms"] = (time.perf_counter() - t_start) * 1000

        result_dict = {
            "cmb_ids": cmb_ids,
            "source_scores": {
                cmb_id: source_scores.get(cmb_id, 0.0) for cmb_id in cmb_ids
            },
            "multi_hop_path": multi_hop_path,
            "latency_breakdown_ms": stage_latencies,
            "diagnostics": stage_diagnostics,
        }
        return result_dict

    async def _exclude_quarantined_candidates(
        self, agent_id: str, candidates: list[dict]
    ) -> list[dict]:
        """Apply the normal-path quarantine gate to cold-start candidates.

        The metadata lookup deliberately propagates failures: returning a
        candidate whose quarantine status cannot be checked is unsafe.
        """
        if not candidates:
            return []
        candidate_ids = [candidate["cmb_id"] for candidate in candidates]
        epistemic_data = await self.dao.get_epistemic_data_for_nodes(
            agent_id, candidate_ids
        )
        if not isinstance(epistemic_data, dict):
            epistemic_data = {}
        return [
            candidate
            for candidate in candidates
            if not epistemic_data.get(
                candidate["cmb_id"], {"is_quarantined": False}
            ).get("is_quarantined")
        ]

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

    async def _apply_rrf_reranking(
        self,
        agent_id: str,
        vector_ranks: list[dict],
        graph_ranks: list[dict],
        lexical_ranks: list[dict],
    ) -> list[str]:
        """Fuse vector, graph and lexical lanes using true RRF.

        Each lane contributes ``1 / (rrf_k + rank)`` once per candidate.
        This deliberately ignores incomparable raw similarity, BM25 and graph
        salience score spaces.  The final ID tie-breaker makes a fixed corpus
        replay deterministic.
        """
        rank_maps: list[dict[str, int]] = []
        for lane in (vector_ranks, graph_ranks, lexical_ranks):
            ranks: dict[str, int] = {}
            for position, item in enumerate(lane, start=1):
                candidate_id = str(item.get("cmb_id", ""))
                if not candidate_id:
                    continue
                supplied_rank = item.get("rank", position)
                try:
                    rank = int(supplied_rank)
                except (TypeError, ValueError):
                    rank = position
                # A malformed/non-positive rank must never amplify a lane.
                ranks[candidate_id] = max(1, rank)
            rank_maps.append(ranks)

        union_ids = set().union(*(set(ranks) for ranks in rank_maps))

        # DP3: Fetch Epistemic Data (Confidence & Quarantine flags)
        epistemic_data = await self.dao.get_epistemic_data_for_nodes(
            agent_id, list(union_ids)
        )

        final_scores: dict[str, float] = {}
        rrf_k = config.rrf_k
        for cmb_id in union_ids:
            e_data = epistemic_data.get(
                cmb_id, {"confidence": 1.0, "is_quarantined": False}
            )

            # Quarantined nodes are completely excluded from retrieval
            if e_data.get("is_quarantined"):
                continue

            confidence = float(e_data.get("confidence", 1.0))
            rrf_score = sum(
                1.0 / (rrf_k + ranks[cmb_id])
                for ranks in rank_maps
                if cmb_id in ranks
            )
            final_scores[cmb_id] = rrf_score * confidence

        sorted_ids = sorted(
            final_scores.keys(), key=lambda cid: (-final_scores[cid], cid)
        )
        return sorted_ids

    async def _apply_alpha_reranking(
        self,
        agent_id: str,
        vector_ranks: list[dict],
        graph_ranks: list[dict],
        lexical_ranks: list[dict],
    ) -> list[str]:
        """Compatibility alias for callers predating the V4 RRF contract."""
        return await self._apply_rrf_reranking(
            agent_id, vector_ranks, graph_ranks, lexical_ranks
        )

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

    async def _fetch_contents_batch(
        self, agent_id: str, cmb_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Fetch node contents in batch for CrossEncoder reranking."""
        if not cmb_ids:
            return []
        batch_map = await self.dao.get_nodes_by_ids_batch(agent_id, cmb_ids)
        results: list[dict[str, Any]] = []
        for cid in cmb_ids:
            node = batch_map.get(cid)
            if node:
                results.append(
                    {
                        "cmb_id": cid,
                        "content": node.get("content_payload", ""),
                        "entity_name": node.get("entity_name", ""),
                    }
                )
            else:
                results.append(
                    {
                        "cmb_id": cid,
                        "content": "",
                        "entity_name": "",
                    }
                )
        return results

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
