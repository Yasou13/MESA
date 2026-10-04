"""Bounded semantic query expansion above the deterministic V4 DAO search."""

from __future__ import annotations

import logging
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.retrieval.core import normalize_query
from mesa_memory.retrieval.legal_resolver import LegalEntityResolver
from mesa_storage.dao import MemoryDAO
from mesa_storage.legal_identity import normalize_turkish
from mesa_storage.retrieval_scope import V4_RRF_DEFAULT_K, rrf_fuse_lanes

logger = logging.getLogger("MESA_AdaptiveRetrieval")

MAX_EXPANSION_QUERIES = 2
MAX_QUERY_LENGTH = 4096
QUERY_RRF_WEIGHTS = {"Q0": 1.0, "Q1": 0.5, "Q2": 0.5}


class QueryExpansionOutput(BaseModel):
    """The complete structured output accepted from the planner."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    queries: list[str] = Field(default_factory=list, max_length=MAX_EXPANSION_QUERIES)


@dataclass(frozen=True)
class PlannerResult:
    expansions: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class AdaptiveRetrievalResult:
    candidates: list[dict[str, Any]]
    diagnostics: dict[str, Any]


def normalize_planner_query(query: str) -> str:
    """Apply only Unicode, whitespace and case normalization."""
    normalized_unicode = unicodedata.normalize("NFKC", query)
    normalized_turkish = normalize_turkish(normalized_unicode)
    return normalize_query(normalized_turkish)


def _citation_identities(query: str) -> set[tuple[str, str | None]]:
    return {
        (citation.statute_code, citation.article)
        for citation in LegalEntityResolver().extract_citations(query)
    }


def _introduces_citation(original: str, expansion: str) -> bool:
    original_citations = _citation_identities(original)
    original_statutes = {statute for statute, _ in original_citations}
    for statute, article in _citation_identities(expansion):
        if statute not in original_statutes:
            return True
        if article is not None and (statute, article) not in original_citations:
            return True
    return False


def _has_invalid_control_character(query: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in query)


class BoundedQueryPlanner:
    """Request and validate at most two semantic query reformulations."""

    def __init__(self, adapter: BaseUniversalLLMAdapter) -> None:
        self._adapter = adapter

    async def plan(self, original_query: str) -> PlannerResult:
        normalized_original = normalize_planner_query(original_query)
        prompt = (
            "Generate search reformulations only. Do not answer the question.\n"
            "Preserve the user's intent and use concise retrieval-oriented wording.\n"
            "Return at most two alternatives in the requested structured schema.\n"
            "Do not invent a statute or article citation not explicit in the query.\n"
            "Output only the structured schema; do not include reasoning.\n\n"
            f"User query: {normalized_original}"
        )
        try:
            raw_response = await self._adapter.acomplete(
                prompt, schema=QueryExpansionOutput
            )
        except ValidationError:
            return PlannerResult((), "planner_invalid_output_fallback_single")
        except Exception as exc:
            logger.warning(
                "QUERY_PLANNER_UNAVAILABLE | exception_type=%s", type(exc).__name__
            )
            return PlannerResult((), "planner_unavailable_fallback_single")

        try:
            if isinstance(raw_response, QueryExpansionOutput):
                response = raw_response
            elif isinstance(raw_response, BaseModel):
                response = QueryExpansionOutput.model_validate(
                    raw_response.model_dump()
                )
            else:
                response = QueryExpansionOutput.model_validate(raw_response)
        except (ValidationError, TypeError, ValueError):
            return PlannerResult((), "planner_invalid_output_fallback_single")

        accepted: list[str] = []
        seen = {normalized_original}
        for raw_query in response.queries:
            if (
                not raw_query.strip()
                or len(raw_query) > MAX_QUERY_LENGTH
                or _has_invalid_control_character(raw_query)
            ):
                continue
            normalized = normalize_planner_query(raw_query)
            if not normalized or normalized in seen:
                continue
            if _introduces_citation(normalized_original, normalized):
                continue
            seen.add(normalized)
            accepted.append(normalized)

        if not accepted:
            return PlannerResult((), "planner_no_valid_expansions")
        return PlannerResult(tuple(accepted), "planner_expanded")


def retrieval_is_weak(candidates: list[dict[str, Any]]) -> bool:
    """Return true unless a top-three result has support from two lanes."""
    if not candidates:
        return True
    for candidate in candidates[:3]:
        provenance = candidate.get("retrieval_provenance")
        origins = (
            provenance.get("origins", [])
            if isinstance(provenance, dict)
            else candidate.get("origins", [])
        )
        if isinstance(origins, (list, tuple, set)) and len(set(origins)) >= 2:
            return False
    return True


def _candidate_identity(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("assertion_id")
        or candidate.get("evidence_id")
        or candidate.get("candidate_id")
        or ""
    )


def fuse_query_results(
    query_results: list[tuple[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """Fuse query rankings without changing their inner retrieval semantics."""
    rankings: dict[str, list[str]] = {}
    candidates_by_identity: dict[str, dict[str, Any]] = {}
    for query_id, candidates in query_results:
        ranking: list[str] = []
        seen_in_query: set[str] = set()
        for candidate in candidates:
            identity = _candidate_identity(candidate)
            if not identity or identity in seen_in_query:
                continue
            seen_in_query.add(identity)
            ranking.append(identity)
            candidates_by_identity.setdefault(identity, candidate)
        rankings[query_id] = ranking

    fused: list[dict[str, Any]] = []
    for identity, fusion_score, query_ranks in rrf_fuse_lanes(
        rankings,
        k=V4_RRF_DEFAULT_K,
        weights=QUERY_RRF_WEIGHTS,
    ):
        candidate = deepcopy(candidates_by_identity[identity])
        query_origins = [
            {"query_id": query_id, "query_rank": query_ranks[query_id]}
            for query_id in ("Q0", "Q1", "Q2")
            if query_id in query_ranks
        ]
        retrieval_provenance = deepcopy(candidate.get("retrieval_provenance") or {})
        retrieval_provenance["query_origins"] = query_origins
        retrieval_provenance["query_fusion_score"] = fusion_score
        candidate["retrieval_provenance"] = retrieval_provenance
        candidate["query_fusion_score"] = fusion_score
        fused.append(candidate)
    fused.sort(
        key=lambda candidate: (
            -float(candidate["query_fusion_score"]),
            (
                0
                if any(
                    origin.get("query_id") == "Q0"
                    for origin in candidate["retrieval_provenance"]["query_origins"]
                )
                else 1
            ),
            _candidate_identity(candidate),
        )
    )
    return fused


class BoundedAdaptiveQueryRetriever:
    """Run Q0, optionally plan once, and reuse the same DAO search for Q1/Q2."""

    def __init__(self, dao: MemoryDAO, planner: BoundedQueryPlanner | None) -> None:
        self._dao = dao
        self._planner = planner

    async def retrieve(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        dataset_ids: list[str],
        query: str,
        limit: int = 20,
        jurisdiction: str | None = None,
        valid_at: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        graph_enabled: bool = True,
        request_principal_id: str | None = None,
    ) -> AdaptiveRetrievalResult:
        normalized_query = normalize_planner_query(query)
        scope = {
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "dataset_ids": dataset_ids,
            "limit": limit,
            "jurisdiction": jurisdiction,
            "valid_at": valid_at,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "graph_enabled": graph_enabled,
            "request_principal_id": request_principal_id,
        }
        original = await self._dao.search_v4_memory(
            query=normalized_query,
            **scope,
        )
        diagnostics: dict[str, Any] = {
            "retrieval_mode": "adaptive",
            "planner_used": False,
            "planner_status": "planner_not_needed",
            "expansion_count": 0,
            "retrieval_count": 1,
            "expansion_failure_count": 0,
        }
        if not retrieval_is_weak(original):
            return AdaptiveRetrievalResult(original, diagnostics)

        if self._planner is None:
            diagnostics["planner_status"] = "planner_unavailable_fallback_single"
            return AdaptiveRetrievalResult(original, diagnostics)

        diagnostics["planner_used"] = True
        plan = await self._planner.plan(normalized_query)
        diagnostics["planner_status"] = plan.status
        diagnostics["expansion_count"] = len(plan.expansions)
        if not plan.expansions:
            return AdaptiveRetrievalResult(original, diagnostics)

        successful_results: list[tuple[str, list[dict[str, Any]]]] = [("Q0", original)]
        for index, expansion in enumerate(plan.expansions, start=1):
            diagnostics["retrieval_count"] += 1
            try:
                expansion_results = await self._dao.search_v4_memory(
                    query=expansion,
                    **scope,
                )
            except Exception as exc:
                diagnostics["expansion_failure_count"] += 1
                logger.warning(
                    "QUERY_EXPANSION_RETRIEVAL_FAILED | query_id=Q%d exception_type=%s",
                    index,
                    type(exc).__name__,
                )
                continue
            successful_results.append((f"Q{index}", expansion_results))

        if len(successful_results) == 1:
            return AdaptiveRetrievalResult(original, diagnostics)
        return AdaptiveRetrievalResult(
            fuse_query_results(successful_results), diagnostics
        )
