"""Compatibility facade and canonical V4 storage orchestration.

SQLite owns V4 catalog, assertions, mutations, source/artifact ownership and
ordered projection outboxes. LanceDB and Kùzu are derived projections. V4 uses server-verified
principal/tenant/workspace/dataset/session scope and explicit storage filters.
An agent ID alone is not a tenant credential or an absolute isolation guarantee.

Mutation admission, validation receipts, fenced claims, projection convergence
and source-owned rollback have separate lifecycle states. Tombstones protect
retrieval eligibility before secondary cleanup; supported purge/reconciliation
paths can physically remove unowned derived artifacts. Stores do not share an
ACID transaction. Use public authenticated API/SDK paths for applications;
direct DAO callers must supply validated authorization and identity.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import sqlite3
import time
import unicodedata
import uuid
from collections import Counter, OrderedDict
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast
from urllib.parse import urlsplit, urlunsplit

import aiosqlite

from mesa_storage import legal_rules
from mesa_storage.kuzu_provider import GraphSearchError, KuzuGraphProvider
from mesa_storage.legal_identity import (
    LegalEntityResolver,
    canonicalize_trusted_legal_identity,
    normalize_law_name,
    normalize_turkish,
)
from mesa_storage.rebuild_health import RebuildHealthReader
from mesa_storage.repositories.catalog import CatalogRepository, CatalogRepositoryPort
from mesa_storage.repositories.operations import (
    OperationRepository,
    OperationRepositoryPort,
    RebuildAdmissionPort,
    RebuildAdmissionReader,
)
from mesa_storage.representation import (
    V4_ASSERTION_REPRESENTATION_VERSION,
    V4_VECTOR_CONSUMER_CONTRACT,
    V4_VECTOR_REPRESENTATION_VERSION,
    build_v4_assertion_vector_payload,
)
from mesa_storage.retrieval_scope import (
    V4_GRAPH_ABLATION_CONTRACT_VERSION,
    V4_RRF_DEFAULT_K,
    V4_RRF_LANE_WEIGHTS,
    V4_SCOPE_AUDIT_CONTRACT_VERSION,
    build_v4_lexical_query,
    rrf_fuse_lanes,
    stable_contract_hash,
    stable_graph_path_id,
)
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import (
    SemanticRuntimeDisabledError,
    VectorEngine,
    VectorSearchError,
)

logger = logging.getLogger("MESA_DAO")

V4_VECTOR_LANE_DIAGNOSTIC_CONTRACT_VERSION = "mesa.vector-lane.v1"


def _retry_delay_seconds(attempt_count: int) -> int:
    """Back off failed durable work from one minute to at most fifteen minutes."""
    return min(60 * 2 ** (attempt_count - 1), 900)


def _assertion_legal_identity(assertion: Mapping[str, Any]) -> dict[str, str] | None:
    """Return only canonical legal identity persisted from a trusted source."""
    source = str(assertion.get("legal_identity_source") or "")
    law_id = str(assertion.get("law_id") or "")
    statute_code = str(assertion.get("statute_code") or "")
    article_id = str(assertion.get("article_id") or "")
    if source not in {"catalog", "verified_ingestion"}:
        return None
    if not law_id or not statute_code or not article_id:
        return None
    identity = {
        "law_id": law_id,
        "statute_code": statute_code,
        "article_id": article_id,
        "version_id": str(assertion.get("legal_version_id") or ""),
        "identity_source": source,
    }
    for key in ("paragraph_id", "clause_id"):
        value = str(assertion.get(key) or "")
        if value:
            identity[key] = value
    return identity


def _decode_registry_metadata(value: Any) -> dict[str, Any]:
    """Decode registry metadata without ever treating malformed data as valid."""
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _public_tier3_audit(value: Any) -> dict[str, Any] | None:
    """Whitelist the bounded Tier-3 receipt safe for status endpoints."""
    if not isinstance(value, dict):
        return None
    decisions = value.get("decisions")
    justifications = value.get("justifications")
    models = value.get("models")
    if not isinstance(decisions, dict):
        return None
    if not isinstance(justifications, dict):
        return None
    if not isinstance(models, dict):
        return None
    allowed_decisions = {
        "STORE",
        "DISCARD",
        "NOT_RUN",
        "NOT_AVAILABLE",
        "SKIPPED_BY_POLICY",
    }
    primary = decisions.get("primary")
    secondary = decisions.get("secondary")
    if primary not in allowed_decisions or secondary not in allowed_decisions:
        return None
    result = {
        "route": str(value.get("route", "unknown"))[:64],
        "route_reason": str(value.get("route_reason", "unknown"))[:96],
        "decisions": {"primary": primary, "secondary": secondary},
        "justifications": {
            "primary": str(justifications.get("primary", ""))[:280],
            "secondary": (
                str(justifications["secondary"])[:280]
                if isinstance(justifications.get("secondary"), str)
                else None
            ),
        },
        "models": {
            "primary": str(models.get("primary", "unknown"))[:160],
            "secondary": (
                str(models["secondary"])[:160]
                if isinstance(models.get("secondary"), str)
                else None
            ),
        },
        "prompt_version": str(value.get("prompt_version", "unknown"))[:96],
        "reason": str(value.get("reason", "tier3_rejected"))[:120],
        "accepted": bool(value.get("accepted")),
    }
    return result


# ---------------------------------------------------------------------------
# Sentinel rejection — defence in depth
# ---------------------------------------------------------------------------

_FORBIDDEN_AGENT_IDS = frozenset({"__unset__", "__system__", ""})
_PURGE_MAX_RETRIES = 3
_V4_ENTITY_NAMESPACE = uuid.UUID("b6b5dc86-5fc0-4d9a-a6f8-a1f92b28e76f")
_V4_ASSERTION_NAMESPACE = uuid.UUID("b526e36d-e4f5-4d30-9d10-c4d68b2f5428")
_V4_CATALOG_NAMESPACE = uuid.UUID("5a227c0d-26ee-47db-98f8-48545636143f")
_WHITESPACE_RE = re.compile(r"\s+")
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(
        r"\b(?:password|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)


_INTERNAL_CATALOG_ID_PREFIXES = (
    "mesa-tenant-",
    "mesa-workspace-",
    "mesa-dataset-",
    "mesa-session-",
    "mesa-document-",
    "mesa-revision-",
    "mesa-chunk-",
)

_INTERNAL_ID_ROW_FIELDS = (
    "document_id",
    "revision_id",
    "chunk_id",
    "assertion_id",
    "mutation_id",
    "subject_id",
    "object_entity_id",
)


def _is_internal_scoring_id(
    value: str, row: Mapping[str, Any] | None = None
) -> bool:
    if not value:
        return False
    if value.startswith(_INTERNAL_CATALOG_ID_PREFIXES):
        return True
    if row is not None:
        for field in _INTERNAL_ID_ROW_FIELDS:
            raw = row.get(field)
            if raw and value == str(raw):
                return True
    return False


def _scoring_entity_name(
    value: Any, row: Mapping[str, Any] | None = None
) -> str:
    text = str(value or "")
    return "" if _is_internal_scoring_id(text, row) else text


def _validate_write_payload(content: str, metadata: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(metadata, sort_keys=True, default=str).encode()
    if len(encoded) > 16 * 1024:
        raise ValueError("metadata exceeds 16 KB")
    _validate_metadata_depth(metadata, depth=1)
    serialised = content + "\n" + encoded.decode(errors="replace")
    if any(pattern.search(serialised) for pattern in _SECRET_PATTERNS):
        raise ValueError("content or metadata appears to contain a secret")
    return metadata


def _validate_metadata_depth(value: dict[str, Any], *, depth: int) -> None:
    if depth > 2:
        raise ValueError("metadata nesting exceeds the supported depth")
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        if key.startswith("_mesa_"):
            raise ValueError("metadata keys beginning with '_mesa_' are reserved")
        if isinstance(item, dict):
            _validate_metadata_depth(item, depth=depth + 1)
        elif isinstance(item, list):
            if depth >= 2 or any(isinstance(entry, (dict, list)) for entry in item):
                raise ValueError("metadata nesting exceeds the supported depth")


class PurgeRetryPendingError(RuntimeError):
    """A purge remains tombstoned and requires an idempotent retry."""


class NonHeadRollbackConflictError(ValueError):
    """A historical revision cannot be rolled back beneath a newer head."""


class NonReplayableMutationConflictError(ValueError):
    """A semantically rejected mutation cannot be replayed."""


class QueueAdmissionError(RuntimeError):
    """Base class for stable, non-sensitive queue admission outcomes."""


class QueueOverCapacityError(QueueAdmissionError):
    def __init__(self, scope: str) -> None:
        super().__init__("queue capacity exhausted")
        self.scope = scope


class QueueRecordTooLargeError(QueueAdmissionError):
    """The deterministic serialized envelope exceeds the single-record bound."""


class QueueUnavailableError(QueueAdmissionError):
    """The durable admission coordinator is unavailable; callers must fail closed."""


_ADMISSION_ACTIVE_STATES = (
    "ENQUEUED",
    "PENDING",
    "CLAIMED",
    "IN_FLIGHT",
    "RETRY_PENDING",
    "DEFERRED",
)


class QueueAdmissionPolicy(Protocol):
    """Storage-owned structural contract for queue capacity inputs."""

    queue_max_pending_records: int
    queue_max_pending_bytes: int
    queue_max_pending_records_per_tenant: int
    queue_max_pending_bytes_per_tenant: int
    queue_max_in_flight_records: int
    queue_max_in_flight_records_per_tenant: int
    queue_max_retry_pending_records: int
    queue_max_retry_pending_records_per_tenant: int
    queue_max_single_record_bytes: int


class _DefaultQueueAdmissionPolicy:
    queue_max_pending_records = 10_000
    queue_max_pending_bytes = 536_870_912
    queue_max_pending_records_per_tenant = 2_000
    queue_max_pending_bytes_per_tenant = 134_217_728
    queue_max_in_flight_records = 32
    queue_max_in_flight_records_per_tenant = 8
    queue_max_retry_pending_records = 2_000
    queue_max_retry_pending_records_per_tenant = 500
    queue_max_single_record_bytes = 8_388_608


_DEFAULT_QUEUE_ADMISSION_POLICY = _DefaultQueueAdmissionPolicy()


def _canonical_payload_bytes(payload: dict[str, Any]) -> tuple[str, int]:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return serialized, len(serialized.encode("utf-8"))


def _assert_valid_agent_id(agent_id: str) -> None:
    """Reject structurally invalid agent_id values at the DAO boundary.

    Raises:
        ValueError: If agent_id is empty, None, or a reserved sentinel.
    """
    if not agent_id or agent_id in _FORBIDDEN_AGENT_IDS:
        raise ValueError(
            f"agent_id must be a non-empty, non-reserved identifier. Got: {agent_id!r}"
        )


def _normalize_identity_text(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFC", value)
    cleaned = (
        normalized.replace("İ", "i")
        .replace("I", "ı")
        .replace("\u0130", "i")
        .replace("\u0131", "ı")
        .replace("i\u0307", "i")
    )
    cleaned = unicodedata.normalize("NFKC", cleaned)
    return _WHITESPACE_RE.sub(" ", cleaned.strip()).casefold()


def normalize_planner_query(query: str) -> str:
    """Apply Unicode, Turkish case and whitespace normalization."""
    return " ".join(normalize_turkish(unicodedata.normalize("NFKC", query)).split())


def introduces_legal_citation(original: str, expansion: str) -> bool:
    """Reject new statutes or articles introduced by a query reformulation."""
    resolver = LegalEntityResolver()
    original_citations = {
        (citation.statute_code, citation.article)
        for citation in resolver.extract_citations(original)
    }
    original_statutes = {statute for statute, _ in original_citations}
    for citation in resolver.extract_citations(expansion):
        statute, article = citation.statute_code, citation.article
        if statute not in original_statutes:
            return True
        if article is not None and (statute, article) not in original_citations:
            return True
    return False


def _normalize_ontology_uri(value: str) -> str:
    """Normalize URI scheme/host without changing a case-sensitive path."""
    raw = unicodedata.normalize("NFKC", value).strip()
    if not raw:
        raise ValueError("ontology_uri cannot be blank")
    parts = urlsplit(raw)
    if parts.scheme:
        return urlunsplit(
            (
                parts.scheme.casefold(),
                parts.netloc.casefold(),
                parts.path,
                parts.query,
                parts.fragment,
            )
        )
    # Compact legal identifiers such as ``mevzuat:6698`` are valid identity
    # URIs even without a hierarchical host.
    return raw


_DATE_REGEX = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?|\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4})$"
)
_TR_DATE_REGEX = re.compile(
    r"^(?:\d{1,2}\s+)?(?:ocak|şubat|mart|nisan|mayıs|haziran|temmuz|ağustos|eylül|ekim|kasım|aralık)\s+\d{4}$",
    re.IGNORECASE,
)
_TR_YEAR_REGEX = re.compile(r"^\d{4}\s+(?:yılı|senesi)$", re.IGNORECASE)
_QUANTITY_REGEX = re.compile(
    r"^(?:(?:\d+(?:[.,]\d+)*)|(?:bir|iki|üç|dört|beş|altı|yedi|sekiz|dokuz|on|yirmi|otuz|kırk|elli|altmış|yetmiş|seksen|doksan|yüz|bin))\s*(?:gün|ay|yıl|sene|hafta|saat|dakika|saniye|kişi|adet|parça|tane|lira|tl|usd|eur|dolar|euro|₺|\$|€|kg|kilogram|gr|gram|km|kilometre|metre|m2|m3|ton|derece)(?:\s+(?:süre|boyunca|içinde|kadar))?$",
    re.IGNORECASE,
)
_PERCENT_REGEX = re.compile(
    r"^(?:%(?:\s*)\d+(?:[.,]\d+)?|yüzde\s+\d+(?:[.,]\d+)?|binde\s+\d+(?:[.,]\d+)?)$",
    re.IGNORECASE,
)
_ARTICLE_REF_REGEX = re.compile(
    r"\b(?:m\.|md\.?|madde|fıkra|bent)\s*\d+|\b\d+\.\s*madde", re.IGNORECASE
)
_LEGAL_STATUTES = {
    "TBK",
    "TMK",
    "TCK",
    "TTK",
    "HMK",
    "CMK",
    "İYUK",
    "VUK",
    "İİK",
    "ANAYASA",
    "KVKK",
    "İŞ KANUNU",
    "4857",
    "6098",
    "4721",
    "5237",
    "5271",
    "6100",
    "6102",
    "2577",
    "6698",
}


def classify_graph_object(
    *,
    tail: str | None,
    literal_value: str | None,
    object_type: str | None = None,
) -> tuple[str | None, str | None, str]:
    """Classify graph object into (tail_entity_name, literal_value, resolved_type).

    Supported types:
    - ENTITY: Concise named entity (creates graph node).
    - LITERAL: Primitive value, quantity, duration, description, or phrase (no graph node).
    - LEGAL_REFERENCE: Reference to a statute or code citation (no graph node).
    - ARTICLE_REFERENCE: Reference to a specific legal article (no graph node).
    - DATE: ISO date or natural temporal expression (no graph node).
    """
    raw_type = (object_type or "").strip().upper()
    if raw_type:
        if raw_type in ("LITERAL", "TEXT", "STRING", "NUMBER"):
            val = literal_value if literal_value is not None else tail
            return (None, val, "LITERAL")
        if raw_type in ("DATE", "TEMPORAL", "TIME", "DATETIME"):
            val = literal_value if literal_value is not None else tail
            return (None, val, "DATE")
        if raw_type == "ARTICLE_REFERENCE":
            val = literal_value if literal_value is not None else tail
            return (None, val, "ARTICLE_REFERENCE")
        if raw_type in ("LEGAL_REFERENCE", "STATUTE_REFERENCE"):
            val = literal_value if literal_value is not None else tail
            return (None, val, "LEGAL_REFERENCE")
        if raw_type == "ENTITY":
            val = tail if tail is not None else literal_value
            return (val, None, "ENTITY")

    # Inferred classification
    if literal_value is not None and tail is None:
        cleaned_lit = literal_value.strip()
        if (
            _DATE_REGEX.match(cleaned_lit)
            or _TR_DATE_REGEX.match(cleaned_lit)
            or _TR_YEAR_REGEX.match(cleaned_lit)
        ):
            return (None, literal_value, "DATE")
        if _ARTICLE_REF_REGEX.search(cleaned_lit):
            return (None, literal_value, "ARTICLE_REFERENCE")
        norm_upper = cleaned_lit.upper()
        if any(norm_upper.startswith(s) for s in _LEGAL_STATUTES):
            return (None, literal_value, "LEGAL_REFERENCE")
        return (None, literal_value, "LITERAL")

    if tail is not None and literal_value is None:
        cleaned_tail = tail.strip()

        # 1. Date/temporal detection
        if (
            _DATE_REGEX.match(cleaned_tail)
            or _TR_DATE_REGEX.match(cleaned_tail)
            or _TR_YEAR_REGEX.match(cleaned_tail)
        ):
            return (None, cleaned_tail, "DATE")

        # 2. Quantity / duration / currency / percentage / measurement
        if _QUANTITY_REGEX.match(cleaned_tail) or _PERCENT_REGEX.match(cleaned_tail):
            return (None, cleaned_tail, "LITERAL")

        # 3. Article reference detection
        if _ARTICLE_REF_REGEX.search(cleaned_tail):
            return (None, cleaned_tail, "ARTICLE_REFERENCE")

        # 4. Legal reference citation detection
        norm_upper = cleaned_tail.upper()
        if any(
            norm_upper == s or norm_upper.startswith(f"{s} ") for s in _LEGAL_STATUTES
        ) or any(
            citation.statute_code.upper() in _LEGAL_STATUTES
            for citation in LegalEntityResolver().extract_citations(cleaned_tail)
        ):
            return (None, cleaned_tail, "LEGAL_REFERENCE")

        # 5. Long phrase detection: prevent auto-entity node explosion
        words = cleaned_tail.split()
        has_clause_punctuation = any(p in cleaned_tail for p in (";", "\n", "\t"))
        tail_no_abbr = re.sub(
            r"\b(?:T\.C\.|A\.Ş\.|Ltd\.|Şti\.|Dr\.|Prof\.|Av\.|vd\.|vb\.|vs\.)",
            "",
            cleaned_tail,
            flags=re.IGNORECASE,
        )
        has_sentence_period = bool(
            re.search(r"\.\s+[a-zA-ZçğıöşüÇĞİÖŞÜ]", tail_no_abbr)
        ) or (tail_no_abbr.strip().endswith(".") and len(words) >= 3)
        if (
            len(cleaned_tail) > 60
            or len(words) > 6
            or has_clause_punctuation
            or has_sentence_period
        ):
            return (None, cleaned_tail, "LITERAL")

        # 6. Concise named entity
        return (cleaned_tail, None, "ENTITY")

    return (tail, literal_value, "ENTITY" if tail else "LITERAL")


# ---------------------------------------------------------------------------
# Core DAO
# ---------------------------------------------------------------------------


class MemoryDAO:
    """Unified Data Access Object enforcing per-agent epistemic isolation.

    Every public method requires ``agent_id`` as its first positional
    argument.  All SQL queries embed ``WHERE agent_id = ?`` via
    parameterised binding.  All LanceDB filters embed
    ``agent_id = '<value>'`` in the WHERE clause.  All Cypher queries
    bind ``$agent_id`` via strict parameterisation.

    Edge storage is owned exclusively by KùzuDB.  SQLite retains
    ownership of canonical V4 tables and raw_logs.

    This class does NOT own the lifecycle of the underlying engines —
    the caller is responsible for initialising and closing them.

    Args:
        sqlite_engine: An initialised ``AsyncEngine`` instance.
        vector_engine: An initialised ``VectorEngine`` instance.
        graph_provider: An initialised ``KuzuGraphProvider`` instance
                        for graph edge operations.
    """

    __slots__ = (
        "_sql",
        "_vec",
        "_graph",
        "_catalog",
        "_operations",
        "_rebuild_admission",
        "_rebuild_health",
        "_canonical_v4_writes_enabled",
        "_embedding_enabled",
        "_graph_enabled",
        "_rrf_k",
        "_rrf_weights",
        "_search_scope_cache_enabled",
        "_legal_rules_enabled",
        "_search_scope_cache",
        "_search_cache_db",
        "_search_data_version",
        "_reranker",
        "_rerank_depth",
    )

    def __init__(
        self,
        sqlite_engine: AsyncEngine,
        vector_engine: VectorEngine,
        graph_provider: KuzuGraphProvider | None = None,
        *,
        canonical_v4_writes_enabled: bool = True,
        embedding_enabled: bool = True,
        graph_enabled: bool = True,
        rrf_k: int = V4_RRF_DEFAULT_K,
        rrf_weights: Mapping[str, float] | None = None,
        search_scope_cache_enabled: bool = True,
        legal_rules_enabled: bool = True,
        reranker: Any = None,
        rerank_depth: int = 50,
    ) -> None:
        self._sql = sqlite_engine
        self._vec = vector_engine
        self._graph = graph_provider
        self._catalog = CatalogRepository(sqlite_engine)
        self._operations = OperationRepository(sqlite_engine)
        self._rebuild_admission = RebuildAdmissionReader(sqlite_engine)
        self._rebuild_health = RebuildHealthReader(sqlite_engine)
        self._canonical_v4_writes_enabled = canonical_v4_writes_enabled
        self._embedding_enabled = embedding_enabled
        self._graph_enabled = graph_enabled
        self._rrf_k = rrf_k
        self._rrf_weights = dict(rrf_weights or V4_RRF_LANE_WEIGHTS)
        self._search_scope_cache_enabled = search_scope_cache_enabled
        self._legal_rules_enabled = legal_rules_enabled
        self._search_scope_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = (
            OrderedDict()
        )
        self._search_cache_db: aiosqlite.Connection | None = None
        self._search_data_version: int | None = None
        self._reranker = reranker
        self._rerank_depth = rerank_depth

    @property
    def catalog(self) -> CatalogRepositoryPort:
        """Expose catalog persistence without leaking the SQLite engine."""
        return self._catalog

    @property
    def operations(self) -> OperationRepositoryPort:
        """Expose durable system operations without leaking SQLite ownership."""
        return self._operations

    @property
    def rebuild_admission(self) -> RebuildAdmissionPort:
        """Expose a content-free maintenance admission signal."""
        return self._rebuild_admission

    @property
    def canonical_v4_writes_enabled(self) -> bool:
        """Whether this runtime owns an executable V4 validation/projection path."""
        return self._canonical_v4_writes_enabled

    @property
    def embedding_enabled(self) -> bool:
        """Whether this runtime may produce or query semantic vectors."""
        return self._embedding_enabled

    @property
    def graph_enabled(self) -> bool:
        """Whether this runtime may open, project or search the graph."""
        return self._graph_enabled

    @property
    def graph_operational(self) -> bool:
        """Return whether the graph provider is operational."""
        if not self.graph_implementation_available:
            return False
        return getattr(self._graph, "is_operational", False) is True

    @property
    def graph_configured(self) -> bool:
        """Return whether a graph provider was composed for this runtime."""
        return self._graph is not None

    @property
    def graph_implementation_available(self) -> bool:
        """Return whether the composed provider implements Graph V2 retrieval."""
        return self._graph is not None and callable(
            getattr(self._graph, "search_v4_graph", None)
        )

    async def submit_system_operation(
        self,
        *,
        requested_by_principal_id: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any]:
        return await self._operations.submit(
            requested_by_principal_id=requested_by_principal_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )

    async def get_system_operation(self, operation_id: str) -> dict[str, Any] | None:
        return await self._operations.get(operation_id)

    async def initialize(self) -> None:
        """Initialize the DAO layer.

        DDL ownership lives exclusively in ``schemas.py`` which MUST be
        called via ``initialize_schema(engine)`` before this method.
        """
        logger.info("MemoryDAO.initialize() — schema owned by schemas.py")

        parity = await self.reconcile_v4_projection_parity(limit=200, repair=True)
        recovered_logs = await self.recover_expired_raw_log_claims()
        if recovered_logs:
            logger.warning(
                "STARTUP_CLAIM_RECOVERY | raw_logs=%d",
                recovered_logs,
            )
        if parity["missing_artifacts"]:
            logger.warning("V4_PROJECTION_PARITY_RECOVERY | %s", parity)

    # ------------------------------------------------------------------
    # v4 catalog / canonical identity
    # ------------------------------------------------------------------

    async def create_v4_workspace(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        tenant_name: str | None = None,
        workspace_name: str | None = None,
    ) -> dict[str, Any]:
        return await self._catalog.create_workspace(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            tenant_name=tenant_name,
            workspace_name=workspace_name,
        )

    async def list_v4_workspaces(self, *, tenant_id: str) -> list[dict[str, Any]]:
        return await self._catalog.list_workspaces(tenant_id=tenant_id)

    async def ensure_v4_catalog_scope(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        tenant_name: str | None = None,
        workspace_name: str | None = None,
        dataset_name: str | None = None,
    ) -> None:
        await self._catalog.ensure_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            tenant_name=tenant_name,
            workspace_name=workspace_name,
            dataset_name=dataset_name,
        )

    async def create_v4_document(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        title: str,
        external_ref: str | None = None,
    ) -> dict[str, Any]:
        """Create an immutable dataset-owned document identity."""
        if not tenant_id or not dataset_id or not document_id or not title:
            raise ValueError("complete document identity is required")
        external_dataset_id = dataset_id
        external_document_id = document_id
        async with self._sql.transaction() as db:
            dataset_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="dataset",
                external_id=external_dataset_id,
            )
            document_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="document",
                external_id=external_document_id,
                create=True,
            )
            async with db.execute(
                "SELECT tenant_id FROM datasets WHERE dataset_id = ?",
                (dataset_id,),
            ) as cursor:
                dataset = await cursor.fetchone()
            if dataset is None or dataset[0] != tenant_id:
                raise ValueError("dataset does not belong to tenant")
            await db.execute(
                "INSERT OR IGNORE INTO documents "
                "(document_id, tenant_id, dataset_id, external_ref, title) "
                "VALUES (?, ?, ?, ?, ?)",
                (document_id, tenant_id, dataset_id, external_ref, title),
            )
            async with db.execute(
                "SELECT * FROM documents WHERE document_id = ?",
                (document_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if (
                row is None
                or row["tenant_id"] != tenant_id
                or row["dataset_id"] != dataset_id
            ):
                raise ValueError("document identity collides with another dataset")
            if external_ref and row["external_ref"] not in (None, external_ref):
                raise ValueError("document external_ref is immutable")
            await db.commit()
        result = dict(row)
        result["dataset_id"] = external_dataset_id
        result["document_id"] = external_document_id
        return result

    async def list_v4_documents(
        self, *, tenant_id: str, dataset_id: str
    ) -> list[dict[str, Any]]:
        async with self._sql.connection() as db:
            dataset_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="dataset",
                external_id=dataset_id,
            )
            async with db.execute(
                "SELECT i.external_id AS document_id, d.tenant_id, "
                "di.external_id AS dataset_id, d.external_ref, d.title, "
                "d.status, d.created_at FROM documents d "
                "JOIN v4_catalog_identities i ON i.tenant_id = d.tenant_id "
                "AND i.kind = 'document' AND i.physical_id = d.document_id "
                "JOIN v4_catalog_identities di ON di.tenant_id = d.tenant_id "
                "AND di.kind = 'dataset' AND di.physical_id = d.dataset_id "
                "WHERE d.tenant_id = ? AND d.dataset_id = ? "
                "ORDER BY d.created_at, i.external_id",
                (tenant_id, dataset_id),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def create_v4_revision(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        revision_id: str,
        revision_number: int,
        content_hash: str,
        supersedes_revision_id: str | None = None,
    ) -> dict[str, Any]:
        """Create one immutable document revision and retain its predecessor."""
        normalized_hash = content_hash.strip().casefold()
        if (
            not tenant_id
            or not document_id
            or not revision_id
            or revision_number < 1
            or not re.fullmatch(r"[0-9a-f]{64}", normalized_hash)
        ):
            raise ValueError("complete revision identity and SHA-256 hash are required")
        external_dataset_id = dataset_id
        external_document_id = document_id
        external_revision_id = revision_id
        external_supersedes_id = supersedes_revision_id
        async with self._sql.transaction() as db:
            dataset_id = await self._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
            )
            document_id = await self._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="document", external_id=document_id
            )
            revision_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="revision",
                external_id=revision_id,
                create=True,
            )
            if supersedes_revision_id:
                supersedes_revision_id = await self._catalog.resolve_id_in_tx(
                    db,
                    tenant_id=tenant_id,
                    kind="revision",
                    external_id=supersedes_revision_id,
                )
            async with db.execute(
                "SELECT tenant_id, dataset_id FROM documents WHERE document_id = ? "
                "AND status != 'PURGED'",
                (document_id,),
            ) as cursor:
                document = await cursor.fetchone()
            if (
                document is None
                or document["tenant_id"] != tenant_id
                or document["dataset_id"] != dataset_id
            ):
                raise ValueError("document does not belong to dataset")
            if supersedes_revision_id:
                async with db.execute(
                    "SELECT document_id FROM document_revisions WHERE revision_id = ?",
                    (supersedes_revision_id,),
                ) as cursor:
                    predecessor = await cursor.fetchone()
                if predecessor is None or predecessor[0] != document_id:
                    raise ValueError("superseded revision is outside document scope")
            await db.execute(
                "INSERT OR IGNORE INTO document_revisions "
                "(revision_id, tenant_id, document_id, revision_number, content_hash, "
                "status, supersedes_revision_id, declared_content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    revision_id,
                    tenant_id,
                    document_id,
                    revision_number,
                    normalized_hash,
                    # A revision without chunks is only a draft.  It becomes
                    # the document head when its canonical mutation commits.
                    "PENDING",
                    supersedes_revision_id,
                    normalized_hash,
                ),
            )
            async with db.execute(
                "SELECT * FROM document_revisions WHERE revision_id = ?",
                (revision_id,),
            ) as cursor:
                row = await cursor.fetchone()
            expected = (
                tenant_id,
                document_id,
                revision_number,
                normalized_hash,
                supersedes_revision_id,
            )
            actual = (
                (
                    row["tenant_id"],
                    row["document_id"],
                    row["revision_number"],
                    row["content_hash"],
                    row["supersedes_revision_id"],
                )
                if row is not None
                else None
            )
            if actual != expected:
                raise ValueError("revision identity is immutable and already differs")
            assert row is not None
            await db.commit()
        result = dict(row)
        result["document_id"] = external_document_id
        result["revision_id"] = external_revision_id
        result["supersedes_revision_id"] = external_supersedes_id
        result["dataset_id"] = external_dataset_id
        return result

    async def list_v4_revisions(
        self, *, tenant_id: str, dataset_id: str, document_id: str
    ) -> list[dict[str, Any]]:
        async with self._sql.connection() as db:
            dataset_id = await self._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
            )
            document_id = await self._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="document", external_id=document_id
            )
            async with db.execute(
                "SELECT ri.external_id AS revision_id, r.tenant_id, "
                "di.external_id AS document_id, r.revision_number, "
                "r.content_hash, r.declared_content_hash, r.manifest_hash, r.status, r.supersedes_revision_id, r.created_at "
                "FROM document_revisions r JOIN documents d "
                "ON d.document_id = r.document_id "
                "JOIN v4_catalog_identities ri ON ri.tenant_id = r.tenant_id "
                "AND ri.kind = 'revision' AND ri.physical_id = r.revision_id "
                "JOIN v4_catalog_identities di ON di.tenant_id = r.tenant_id "
                "AND di.kind = 'document' AND di.physical_id = r.document_id "
                "WHERE r.tenant_id = ? AND d.dataset_id = ? AND r.document_id = ? "
                "ORDER BY r.revision_number, r.revision_id",
                (tenant_id, dataset_id, document_id),
            ) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            for row in rows:
                if row["supersedes_revision_id"]:
                    row[
                        "supersedes_revision_id"
                    ] = await self._catalog.external_id_in_tx(
                        db,
                        tenant_id=tenant_id,
                        kind="revision",
                        physical_id=str(row["supersedes_revision_id"]),
                    )
            return rows

    async def list_v4_revision_mutations(
        self, *, tenant_id: str, dataset_id: str, revision_id: str
    ) -> dict[int, str]:
        """Read durable chunk admissions for a scoped document upload retry."""
        async with self._sql.connection() as db:
            dataset_id = await self._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
            )
            revision_id = await self._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="revision", external_id=revision_id
            )
            async with db.execute(
                "SELECT c.ordinal, m.mutation_id FROM memory_mutations m "
                "JOIN source_chunks c ON c.chunk_id = m.chunk_id "
                "WHERE m.tenant_id = ? AND m.dataset_id = ? AND m.revision_id = ? "
                "ORDER BY c.ordinal",
                (tenant_id, dataset_id, revision_id),
            ) as cursor:
                return {int(row[0]): str(row[1]) for row in await cursor.fetchall()}

    async def create_v4_source_chunk(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        revision_id: str,
        chunk_id: str,
        title: str,
        content_payload: str,
        source_ref: str,
        revision_number: int = 1,
        chunk_ordinal: int = 0,
        external_ref: str | None = None,
        supersedes_revision_id: str | None = None,
        finalize_revision: bool = True,
    ) -> dict[str, Any]:
        """Create immutable document/revision/chunk provenance."""
        if not all(
            (
                tenant_id,
                dataset_id,
                document_id,
                revision_id,
                chunk_id,
                title,
                content_payload,
                source_ref,
            )
        ):
            raise ValueError("complete document provenance is required")
        external_dataset_id = dataset_id
        external_document_id = document_id
        external_revision_id = revision_id
        external_chunk_id = chunk_id
        external_supersedes_id = supersedes_revision_id
        _validate_write_payload(content_payload, {})
        content_hash = hashlib.sha256(content_payload.encode("utf-8")).hexdigest()
        async with self._sql.transaction() as db:
            dataset_id = await self._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
            )
            document_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="document",
                external_id=document_id,
                create=True,
            )
            revision_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="revision",
                external_id=revision_id,
                create=True,
            )
            chunk_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="chunk",
                external_id=chunk_id,
                create=True,
            )
            if supersedes_revision_id:
                supersedes_revision_id = await self._catalog.resolve_id_in_tx(
                    db,
                    tenant_id=tenant_id,
                    kind="revision",
                    external_id=supersedes_revision_id,
                )
            async with db.execute(
                "SELECT tenant_id FROM datasets WHERE dataset_id = ?", (dataset_id,)
            ) as cursor:
                dataset = await cursor.fetchone()
            if dataset is None or dataset[0] != tenant_id:
                raise ValueError("dataset does not belong to tenant")
            await db.execute(
                "INSERT OR IGNORE INTO documents "
                "(document_id, tenant_id, dataset_id, external_ref, title) "
                "VALUES (?, ?, ?, ?, ?)",
                (document_id, tenant_id, dataset_id, external_ref, title),
            )
            async with db.execute(
                "SELECT tenant_id, dataset_id FROM documents WHERE document_id = ?",
                (document_id,),
            ) as cursor:
                document = await cursor.fetchone()
            if document is None or tuple(document) != (tenant_id, dataset_id):
                raise ValueError("document identity collides with another dataset")
            async with db.execute(
                "SELECT * FROM document_revisions WHERE revision_id = ?",
                (revision_id,),
            ) as cursor:
                existing_revision = await cursor.fetchone()
            await db.execute(
                "INSERT OR IGNORE INTO document_revisions "
                "(revision_id, tenant_id, document_id, revision_number, content_hash, "
                "status, supersedes_revision_id, declared_content_hash) VALUES (?, ?, ?, ?, NULL, 'PENDING', ?, NULL)",
                (
                    revision_id,
                    tenant_id,
                    document_id,
                    revision_number,
                    supersedes_revision_id,
                ),
            )
            async with db.execute(
                "SELECT tenant_id, document_id FROM document_revisions "
                "WHERE revision_id = ?",
                (revision_id,),
            ) as cursor:
                revision = await cursor.fetchone()
            if revision is None or tuple(revision) != (tenant_id, document_id):
                raise ValueError("revision identity collides with another document")
            if existing_revision is not None:
                if (
                    existing_revision["status"]
                    in (
                        "ACTIVE",
                        "SUPERSEDED",
                        "PURGED",
                        "ROLLED_BACK",
                    )
                    or existing_revision["manifest_frozen_at"] is not None
                ):
                    async with db.execute(
                        "SELECT chunk_id FROM source_chunks WHERE chunk_id = ?",
                        (chunk_id,),
                    ) as cur:
                        c_row = await cur.fetchone()
                    if c_row is None:
                        raise ValueError(
                            "cannot append source chunk to finalized revision "
                            f"in state {existing_revision['status']}"
                        )
                if (
                    existing_revision["revision_number"] != revision_number
                    or existing_revision["supersedes_revision_id"]
                    != supersedes_revision_id
                ):
                    raise ValueError(
                        "revision identity is immutable and already differs"
                    )
            if existing_revision is None and supersedes_revision_id:
                async with db.execute(
                    "SELECT document_id FROM document_revisions WHERE revision_id = ?",
                    (supersedes_revision_id,),
                ) as cursor:
                    predecessor = await cursor.fetchone()
                if predecessor is None or predecessor[0] != document_id:
                    raise ValueError("superseded revision is outside document scope")
            await db.execute(
                "INSERT OR IGNORE INTO source_chunks "
                "(chunk_id, tenant_id, dataset_id, revision_id, ordinal, "
                "content_payload, content_hash, source_ref) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk_id,
                    tenant_id,
                    dataset_id,
                    revision_id,
                    chunk_ordinal,
                    content_payload,
                    content_hash,
                    source_ref,
                ),
            )
            async with db.execute(
                "SELECT * FROM source_chunks WHERE chunk_id = ?", (chunk_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("source chunk was not persisted")
            if (
                row["tenant_id"] != tenant_id
                or row["dataset_id"] != dataset_id
                or row["revision_id"] != revision_id
                or row["ordinal"] != chunk_ordinal
                or row["content_hash"] != content_hash
                or row["source_ref"] != source_ref
            ):
                raise ValueError(
                    "source chunk identity is immutable and already differs"
                )
            manifest_hash = await self._update_revision_manifest(
                db,
                revision_id,
                finalize_revision=finalize_revision,
            )
            row_data = dict(row)
            row_data["manifest_hash"] = manifest_hash
            row_data["dataset_id"] = external_dataset_id
            row_data["document_id"] = external_document_id
            row_data["revision_id"] = external_revision_id
            row_data["chunk_id"] = external_chunk_id
            row_data["supersedes_revision_id"] = external_supersedes_id
            await db.commit()
        return row_data

    @staticmethod
    async def _update_revision_manifest(
        db: aiosqlite.Connection,
        revision_id: str,
        *,
        finalize_revision: bool = False,
    ) -> str:
        """Persist a stable hash over every source chunk in a revision."""
        async with db.execute(
            "SELECT chunk_id, ordinal, content_hash, source_ref FROM source_chunks "
            "WHERE revision_id = ? ORDER BY ordinal, chunk_id",
            (revision_id,),
        ) as cursor:
            chunks = [dict(row) for row in await cursor.fetchall()]
        if not chunks:
            raise RuntimeError("revision manifest requires at least one source chunk")
        manifest = json.dumps(chunks, sort_keys=True, separators=(",", ":"))
        manifest_hash = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
        freeze_sql = (
            ", manifest_frozen_at = CURRENT_TIMESTAMP" if finalize_revision else ""
        )
        cursor = await db.execute(
            f"UPDATE document_revisions SET manifest_hash = ?{freeze_sql} "
            "WHERE revision_id = ? AND status = 'PENDING' "
            "AND manifest_frozen_at IS NULL",
            (manifest_hash, revision_id),
        )
        if cursor.rowcount != 1:
            # Calling the idempotent chunk endpoint for an already finalized
            # chunk is allowed, but it must never alter the frozen identity.
            async with db.execute(
                "SELECT manifest_hash FROM document_revisions WHERE revision_id = ?",
                (revision_id,),
            ) as cursor:
                frozen = await cursor.fetchone()
            if frozen is None or frozen[0] != manifest_hash:
                raise ValueError("cannot change manifest of finalized revision")
        if finalize_revision:
            await MemoryDAO._reconcile_revision_activation_in_tx(db, revision_id)
        return manifest_hash

    async def create_v4_session(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_ids: list[str],
        agent_id: str,
        principal_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Bind an immutable server-owned session to an authorized dataset set."""
        _assert_valid_agent_id(agent_id)
        if not tenant_id or not workspace_id or not principal_id or not dataset_ids:
            raise ValueError("complete session scope is required")
        external_workspace_id = workspace_id
        external_datasets = sorted(set(dataset_ids))
        identifier = session_id or f"sess_{uuid.uuid4().hex}"
        async with self._sql.transaction() as db:
            workspace_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="workspace",
                external_id=workspace_id,
            )
            unique_datasets = [
                await self._catalog.resolve_id_in_tx(
                    db,
                    tenant_id=tenant_id,
                    kind="dataset",
                    external_id=item,
                )
                for item in external_datasets
            ]
            placeholders = ",".join("?" for _ in unique_datasets)
            async with db.execute(
                f"SELECT dataset_id FROM datasets WHERE tenant_id = ? "
                f"AND workspace_id = ? AND status = 'ACTIVE' "
                f"AND dataset_id IN ({placeholders})",
                (tenant_id, workspace_id, *unique_datasets),
            ) as cursor:
                found = {row[0] for row in await cursor.fetchall()}
            if found != set(unique_datasets):
                raise ValueError("session contains an out-of-scope dataset")
            await db.execute(
                "INSERT INTO v4_sessions "
                "(session_id, tenant_id, workspace_id, agent_id, principal_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (identifier, tenant_id, workspace_id, agent_id, principal_id),
            )
            await db.executemany(
                "INSERT INTO v4_session_datasets (session_id, dataset_id) VALUES (?, ?)",
                [(identifier, dataset_id) for dataset_id in unique_datasets],
            )
            await db.commit()
        return {
            "session_id": identifier,
            "tenant_id": tenant_id,
            "workspace_id": external_workspace_id,
            "agent_id": agent_id,
            "principal_id": principal_id,
            "dataset_ids": external_datasets,
            "status": "ACTIVE",
        }

    async def get_v4_session(
        self,
        session_id: str,
        *,
        principal_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not session_id:
            return None
        async with self._sql.connection() as db:
            principal_filter = " AND principal_id = ?" if principal_id else ""
            parameters = (session_id, principal_id) if principal_id else (session_id,)
            async with db.execute(
                "SELECT * FROM v4_sessions WHERE session_id = ?" + principal_filter,
                parameters,
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            result = dict(row)
            result["workspace_id"] = await self._catalog.external_id_in_tx(
                db,
                tenant_id=str(row["tenant_id"]),
                kind="workspace",
                physical_id=str(row["workspace_id"]),
            )
            async with db.execute(
                "SELECT i.external_id FROM v4_session_datasets sd "
                "JOIN v4_catalog_identities i ON i.tenant_id = ? "
                "AND i.kind = 'dataset' AND i.physical_id = sd.dataset_id "
                "WHERE sd.session_id = ? ORDER BY i.external_id",
                (row["tenant_id"], session_id),
            ) as cursor:
                result["dataset_ids"] = [item[0] for item in await cursor.fetchall()]
        return result

    async def list_v4_datasets(
        self, *, tenant_id: str, workspace_id: str
    ) -> list[dict[str, Any]]:
        async with self._sql.connection() as db:
            workspace_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="workspace",
                external_id=workspace_id,
            )
            async with db.execute(
                "SELECT di.external_id AS dataset_id, d.tenant_id, "
                "wi.external_id AS workspace_id, d.name, d.status, d.created_at "
                "FROM datasets d JOIN v4_catalog_identities di "
                "ON di.tenant_id = d.tenant_id AND di.kind = 'dataset' "
                "AND di.physical_id = d.dataset_id "
                "JOIN v4_catalog_identities wi ON wi.tenant_id = d.tenant_id "
                "AND wi.kind = 'workspace' AND wi.physical_id = d.workspace_id "
                "WHERE d.tenant_id = ? AND d.workspace_id = ? "
                "ORDER BY d.name, di.external_id",
                (tenant_id, workspace_id),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def end_v4_session(self, session_id: str) -> bool:
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE v4_sessions SET status = 'ENDED', "
                "ended_at = CURRENT_TIMESTAMP WHERE session_id = ? "
                "AND status = 'ACTIVE'",
                (session_id,),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def purge_v4_document(
        self, *, tenant_id: str, dataset_id: str, document_id: str
    ) -> dict[str, Any]:
        """Purge one document by rolling back its source-owned pipeline runs and fencing pending mutations."""
        external_document_id = document_id
        async with self._sql.transaction() as db:
            dataset_id = await self._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
            )
            document_id = await self._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="document", external_id=document_id
            )
            async with db.execute(
                "SELECT tenant_id FROM documents WHERE document_id = ? AND dataset_id = ?",
                (document_id, dataset_id),
            ) as cursor:
                document = await cursor.fetchone()
            if document is None or document[0] != tenant_id:
                raise ValueError("document does not belong to dataset scope")

            async with db.execute(
                "SELECT DISTINCT pipeline_run_id FROM ("
                "SELECT s.pipeline_run_id FROM artifact_sources s "
                "JOIN artifact_registry r ON r.registry_id = s.registry_id "
                "WHERE s.dataset_id = ? AND s.document_id = ? "
                "AND s.state = 'ACTIVE' AND r.state = 'ACTIVE' "
                "UNION "
                "SELECT pipeline_run_id FROM memory_mutations "
                "WHERE dataset_id = ? AND document_id = ? "
                "AND state NOT IN ('ROLLED_BACK', 'PURGED', 'REJECTED', 'CANCELLED')"
                ") WHERE pipeline_run_id IS NOT NULL",
                (dataset_id, document_id, dataset_id, document_id),
            ) as cursor:
                pipeline_ids = [
                    str(row[0]) for row in await cursor.fetchall() if row[0]
                ]

            # Earlier FTS5 secure-delete has false integrity failures (fixed
            # in SQLite 3.46.1). Rebuild those indexes after erasure instead.
            if sqlite3.sqlite_version_info >= (3, 46, 1):
                for fts_table in ("v4_assertions_fts", "v4_entities_fts"):
                    await db.execute(
                        f"INSERT INTO {fts_table}({fts_table}, rank) "
                        "VALUES('secure-delete', 1)"
                    )
            purged_mutation_ids = await self._purge_mutations_in_tx(
                db,
                where_sql="dataset_id = ? AND document_id = ?",
                params=(dataset_id, document_id),
            )
            if purged_mutation_ids:
                placeholders = ",".join("?" for _ in purged_mutation_ids)
                await db.execute(
                    "UPDATE projection_outbox SET state = 'CANCELLED', claim_token = NULL, claimed_by = NULL, "
                    "lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                    f"WHERE mutation_id IN ({placeholders})",
                    purged_mutation_ids,
                )
            # Erase every admission, including already-terminal mutations.
            # Cleanup uses artifact IDs/ownership, never these source payloads.
            async with db.execute(
                "SELECT mutation_id, raw_log_id, agent_id, metadata_json "
                "FROM memory_mutations WHERE dataset_id = ? AND document_id = ?",
                (dataset_id, document_id),
            ) as cursor:
                admissions = await cursor.fetchall()
            for admission in admissions:
                metadata = json.loads(admission["metadata_json"] or "{}")
                audit = metadata.get("_mesa_tier3_audit")
                if isinstance(audit, dict) and isinstance(
                    audit.get("justifications"), dict
                ):
                    # Validator justifications may quote the source text.
                    audit["justifications"] = dict.fromkeys(audit["justifications"], "")
                # Keep engine-owned identity/policy audit, dropping caller text
                # and durable extracted facts (which include source spans).
                retained_metadata = json.dumps(
                    {
                        key: value
                        for key, value in metadata.items()
                        if key.startswith("_mesa_")
                        and key not in {"_mesa_v4_projection_triplets", "_mesa_legal_items"}
                    },
                    sort_keys=True,
                )
                await db.execute(
                    "UPDATE raw_logs SET payload = json_set(payload, "
                    "'$.content', '', '$.evidence_span', '', '$.metadata', json(?)) "
                    "WHERE id = ? AND agent_id = ?",
                    (retained_metadata, admission["raw_log_id"], admission["agent_id"]),
                )
                await db.execute(
                    "UPDATE memory_mutations SET content_payload = '', evidence_span = '', "
                    "metadata_json = ? WHERE mutation_id = ?",
                    (retained_metadata, admission["mutation_id"]),
                )
            await db.execute(
                "UPDATE pipeline_run_events SET detail_json = json_set(detail_json, "
                "'$.tier3.justifications', json('{}')) "
                "WHERE json_type(detail_json, '$.tier3.justifications') IS NOT NULL "
                "AND pipeline_run_id IN (SELECT pipeline_run_id FROM memory_mutations "
                "WHERE dataset_id = ? AND document_id = ?)",
                (dataset_id, document_id),
            )
            await db.execute(
                "UPDATE source_chunks SET content_payload = '' WHERE revision_id IN "
                "(SELECT revision_id FROM document_revisions WHERE document_id = ?)",
                (document_id,),
            )
            await db.execute(
                "UPDATE artifact_sources SET evidence_span = '' "
                "WHERE dataset_id = ? AND document_id = ?",
                (dataset_id, document_id),
            )
            await db.execute(
                "UPDATE memory_artifacts SET metadata_json = json_remove(metadata_json, "
                "'$.fact_text', '$.source_span', '$.predicate', '$.entity_name', '$.supersedes') "
                "WHERE mutation_id IN (SELECT mutation_id FROM memory_mutations "
                "WHERE dataset_id = ? AND document_id = ?)",
                (dataset_id, document_id),
            )
            await db.execute(
                "UPDATE artifact_registry SET metadata_json = json_remove(metadata_json, "
                "'$.fact_text', '$.source_span', '$.predicate', '$.entity_name', '$.supersedes') "
                "WHERE EXISTS (SELECT 1 FROM artifact_sources own "
                "WHERE own.registry_id = artifact_registry.registry_id "
                "AND own.dataset_id = ? AND own.document_id = ?) "
                "AND NOT EXISTS (SELECT 1 FROM artifact_sources other "
                "WHERE other.registry_id = artifact_registry.registry_id "
                "AND other.state = 'ACTIVE' AND other.document_id IS NOT ?)",
                (dataset_id, document_id, document_id),
            )
            await db.execute(
                "UPDATE documents SET status = 'PURGED', title = '' WHERE document_id = ?",
                (document_id,),
            )
            await db.execute(
                "UPDATE document_revisions SET status = 'PURGED' WHERE document_id = ?",
                (document_id,),
            )
            await db.execute(
                "UPDATE v4_assertions SET status = 'RETRACTED', evidence_span = '', "
                "predicate = '', literal_value = CASE WHEN literal_value IS NULL "
                "THEN NULL ELSE '' END, jurisdiction = '', authority_level = '' "
                "WHERE tenant_id = ? AND dataset_id = ? AND document_id = ? "
                "AND NOT EXISTS (SELECT 1 FROM artifact_registry r "
                "JOIN artifact_sources s ON s.registry_id = r.registry_id "
                "WHERE r.tenant_id = v4_assertions.tenant_id "
                "AND r.store_name = 'SQL' AND r.artifact_kind = 'ASSERTION' "
                "AND r.physical_artifact_id = v4_assertions.assertion_id "
                "AND s.state = 'ACTIVE' AND s.document_id IS NOT ?)",
                (tenant_id, dataset_id, document_id, document_id),
            )
            if sqlite3.sqlite_version_info < (3, 46, 1):
                # ponytail: full index work on old SQLite; upgrade for secure-delete.
                for fts_table in ("v4_assertions_fts", "v4_entities_fts"):
                    await db.execute(
                        f"INSERT INTO {fts_table}({fts_table}) VALUES('rebuild')"
                    )
            await db.commit()

        outcomes = []
        rollback_errors: list[Exception] = []
        for pipeline_id in pipeline_ids:
            try:
                outcomes.append(await self.request_pipeline_rollback(pipeline_id))
            except Exception as exc:
                rollback_errors.append(exc)
        if rollback_errors:
            raise PurgeRetryPendingError(
                "document is purge-fenced but artifact cleanup remains pending"
            ) from rollback_errors[0]
        # Old page images in the WAL still hold the erased text.
        await self._sql.checkpoint("TRUNCATE")
        return {
            "document_id": external_document_id,
            "pipeline_runs": outcomes,
            "state": "PURGED",
        }

    @staticmethod
    def v4_entity_id(
        tenant_id: str,
        entity_name: str,
        *,
        entity_type: str = "ENTITY",
        ontology_uri: str | None = None,
    ) -> str:
        """Return a stable tenant/type/ontology-or-name entity identity."""
        if not tenant_id or not entity_name or not entity_type:
            raise ValueError("tenant, entity type and canonical name are required")
        normalized_type = _normalize_identity_text(entity_type).upper()
        identity_key = (
            f"ontology:{_normalize_ontology_uri(ontology_uri)}"
            if ontology_uri
            else f"name:{_normalize_identity_text(entity_name)}"
        )
        return str(
            uuid.uuid5(
                _V4_ENTITY_NAMESPACE,
                "\x1f".join((tenant_id, normalized_type, identity_key)),
            )
        )

    @staticmethod
    def v4_assertion_id(
        *,
        tenant_id: str,
        dataset_id: str,
        revision_id: str,
        chunk_id: str,
        subject_id: str,
        predicate: str,
        object_entity_id: str | None = None,
        literal_value: str | None = None,
        evidence_span: str = "",
    ) -> str:
        """Return the stable source-scoped Graph V2 assertion identity."""
        required = (
            tenant_id,
            dataset_id,
            revision_id,
            chunk_id,
            subject_id,
            predicate,
        )
        if not all(required) or (object_entity_id is None) == (literal_value is None):
            raise ValueError("assertion identity requires one entity or literal object")
        object_identity = object_entity_id or f"literal:{literal_value}"
        return str(
            uuid.uuid5(
                _V4_ASSERTION_NAMESPACE,
                "\x1f".join(
                    (
                        tenant_id,
                        dataset_id,
                        revision_id,
                        chunk_id,
                        subject_id,
                        predicate,
                        object_identity,
                        evidence_span,
                    )
                ),
            )
        )

    @staticmethod
    async def _follow_v4_entity_redirect_in_tx(db: Any, row: Any) -> Any:
        """Resolve a bounded redirect chain without erasing historical rows."""
        visited: set[str] = set()
        current = row
        while current is not None and current["redirect_entity_id"]:
            current_id = str(current["entity_id"])
            if current_id in visited or len(visited) >= 16:
                raise RuntimeError("invalid canonical entity redirect chain")
            visited.add(current_id)
            async with db.execute(
                "SELECT * FROM v4_entities WHERE entity_id = ?",
                (current["redirect_entity_id"],),
            ) as cursor:
                current = await cursor.fetchone()
        return current

    @classmethod
    async def _merge_v4_entity_rows_in_tx(cls, db: Any, first: Any, second: Any) -> Any:
        """Deterministically redirect one colliding identity to its winner.

        Ontology-backed identities outrank name-only identities. Equal identity
        classes use the UUID string as a stable tie-break. Assertions remain
        immutable and may continue to reference the redirected historical row.
        """
        first = await cls._follow_v4_entity_redirect_in_tx(db, first)
        second = await cls._follow_v4_entity_redirect_in_tx(db, second)
        if first is None or second is None:
            raise RuntimeError("cannot merge a missing canonical entity")
        if first["entity_id"] == second["entity_id"]:
            return first

        def rank(item: Any) -> tuple[int, str]:
            return (
                0 if str(item["identity_key"]).startswith("ontology:") else 1,
                str(item["entity_id"]),
            )

        winner, duplicate = sorted((first, second), key=rank)
        if (
            winner["tenant_id"] != duplicate["tenant_id"]
            or winner["entity_type"] != duplicate["entity_type"]
        ):
            raise ValueError("canonical entities can only merge inside tenant/type")
        async with db.execute(
            "SELECT tenant_id, entity_type, alias, normalized_alias, created_at "
            "FROM entity_aliases WHERE entity_id = ?",
            (duplicate["entity_id"],),
        ) as cursor:
            duplicate_aliases = await cursor.fetchall()
        await db.execute(
            "DELETE FROM entity_aliases WHERE entity_id = ?",
            (duplicate["entity_id"],),
        )
        if duplicate_aliases:
            await db.executemany(
                "INSERT OR IGNORE INTO entity_aliases "
                "(tenant_id, entity_id, entity_type, alias, normalized_alias, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        item[0],
                        winner["entity_id"],
                        item[1],
                        item[2],
                        item[3],
                        item[4],
                    )
                    for item in duplicate_aliases
                ],
            )
        async with db.execute(
            "SELECT tenant_id, entity_type, scheme, external_id, "
            "normalized_external_id, is_primary, created_at "
            "FROM entity_external_ids WHERE entity_id = ?",
            (duplicate["entity_id"],),
        ) as cursor:
            duplicate_external_ids = await cursor.fetchall()
        await db.execute(
            "DELETE FROM entity_external_ids WHERE entity_id = ?",
            (duplicate["entity_id"],),
        )
        if duplicate_external_ids:
            await db.executemany(
                "INSERT OR IGNORE INTO entity_external_ids "
                "(tenant_id, entity_id, entity_type, scheme, external_id, "
                "normalized_external_id, is_primary, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item[0],
                        winner["entity_id"],
                        item[1],
                        item[2],
                        item[3],
                        item[4],
                        item[5],
                        item[6],
                    )
                    for item in duplicate_external_ids
                ],
            )
        await db.execute(
            "UPDATE v4_entities SET status = 'REDIRECTED', redirect_entity_id = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE entity_id = ?",
            (winner["entity_id"], duplicate["entity_id"]),
        )
        return winner

    async def _resolve_v4_entity_in_tx(
        self,
        db: Any,
        *,
        tenant_id: str,
        canonical_name: str,
        entity_type: str = "ENTITY",
        ontology_uri: str | None = None,
        aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve external ID, alias or canonical name inside an open tx."""
        normalized_name = _normalize_identity_text(canonical_name)
        normalized_type = _normalize_identity_text(entity_type).upper()
        normalized_uri = _normalize_ontology_uri(ontology_uri) if ontology_uri else None
        row = None
        if normalized_uri:
            async with db.execute(
                "SELECT e.* FROM entity_external_ids x "
                "JOIN v4_entities e ON e.entity_id = x.entity_id "
                "WHERE x.tenant_id = ? AND x.entity_type = ? "
                "AND x.scheme = 'ontology' AND x.normalized_external_id = ?",
                (tenant_id, normalized_type, normalized_uri),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            async with db.execute(
                "SELECT e.* FROM entity_aliases a "
                "JOIN v4_entities e ON e.entity_id = a.entity_id "
                "WHERE a.tenant_id = ? AND a.entity_type = ? "
                "AND a.normalized_alias = ?",
                (tenant_id, normalized_type, normalized_name),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            async with db.execute(
                "SELECT * FROM v4_entities WHERE tenant_id = ? "
                "AND entity_type = ? AND normalized_name = ? "
                "AND status = 'ACTIVE' ORDER BY entity_id LIMIT 1",
                (tenant_id, normalized_type, normalized_name),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            entity_id = self.v4_entity_id(
                tenant_id,
                canonical_name,
                entity_type=normalized_type,
                ontology_uri=normalized_uri,
            )
            identity_key = (
                f"ontology:{normalized_uri}"
                if normalized_uri
                else f"name:{normalized_name}"
            )
            await db.execute(
                "INSERT OR IGNORE INTO v4_entities "
                "(entity_id, tenant_id, entity_type, canonical_name, "
                "normalized_name, ontology_uri, identity_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entity_id,
                    tenant_id,
                    normalized_type,
                    canonical_name.strip(),
                    normalized_name,
                    normalized_uri,
                    identity_key,
                ),
            )
            async with db.execute(
                "SELECT * FROM v4_entities WHERE tenant_id = ? "
                "AND entity_type = ? AND identity_key = ?",
                (tenant_id, normalized_type, identity_key),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("canonical entity resolution failed")
        row = await self._follow_v4_entity_redirect_in_tx(db, row)
        if row is None:
            raise RuntimeError("canonical entity redirect target is missing")
        if normalized_uri and not str(row["identity_key"]).startswith("ontology:"):
            ontology_entity_id = self.v4_entity_id(
                tenant_id,
                canonical_name,
                entity_type=normalized_type,
                ontology_uri=normalized_uri,
            )
            ontology_identity_key = f"ontology:{normalized_uri}"
            await db.execute(
                "INSERT OR IGNORE INTO v4_entities "
                "(entity_id, tenant_id, entity_type, canonical_name, "
                "normalized_name, ontology_uri, identity_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ontology_entity_id,
                    tenant_id,
                    normalized_type,
                    canonical_name.strip(),
                    normalized_name,
                    normalized_uri,
                    ontology_identity_key,
                ),
            )
            async with db.execute(
                "SELECT * FROM v4_entities WHERE tenant_id = ? "
                "AND entity_type = ? AND identity_key = ?",
                (tenant_id, normalized_type, ontology_identity_key),
            ) as cursor:
                ontology_row = await cursor.fetchone()
            row = await self._merge_v4_entity_rows_in_tx(db, row, ontology_row)
        result = dict(row)
        names = {canonical_name, *(aliases or [])}
        for alias in names:
            if not alias.strip():
                continue
            normalized_alias = _normalize_identity_text(alias)
            async with db.execute(
                "SELECT e.* FROM entity_aliases a "
                "JOIN v4_entities e ON e.entity_id = a.entity_id "
                "WHERE a.tenant_id = ? AND a.entity_type = ? "
                "AND a.normalized_alias = ?",
                (tenant_id, normalized_type, normalized_alias),
            ) as cursor:
                alias_owner = await cursor.fetchone()
            if (
                alias_owner is not None
                and alias_owner["entity_id"] != result["entity_id"]
            ):
                merged = await self._merge_v4_entity_rows_in_tx(
                    db, row, alias_owner
                )
                row = merged
                result = dict(merged)
            await db.execute(
                "INSERT OR IGNORE INTO entity_aliases "
                "(tenant_id, entity_id, entity_type, alias, normalized_alias) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    result["entity_id"],
                    normalized_type,
                    alias.strip(),
                    normalized_alias,
                ),
            )
        if normalized_uri:
            async with db.execute(
                "SELECT e.* FROM entity_external_ids x "
                "JOIN v4_entities e ON e.entity_id = x.entity_id "
                "WHERE x.tenant_id = ? AND x.entity_type = ? "
                "AND x.scheme = 'ontology' AND x.normalized_external_id = ?",
                (tenant_id, normalized_type, normalized_uri),
            ) as cursor:
                external_owner = await cursor.fetchone()
            if (
                external_owner is not None
                and external_owner["entity_id"] != result["entity_id"]
            ):
                merged = await self._merge_v4_entity_rows_in_tx(
                    db, row, external_owner
                )
                row = merged
                result = dict(merged)
            await db.execute(
                "INSERT OR IGNORE INTO entity_external_ids "
                "(tenant_id, entity_id, entity_type, scheme, external_id, "
                "normalized_external_id, is_primary) VALUES (?, ?, ?, 'ontology', ?, ?, 1)",
                (
                    tenant_id,
                    result["entity_id"],
                    normalized_type,
                    ontology_uri,
                    normalized_uri,
                ),
            )
        return result

    async def resolve_v4_entity(
        self,
        *,
        tenant_id: str,
        canonical_name: str,
        entity_type: str = "ENTITY",
        ontology_uri: str | None = None,
        aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve external ID, alias or canonical name, then create once."""
        async with self._sql.transaction() as db:
            result = await self._resolve_v4_entity_in_tx(
                db,
                tenant_id=tenant_id,
                canonical_name=canonical_name,
                entity_type=entity_type,
                ontology_uri=ontology_uri,
                aliases=aliases,
            )
            await db.commit()
        return result

    # ------------------------------------------------------------------
    # v4 mutation ledger / projection outbox
    # ------------------------------------------------------------------

    async def admit_v4_memory(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        agent_id: str,
        session_id: str,
        document_id: str,
        revision_id: str,
        chunk_id: str,
        title: str,
        content_payload: str,
        source_ref: str,
        evidence_span: str,
        revision_number: int,
        chunk_ordinal: int,
        supersedes_revision_id: str | None,
        metadata: dict[str, Any],
        embedding_provider: str,
        embedding_model: str,
        embedding_version: str,
        embedding_dimension: int,
        policy: QueueAdmissionPolicy,
        validation_mode: int,
        legal_identity: Mapping[str, Any] | None = None,
        document_chunk: bool = False,
        embedding_space_id: str | None = None,
        embedding_model_revision: str | None = None,
        embedding_normalized: bool = True,
        idempotency_key: str | None = None,
        payload_hash: str | None = None,
        finalize_revision: bool = True,
    ) -> dict[str, Any]:
        """Atomically admit one V4 write and all of its durable records.

        The source chunk, raw-log/dispatch receipt, mutation ledger and (when
        present) idempotency receipt are one SQLite unit.  Consequently a
        rejected queue admission cannot leave catalog provenance behind, and
        a failed request never strands an idempotency key in ``PENDING``.
        """
        _assert_valid_agent_id(agent_id)
        if not all(
            (
                tenant_id,
                workspace_id,
                dataset_id,
                session_id,
                document_id,
                revision_id,
                chunk_id,
                title,
                content_payload,
                source_ref,
            )
        ):
            raise ValueError("complete V4 admission provenance is required")
        if (idempotency_key is None) != (payload_hash is None):
            raise ValueError(
                "idempotency key and payload hash must be supplied together"
            )
        if (
            not embedding_provider
            or not embedding_model
            or not embedding_version
            or embedding_dimension < 1
        ):
            raise ValueError("complete embedding identity is required")

        # Caller metadata is untrusted and must never select the validation
        # assurance attached to durable work.  Snapshot only runtime-owned
        # policy truth, and remove the entire reserved namespace before adding
        # canonical fields.
        effective_metadata = {
            str(key): value
            for key, value in (metadata.items() if isinstance(metadata, dict) else ())
            if not str(key).startswith("_mesa_")
        }
        if document_chunk:
            effective_metadata["_mesa_document_chunk"] = True
        normalized_legal_identity = canonicalize_trusted_legal_identity(
            {**legal_identity, "identity_source": "verified_ingestion"}
            if legal_identity is not None
            else None
        )
        if normalized_legal_identity is not None:
            effective_metadata["_mesa_legal_identity"] = normalized_legal_identity
        if type(validation_mode) is not int or validation_mode not in (0, 1, 2):
            raise ValueError("validation_mode must be 0, 1, or 2")
        effective_mode = validation_mode
        effective_metadata["_mesa_validation_mode"] = effective_mode
        revision = embedding_model_revision or embedding_version
        derived_space_id = (
            f"{embedding_provider}:{embedding_model}:{revision}:{embedding_dimension}:"
            f"norm={str(embedding_normalized).lower()}"
        )
        if embedding_space_id and embedding_space_id != derived_space_id:
            raise ValueError("embedding space identity is inconsistent")
        effective_metadata["_mesa_embedding_identity"] = {
            "embedding_space_id": embedding_space_id or derived_space_id,
            "provider": embedding_provider,
            "model": embedding_model,
            "model_revision": embedding_model_revision,
            "version": embedding_version,
            "dimension": embedding_dimension,
            "normalized": embedding_normalized,
        }
        if self._legal_rules_enabled:
            effective_metadata["_mesa_legal_items"] = {
                "version": legal_rules.RULES_VERSION,
                "items": await asyncio.to_thread(legal_rules.extract, content_payload),
            }

        raw_payload = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "document_id": document_id,
            "revision_id": revision_id,
            "chunk_id": chunk_id,
            "source_ref": source_ref,
            "evidence_span": evidence_span,
            "agent_id": agent_id,
            "session_id": session_id,
            "content": content_payload,
            "metadata": effective_metadata,
            "validation_mode": effective_mode,
        }
        serialized, payload_bytes = _canonical_payload_bytes(raw_payload)
        content_hash = hashlib.sha256(content_payload.encode("utf-8")).hexdigest()

        try:
            async with self._sql.transaction() as db:
                workspace_id = await self._catalog.resolve_id_in_tx(
                    db,
                    tenant_id=tenant_id,
                    kind="workspace",
                    external_id=workspace_id,
                )
                dataset_id = await self._catalog.resolve_id_in_tx(
                    db,
                    tenant_id=tenant_id,
                    kind="dataset",
                    external_id=dataset_id,
                )
                if idempotency_key is not None:
                    insert_cursor = await db.execute(
                        "INSERT OR IGNORE INTO v4_idempotency_receipts "
                        "(tenant_id, agent_id, dataset_id, operation_type, idempotency_key, payload_hash, state) "
                        "VALUES (?, ?, ?, 'INSERT', ?, ?, 'PENDING')",
                        (
                            tenant_id,
                            agent_id,
                            dataset_id,
                            idempotency_key,
                            payload_hash,
                        ),
                    )
                    async with db.execute(
                        "SELECT * FROM v4_idempotency_receipts WHERE tenant_id = ? "
                        "AND agent_id = ? AND dataset_id = ? AND operation_type = 'INSERT' "
                        "AND idempotency_key = ?",
                        (tenant_id, agent_id, dataset_id, idempotency_key),
                    ) as cursor:
                        receipt = await cursor.fetchone()
                    if (
                        receipt is None
                    ):  # pragma: no cover - INSERT OR IGNORE guarantees it
                        raise RuntimeError("V4 idempotency reservation did not persist")
                    receipt_data = dict(receipt)
                    if receipt_data["payload_hash"] != payload_hash:
                        raise ValueError("idempotency_key payload mismatch")
                    if insert_cursor.rowcount != 1:
                        await db.commit()
                        if receipt_data["state"] == "COMPLETED":
                            return {
                                "outcome": "DUPLICATE",
                                "response": json.loads(
                                    str(receipt_data["response_json"])
                                ),
                            }
                        return {"outcome": "IN_PROGRESS"}

                document_id = await self._catalog.resolve_id_in_tx(
                    db,
                    tenant_id=tenant_id,
                    kind="document",
                    external_id=document_id,
                    create=True,
                )
                revision_id = await self._catalog.resolve_id_in_tx(
                    db,
                    tenant_id=tenant_id,
                    kind="revision",
                    external_id=revision_id,
                    create=True,
                )
                chunk_id = await self._catalog.resolve_id_in_tx(
                    db,
                    tenant_id=tenant_id,
                    kind="chunk",
                    external_id=chunk_id,
                    create=True,
                )
                if supersedes_revision_id:
                    supersedes_revision_id = await self._catalog.resolve_id_in_tx(
                        db,
                        tenant_id=tenant_id,
                        kind="revision",
                        external_id=supersedes_revision_id,
                    )
                async with db.execute(
                    "SELECT tenant_id FROM datasets WHERE dataset_id = ?", (dataset_id,)
                ) as cursor:
                    dataset = await cursor.fetchone()
                if dataset is None or dataset[0] != tenant_id:
                    raise ValueError("dataset does not belong to tenant")
                await db.execute(
                    "INSERT OR IGNORE INTO documents "
                    "(document_id, tenant_id, dataset_id, title) VALUES (?, ?, ?, ?)",
                    (document_id, tenant_id, dataset_id, title),
                )
                async with db.execute(
                    "SELECT tenant_id, dataset_id, status FROM documents WHERE document_id = ?",
                    (document_id,),
                ) as cursor:
                    document = await cursor.fetchone()
                if document is None or (document[0], document[1]) != (
                    tenant_id,
                    dataset_id,
                ):
                    raise ValueError("document identity collides with another dataset")
                if document[2] == "PURGED":
                    raise ValueError("document is purged")
                async with db.execute(
                    "SELECT * FROM document_revisions WHERE revision_id = ?",
                    (revision_id,),
                ) as cursor:
                    existing_revision = await cursor.fetchone()
                await db.execute(
                    "INSERT OR IGNORE INTO document_revisions "
                    "(revision_id, tenant_id, document_id, revision_number, content_hash, "
                    "status, supersedes_revision_id, declared_content_hash) VALUES (?, ?, ?, ?, NULL, 'PENDING', ?, NULL)",
                    (
                        revision_id,
                        tenant_id,
                        document_id,
                        revision_number,
                        supersedes_revision_id,
                    ),
                )
                async with db.execute(
                    "SELECT 1 FROM document_revisions WHERE revision_id = ?",
                    (revision_id,),
                ) as cursor:
                    if await cursor.fetchone() is None:
                        raise ValueError(
                            "revision number already used for this document"
                        )
                if existing_revision is not None and (
                    existing_revision["revision_number"] != revision_number
                    or existing_revision["supersedes_revision_id"]
                    != supersedes_revision_id
                ):
                    raise ValueError(
                        "revision identity is immutable and already differs"
                    )
                if existing_revision is not None and (
                    existing_revision["status"] != "PENDING"
                    or existing_revision["manifest_frozen_at"] is not None
                ):
                    async with db.execute(
                        "SELECT chunk_id FROM source_chunks WHERE chunk_id = ?",
                        (chunk_id,),
                    ) as cursor:
                        existing_chunk = await cursor.fetchone()
                    if existing_chunk is None:
                        raise ValueError(
                            "cannot append source chunk to finalized revision"
                        )
                if supersedes_revision_id:
                    async with db.execute(
                        "SELECT document_id FROM document_revisions WHERE revision_id = ?",
                        (supersedes_revision_id,),
                    ) as cursor:
                        predecessor = await cursor.fetchone()
                    if predecessor is None or predecessor[0] != document_id:
                        raise ValueError(
                            "superseded revision is outside document scope"
                        )
                await db.execute(
                    "INSERT OR IGNORE INTO source_chunks "
                    "(chunk_id, tenant_id, dataset_id, revision_id, ordinal, content_payload, content_hash, source_ref) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk_id,
                        tenant_id,
                        dataset_id,
                        revision_id,
                        chunk_ordinal,
                        content_payload,
                        content_hash,
                        source_ref,
                    ),
                )
                async with db.execute(
                    "SELECT * FROM source_chunks WHERE chunk_id = ?", (chunk_id,)
                ) as cursor:
                    chunk = await cursor.fetchone()
                if chunk is None or (
                    chunk["tenant_id"],
                    chunk["dataset_id"],
                    chunk["revision_id"],
                    chunk["ordinal"],
                    chunk["content_hash"],
                    chunk["source_ref"],
                ) != (
                    tenant_id,
                    dataset_id,
                    revision_id,
                    chunk_ordinal,
                    content_hash,
                    source_ref,
                ):
                    raise ValueError(
                        "source chunk identity is immutable and already differs"
                    )
                global_usage = await self._queue_usage(db)
                tenant_usage = await self._queue_usage(db, tenant_id)
                self._enforce_queue_capacity(
                    global_usage, tenant_usage, payload_bytes, policy
                )
                # Whole-document uploads can resume from durable chunks. Hash
                # their membership only when accepting the final chunk.
                if finalize_revision or not document_chunk:
                    await self._update_revision_manifest(
                        db,
                        revision_id,
                        finalize_revision=finalize_revision,
                    )
                elif (
                    existing_revision is not None
                    and existing_revision["status"] == "PENDING"
                    and existing_revision["manifest_frozen_at"] is None
                    and existing_revision["manifest_hash"]
                ):
                    # A resumed draft may still have a hash of an old prefix.
                    await db.execute(
                        "UPDATE document_revisions SET manifest_hash = '' WHERE revision_id = ?",
                        (revision_id,),
                    )
                cursor = await db.execute(
                    "INSERT INTO raw_logs (agent_id, payload, status) VALUES (?, ?, 'DEFERRED')",
                    (agent_id, serialized),
                )
                assert cursor.lastrowid is not None
                log_id = int(cursor.lastrowid)
                dispatch_id, queue_id = str(uuid.uuid4()), str(uuid.uuid4())
                dispatch_key = f"raw-log:{agent_id}:{log_id}"
                await db.execute(
                    "INSERT INTO dispatch_journal (dispatch_id, source_record_id, tenant_id, agent_id, "
                    "job_type, idempotency_key, state, attempt_count, queue_record_id, dispatched_at, finalized_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'cold_path', ?, 'RECEIPT_RECORDED', 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (dispatch_id, log_id, tenant_id, agent_id, dispatch_key, queue_id),
                )
                await db.execute(
                    "INSERT INTO dispatch_queue (queue_record_id, dispatch_id, tenant_id, agent_id, job_type, "
                    "payload_reference, payload_bytes, idempotency_key, state) VALUES (?, ?, ?, ?, 'cold_path', ?, ?, ?, 'ENQUEUED')",
                    (
                        queue_id,
                        dispatch_id,
                        tenant_id,
                        agent_id,
                        log_id,
                        payload_bytes,
                        dispatch_key,
                    ),
                )
                await db.execute(
                    "INSERT INTO dispatch_receipts (receipt_id, dispatch_id, queue_record_id, tenant_id, agent_id, outcome, idempotency_key) "
                    "VALUES (?, ?, ?, ?, ?, 'ENQUEUED', ?)",
                    (
                        str(uuid.uuid4()),
                        dispatch_id,
                        queue_id,
                        tenant_id,
                        agent_id,
                        dispatch_key,
                    ),
                )

                identity = f"mesa:v4:{tenant_id}:{agent_id}:{log_id}"
                candidate_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}:candidate")
                )
                mutation_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}:mutation")
                )
                pipeline_run_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}:pipeline-run")
                )
                await db.execute(
                    "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, workspace_id, dataset_id, session_id, agent_id, state) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'QUEUED')",
                    (
                        pipeline_run_id,
                        tenant_id,
                        workspace_id,
                        dataset_id,
                        session_id,
                        agent_id,
                    ),
                )
                await db.execute(
                    "INSERT INTO pipeline_run_events (event_id, pipeline_run_id, to_state, event_type) "
                    "VALUES (?, ?, 'QUEUED', 'ADMITTED')",
                    (
                        str(
                            uuid.uuid5(
                                _V4_CATALOG_NAMESPACE,
                                f"{pipeline_run_id}:QUEUED:ADMITTED",
                            )
                        ),
                        pipeline_run_id,
                    ),
                )
                await db.execute(
                    "INSERT INTO memory_mutations "
                    "(mutation_id, candidate_id, raw_log_id, tenant_id, workspace_id, dataset_id, document_id, revision_id, chunk_id, source_ref, evidence_span, agent_id, session_id, content_payload, metadata_json, source, pipeline_run_id, extraction_version, embedding_provider, embedding_model, embedding_version, embedding_dimension, state) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'api', ?, 'v4', ?, ?, ?, ?, 'RECEIVED')",
                    (
                        mutation_id,
                        candidate_id,
                        log_id,
                        tenant_id,
                        workspace_id,
                        dataset_id,
                        document_id,
                        revision_id,
                        chunk_id,
                        source_ref,
                        evidence_span,
                        agent_id,
                        session_id,
                        content_payload,
                        json.dumps(effective_metadata, sort_keys=True),
                        pipeline_run_id,
                        embedding_provider,
                        embedding_model,
                        embedding_version,
                        embedding_dimension,
                    ),
                )
                for projection_name in ("SQL", "VECTOR", "GRAPH"):
                    await db.execute(
                        "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
                        "VALUES (?, ?, ?, 'BLOCKED_VALIDATION')",
                        (
                            str(
                                uuid.uuid5(
                                    uuid.NAMESPACE_URL,
                                    f"{mutation_id}:{projection_name}",
                                )
                            ),
                            mutation_id,
                            projection_name,
                        ),
                    )
                response = {
                    "status": "accepted",
                    "mutation_id": mutation_id,
                    "candidate_id": candidate_id,
                    "pipeline_run_id": pipeline_run_id,
                    "raw_log_id": log_id,
                }
                if idempotency_key is not None:
                    await db.execute(
                        "UPDATE v4_idempotency_receipts SET state = 'COMPLETED', mutation_id = ?, response_json = ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE tenant_id = ? AND agent_id = ? AND dataset_id = ? AND operation_type = 'INSERT' AND idempotency_key = ?",
                        (
                            mutation_id,
                            json.dumps(response, sort_keys=True),
                            tenant_id,
                            agent_id,
                            dataset_id,
                            idempotency_key,
                        ),
                    )
                await db.commit()
        except (aiosqlite.Error, OSError) as exc:
            import traceback

            traceback.print_exc()
            logger.exception("admit_v4_memory failed with SQLite/OS error: %s", exc)
            raise QueueUnavailableError("durable admission is unavailable") from exc
        return {"outcome": "ADMITTED", "response": response}

    async def record_mutation(
        self, candidate: dict[str, Any], *, raw_log_id: int | None
    ) -> dict[str, Any]:
        """Persist one canonical candidate and its idempotent projections.

        The candidate record, rather than an unstructured raw-log payload, is
        the source of truth for v4 projection workers. Re-delivery of the same
        deterministic mutation ID is safe and never duplicates outbox rows.
        """
        required = {
            "mutation_id",
            "candidate_id",
            "tenant_id",
            "workspace_id",
            "dataset_id",
            "document_id",
            "revision_id",
            "chunk_id",
            "agent_id",
            "session_id",
            "content_payload",
            "source_ref",
            "pipeline_run_id",
        }
        missing = sorted(name for name in required if not candidate.get(name))
        if missing:
            raise ValueError(f"canonical candidate is missing: {', '.join(missing)}")
        agent_id = str(candidate["agent_id"])
        _assert_valid_agent_id(agent_id)
        tenant_id = str(candidate["tenant_id"])
        await self.ensure_v4_catalog_scope(
            tenant_id=tenant_id,
            workspace_id=str(candidate["workspace_id"]),
            dataset_id=str(candidate["dataset_id"]),
        )
        mutation_id = str(candidate["mutation_id"])
        pipeline_run_id = str(candidate["pipeline_run_id"])
        metadata = dict(candidate.get("metadata") or {})
        legal_identity = canonicalize_trusted_legal_identity(
            metadata.get("_mesa_legal_identity")
        )
        if legal_identity is not None:
            metadata["_mesa_legal_identity"] = legal_identity
        if "_mesa_embedding_identity" not in metadata:
            provider = candidate.get("embedding_provider")
            model = candidate.get("embedding_model")
            version = candidate.get("embedding_version")
            dimension = candidate.get("embedding_dimension")
            if provider and model and version and dimension:
                normalized = bool(candidate.get("embedding_normalized", True))
                revision = candidate.get("embedding_model_revision") or version
                metadata["_mesa_embedding_identity"] = {
                    "embedding_space_id": candidate.get("embedding_space_id")
                    or f"{provider}:{model}:{revision}:{dimension}:norm={str(normalized).lower()}",
                    "provider": provider,
                    "model": model,
                    "model_revision": candidate.get("embedding_model_revision"),
                    "version": version,
                    "dimension": dimension,
                    "normalized": normalized,
                }
        async with self._sql.transaction() as db:
            workspace_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="workspace",
                external_id=str(candidate["workspace_id"]),
                create=True,
            )
            dataset_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="dataset",
                external_id=str(candidate["dataset_id"]),
                create=True,
            )
            document_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="document",
                external_id=str(candidate["document_id"]),
                create=True,
            )
            revision_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="revision",
                external_id=str(candidate["revision_id"]),
                create=True,
            )
            chunk_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="chunk",
                external_id=str(candidate["chunk_id"]),
                create=True,
            )
            await db.execute(
                "INSERT OR IGNORE INTO pipeline_runs "
                "(pipeline_run_id, tenant_id, workspace_id, dataset_id, "
                "session_id, agent_id, state) "
                "VALUES (?, ?, ?, ?, ?, ?, 'QUEUED')",
                (
                    pipeline_run_id,
                    tenant_id,
                    workspace_id,
                    dataset_id,
                    candidate["session_id"],
                    agent_id,
                ),
            )
            await db.execute(
                "INSERT OR IGNORE INTO pipeline_run_events "
                "(event_id, pipeline_run_id, to_state, event_type) "
                "VALUES (?, ?, 'QUEUED', 'ADMITTED')",
                (
                    str(
                        uuid.uuid5(
                            _V4_CATALOG_NAMESPACE,
                            f"{pipeline_run_id}:QUEUED:ADMITTED",
                        )
                    ),
                    pipeline_run_id,
                ),
            )
            await db.execute(
                "INSERT OR IGNORE INTO memory_mutations "
                "(mutation_id, candidate_id, raw_log_id, tenant_id, workspace_id, dataset_id, "
                "document_id, revision_id, chunk_id, source_ref, evidence_span, "
                "agent_id, session_id, content_payload, "
                "metadata_json, source, pipeline_run_id, extraction_version, "
                "embedding_provider, embedding_model, embedding_version, "
                "embedding_dimension, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECEIVED')",
                (
                    mutation_id,
                    candidate["candidate_id"],
                    raw_log_id,
                    tenant_id,
                    workspace_id,
                    dataset_id,
                    document_id,
                    revision_id,
                    chunk_id,
                    candidate["source_ref"],
                    candidate.get("evidence_span", ""),
                    agent_id,
                    candidate["session_id"],
                    candidate["content_payload"],
                    json.dumps(metadata, sort_keys=True),
                    candidate.get("source", "api"),
                    pipeline_run_id,
                    candidate.get("extraction_version", "v4"),
                    candidate.get("embedding_provider"),
                    candidate.get("embedding_model"),
                    candidate.get("embedding_version"),
                    candidate.get("embedding_dimension"),
                ),
            )
            for projection_name in ("SQL", "VECTOR", "GRAPH"):
                await db.execute(
                    "INSERT OR IGNORE INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
                    "VALUES (?, ?, ?, 'BLOCKED_VALIDATION')",
                    (
                        str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"{mutation_id}:{projection_name}",
                            )
                        ),
                        mutation_id,
                        projection_name,
                    ),
                )
            async with db.execute(
                "SELECT * FROM memory_mutations WHERE mutation_id = ? AND agent_id = ?",
                (mutation_id, agent_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("mutation ledger insert did not persist")
            existing = dict(row)
            if existing["candidate_id"] != candidate["candidate_id"]:
                raise ValueError("mutation_id collision with a different candidate")
            identity_fields = (
                "embedding_provider",
                "embedding_model",
                "embedding_version",
                "embedding_dimension",
            )
            candidate_identity = tuple(
                candidate.get(field) for field in identity_fields
            )
            if any(value is not None for value in candidate_identity):
                if (
                    any(value in (None, "") for value in candidate_identity[:3])
                    or isinstance(candidate_identity[3], bool)
                    or not isinstance(candidate_identity[3], int)
                    or candidate_identity[3] < 1
                ):
                    raise ValueError("complete embedding identity is required")
                existing_identity = tuple(existing[field] for field in identity_fields)
                for current, expected in zip(existing_identity, candidate_identity):
                    if current is not None and current != expected:
                        raise ValueError("mutation embedding identity conflicts")
                if existing_identity != candidate_identity:
                    await db.execute(
                        "UPDATE memory_mutations SET embedding_provider = ?, "
                        "embedding_model = ?, embedding_version = ?, "
                        "embedding_dimension = ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE mutation_id = ?",
                        (*candidate_identity, mutation_id),
                    )
                    existing.update(zip(identity_fields, candidate_identity))
            # A mutation is never projectable before the selected validation
            # policy is satisfied. The durable rows exist now so a rejection
            # can be audited/cancelled, but consumers cannot claim them until
            # ``VALIDATED``.
            for projection_name in ("SQL", "VECTOR", "GRAPH"):
                await db.execute(
                    "INSERT OR IGNORE INTO projection_outbox "
                    "(projection_id, mutation_id, projection_name, state) VALUES (?, ?, ?, 'BLOCKED_VALIDATION')",
                    (
                        str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL, f"{mutation_id}:{projection_name}"
                            )
                        ),
                        mutation_id,
                        projection_name,
                    ),
                )
            await db.commit()
        return existing

    async def get_mutation(
        self, agent_id: str, mutation_id: str
    ) -> dict[str, Any] | None:
        _assert_valid_agent_id(agent_id)
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM memory_mutations WHERE mutation_id = ? AND agent_id = ?",
                (mutation_id, agent_id),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_pipeline_mutations(
        self, pipeline_run_id: str
    ) -> list[dict[str, Any]]:
        """Return all canonical memory mutations produced by a single pipeline run event (1 Event -> 0..N Memories)."""
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM memory_mutations WHERE pipeline_run_id = ? ORDER BY created_at ASC",
                (pipeline_run_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        results = []
        for row in rows:
            rec = dict(row)
            rec["metadata"] = json.loads(rec.get("metadata_json") or "{}")
            results.append(rec)
        return results

    async def get_mutation_summary(self, mutation_id: str) -> dict[str, Any] | None:
        """Return a v4 mutation only with its durable projection receipts.

        This lookup deliberately has no caller-supplied tenant parameter.  The
        route layer must first load the immutable owner and then authorize its
        principal against that exact agent/session scope; accepting a client
        tenant here would make it too easy to turn a status endpoint into a
        cross-tenant oracle.
        """
        if not mutation_id:
            return None
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM memory_mutations WHERE mutation_id = ?", (mutation_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            mutation = dict(row)
            try:
                mutation_metadata = json.loads(
                    str(mutation.get("metadata_json") or "{}")
                )
            except (TypeError, ValueError):
                mutation_metadata = {}
            tier3_audit = _public_tier3_audit(
                mutation_metadata.get("_mesa_tier3_audit")
                if isinstance(mutation_metadata, dict)
                else None
            )
            if tier3_audit is not None:
                mutation["tier3_audit"] = tier3_audit
                if str(mutation.get("state", "")).upper() == "REJECTED":
                    mutation["rejection_reason"] = tier3_audit["reason"]
            async with db.execute(
                "SELECT store_name, artifact_kind, artifact_id, state "
                "FROM memory_artifacts WHERE mutation_id = ? ORDER BY store_name, artifact_kind, artifact_id",
                (mutation_id,),
            ) as cursor:
                mutation["artifacts"] = [dict(item) for item in await cursor.fetchall()]
            async with db.execute(
                "SELECT projection_name, state, attempt_count, retry_limit, last_error_class "
                "FROM projection_outbox WHERE mutation_id = ? ORDER BY projection_name",
                (mutation_id,),
            ) as cursor:
                mutation["projections"] = [
                    dict(item) for item in await cursor.fetchall()
                ]
            tenant_id = str(mutation["tenant_id"])
            for field, kind in (
                ("workspace_id", "workspace"),
                ("dataset_id", "dataset"),
                ("document_id", "document"),
                ("revision_id", "revision"),
                ("chunk_id", "chunk"),
            ):
                physical_id = mutation.get(field)
                if physical_id:
                    mutation[field] = await self._catalog.external_id_in_tx(
                        db,
                        tenant_id=tenant_id,
                        kind=kind,
                        physical_id=str(physical_id),
                    )
        return mutation

    async def list_session_mutation_summaries(
        self, agent_id: str, session_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """List bounded v4 provenance receipts for one authorized session."""
        _assert_valid_agent_id(agent_id)
        if not session_id or not 1 <= limit <= 100:
            raise ValueError("invalid session mutation query")
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT mutation_id, candidate_id, raw_log_id, tenant_id, "
                "workspace_id, dataset_id, document_id, revision_id, chunk_id, "
                "source_ref, state, source, pipeline_run_id, "
                "created_at, updated_at FROM memory_mutations "
                "WHERE agent_id = ? AND session_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, session_id, limit),
            ) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            for mutation in rows:
                tenant_id = str(mutation["tenant_id"])
                for field, kind in (
                    ("workspace_id", "workspace"),
                    ("dataset_id", "dataset"),
                    ("document_id", "document"),
                    ("revision_id", "revision"),
                    ("chunk_id", "chunk"),
                ):
                    physical_id = mutation.get(field)
                    if physical_id:
                        mutation[field] = await self._catalog.external_id_in_tx(
                            db,
                            tenant_id=tenant_id,
                            kind=kind,
                            physical_id=str(physical_id),
                        )
            return rows

    @staticmethod
    async def _transition_pipeline_run_in_tx(
        db: Any,
        pipeline_run_id: str,
        *,
        to_state: str,
        event_type: str,
        failure_class: str | None = None,
        event_detail: dict[str, Any] | None = None,
    ) -> bool:
        """Compare-and-swap one legal pipeline transition and append its event."""
        transitions = {
            "QUEUED": {
                "RUNNING",
                "RETRY_PENDING",
                "DLQ",
                "ROLLING_BACK",
                "BLOCKED",
                "PURGED",
                "CANCELLED",
            },
            "RUNNING": {
                "EXTRACTED",
                "VALIDATED",
                "PROJECTING",
                "COMMITTED",
                "RETRY_PENDING",
                "DLQ",
                "ROLLING_BACK",
                "BLOCKED",
                "PURGED",
                "CANCELLED",
            },
            "EXTRACTED": {
                "VALIDATED",
                "PROJECTING",
                "COMMITTED",
                "RETRY_PENDING",
                "DLQ",
                "ROLLING_BACK",
                "BLOCKED",
                "PURGED",
                "CANCELLED",
            },
            "VALIDATED": {
                "PROJECTING",
                "COMMITTED",
                "RETRY_PENDING",
                "DLQ",
                "ROLLING_BACK",
                "BLOCKED",
                "PURGED",
                "CANCELLED",
            },
            "PROJECTING": {
                "COMMITTED",
                "RETRY_PENDING",
                "DLQ",
                "ROLLING_BACK",
                "BLOCKED",
                "PURGED",
                "CANCELLED",
            },
            "RETRY_PENDING": {
                "RUNNING",
                "PROJECTING",
                "COMMITTED",
                "DLQ",
                "ROLLING_BACK",
                "BLOCKED",
                "PURGED",
                "CANCELLED",
            },
            "DLQ": {"RETRY_PENDING", "ROLLING_BACK", "BLOCKED", "PURGED", "CANCELLED"},
            "ROLLING_BACK": {"ROLLED_BACK", "BLOCKED", "PURGED", "CANCELLED"},
            "BLOCKED": {"RETRY_PENDING", "ROLLING_BACK", "PURGED", "CANCELLED"},
            "COMMITTED": {
                "RETRY_PENDING",
                "ROLLING_BACK",
                "PURGED",
                "CANCELLED",
            },
            "ROLLED_BACK": set(),
            "PURGED": set(),
            "CANCELLED": set(),
            "REJECTED": set(),
        }
        async with db.execute(
            "SELECT state FROM pipeline_runs WHERE pipeline_run_id = ?",
            (pipeline_run_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise ValueError("unknown pipeline run")
        current = str(row[0])
        if current == to_state:
            return True
        if to_state not in transitions.get(current, set()):
            return False
        cursor = await db.execute(
            "UPDATE pipeline_runs SET state = ?, failure_class = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE pipeline_run_id = ? AND state = ?",
            (to_state, failure_class, pipeline_run_id, current),
        )
        if cursor.rowcount != 1:
            return False
        await db.execute(
            "INSERT INTO pipeline_run_events "
            "(event_id, pipeline_run_id, from_state, to_state, event_type, detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                pipeline_run_id,
                current,
                to_state,
                event_type,
                json.dumps(
                    {
                        **({"failure_class": failure_class} if failure_class else {}),
                        **(event_detail or {}),
                    },
                    sort_keys=True,
                ),
            ),
        )
        return True

    @staticmethod
    async def _recompute_pipeline_state_in_tx(
        db: Any,
        pipeline_run_id: str,
        *,
        event_type: str = "MUTATION_STATE_CHANGE",
        failure_class: str | None = None,
        event_detail: dict[str, Any] | None = None,
    ) -> bool:
        """Derive parent pipeline state from the aggregate state of all child mutations."""
        async with db.execute(
            "SELECT state FROM memory_mutations WHERE pipeline_run_id = ?",
            (pipeline_run_id,),
        ) as cursor:
            child_states = [r[0] for r in await cursor.fetchall()]
        if not child_states:
            return False

        if all(s == "COMMITTED" for s in child_states):
            target_state = "COMMITTED"
        elif any(s in ("DEAD_LETTER", "BLOCKED", "REJECTED") for s in child_states):
            target_state = "DLQ"
        elif any(s == "RETRY_PENDING" for s in child_states):
            target_state = "RETRY_PENDING"
        elif any(
            s in ("SQL_APPLIED", "VECTOR_APPLIED", "GRAPH_APPLIED")
            for s in child_states
        ):
            target_state = "PROJECTING"
        elif any(s == "VALIDATED" for s in child_states):
            target_state = "VALIDATED"
        elif any(s == "EXTRACTED" for s in child_states):
            target_state = "EXTRACTED"
        else:
            target_state = "RUNNING"

        async with db.execute(
            "SELECT state FROM pipeline_runs WHERE pipeline_run_id = ?",
            (pipeline_run_id,),
        ) as cursor:
            pipeline = await cursor.fetchone()
        if pipeline is None:
            raise ValueError("unknown pipeline run")
        if pipeline[0] == "QUEUED" and target_state in {
            "EXTRACTED",
            "VALIDATED",
            "PROJECTING",
            "COMMITTED",
        }:
            started = await MemoryDAO._transition_pipeline_run_in_tx(
                db,
                pipeline_run_id,
                to_state="RUNNING",
                event_type="AGGREGATE_STARTED",
            )
            if not started:
                return False
        return await MemoryDAO._transition_pipeline_run_in_tx(
            db,
            pipeline_run_id,
            to_state=target_state,
            event_type=event_type,
            failure_class=failure_class,
            event_detail=event_detail,
        )

    @staticmethod
    async def _transition_memory_mutation_in_tx(
        db: Any,
        mutation_id: str,
        *,
        to_state: str,
        failure_class: str | None = None,
    ) -> bool:
        """Enforce canonical memory mutation state transitions via CAS."""
        transitions = {
            "RECEIVED": {
                "EXTRACTED",
                "RETRY_PENDING",
                "DEAD_LETTER",
                "BLOCKED",
                "REJECTED",
                "ROLLING_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
            },
            "EXTRACTED": {
                "VALIDATED",
                "RETRY_PENDING",
                "DEAD_LETTER",
                "BLOCKED",
                "REJECTED",
                "ROLLING_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
            },
            "VALIDATED": {
                "SQL_APPLIED",
                "RETRY_PENDING",
                "DEAD_LETTER",
                "BLOCKED",
                "REJECTED",
                "ROLLING_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
            },
            "PENDING": {
                "SQL_APPLIED",
                "RETRY_PENDING",
                "DEAD_LETTER",
                "BLOCKED",
                "REJECTED",
                "ROLLING_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
            },
            "SQL_APPLIED": {
                "VECTOR_APPLIED",
                "RETRY_PENDING",
                "DEAD_LETTER",
                "BLOCKED",
                "REJECTED",
                "ROLLING_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
            },
            "VECTOR_APPLIED": {
                "GRAPH_APPLIED",
                "RETRY_PENDING",
                "DEAD_LETTER",
                "BLOCKED",
                "REJECTED",
                "ROLLING_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
            },
            "GRAPH_APPLIED": {
                "COMMITTED",
                "RETRY_PENDING",
                "DEAD_LETTER",
                "BLOCKED",
                "REJECTED",
                "ROLLING_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
            },
            "COMMITTED": {
                "RETRY_PENDING",
                "ROLLING_BACK",
                "PURGING",
                "PURGED",
            },
            "RETRY_PENDING": {
                "PENDING",
                "EXTRACTED",
                "DEAD_LETTER",
                "BLOCKED",
                "REJECTED",
                "ROLLING_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
            },
            "DEAD_LETTER": {"RETRY_PENDING", "ROLLING_BACK", "PURGED"},
            "BLOCKED": {"RETRY_PENDING", "ROLLING_BACK", "PURGED"},
            "ROLLING_BACK": {"ROLLED_BACK", "BLOCKED", "CANCELLED"},
            "PURGING": {"PURGED"},
            "ROLLED_BACK": set(),
            "PURGED": set(),
            "REJECTED": set(),
            "CANCELLED": set(),
        }
        async with db.execute(
            "SELECT state FROM memory_mutations WHERE mutation_id = ?",
            (mutation_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"unknown mutation {mutation_id}")
        current = str(row[0])
        if current == to_state:
            return True
        if to_state not in transitions.get(current, set()):
            return False
        cursor = await db.execute(
            "UPDATE memory_mutations SET state = ?, failure_class = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE mutation_id = ? AND state = ?",
            (to_state, failure_class, mutation_id, current),
        )
        if cursor.rowcount == 1:
            async with db.execute(
                "SELECT pipeline_run_id FROM memory_mutations WHERE mutation_id = ?",
                (mutation_id,),
            ) as c:
                pr = await c.fetchone()
                if pr and pr[0]:
                    await MemoryDAO._recompute_pipeline_state_in_tx(db, str(pr[0]))
            if to_state == "COMMITTED":
                await MemoryDAO._activate_committed_revision_in_tx(db, mutation_id)
            return True
        return False

    async def _transition_pipeline_mutations_in_tx(
        self,
        db: Any,
        pipeline_run_id: str,
        *,
        to_state: str,
        failure_class: str | None = None,
    ) -> int:
        """Transition each mutation through the canonical CAS state machine."""
        async with db.execute(
            "SELECT mutation_id FROM memory_mutations WHERE pipeline_run_id = ?",
            (pipeline_run_id,),
        ) as cursor:
            mutation_ids = [str(row[0]) for row in await cursor.fetchall()]
        changed = 0
        for mutation_id in mutation_ids:
            changed += int(
                await self._transition_memory_mutation_in_tx(
                    db,
                    mutation_id,
                    to_state=to_state,
                    failure_class=failure_class,
                )
            )
        return changed

    @staticmethod
    async def _activate_assertion_supersession_links_in_tx(
        db: Any, mutation_id: str
    ) -> None:
        """Activate explicit fact-level supersession only at canonical commit."""
        async with db.execute(
            "SELECT l.target_assertion_id, source.valid_from "
            "FROM v4_assertion_links l "
            "JOIN v4_assertions source ON source.assertion_id = l.source_assertion_id "
            "WHERE l.mutation_id = ? AND l.relation_type = 'SUPERSEDES'",
            (mutation_id,),
        ) as cursor:
            targets = await cursor.fetchall()
        for target_id, source_valid_from in targets:
            await db.execute(
                "UPDATE v4_assertions SET status = 'SUPERSEDED', "
                "valid_to = CASE WHEN valid_to = '' AND ? != '' THEN ? ELSE valid_to END "
                "WHERE assertion_id = ? AND status = 'ACTIVE'",
                (source_valid_from or "", source_valid_from or "", target_id),
            )

    @staticmethod
    async def _reconcile_revision_activation_in_tx(db: Any, revision_id: str) -> None:
        """Evaluate canonical completeness and head invariant for a revision."""
        if not revision_id:
            return
        async with db.execute(
            "SELECT r.tenant_id, r.document_id, r.supersedes_revision_id, "
            "r.manifest_hash, r.manifest_frozen_at, r.status "
            "FROM document_revisions r WHERE r.revision_id = ?",
            (revision_id,),
        ) as cursor:
            rev = await cursor.fetchone()
        if rev is None:
            return
        if rev[5] != "PENDING":
            return
        if not rev[3] or not rev[4]:
            return
        tenant_id = str(rev[0])
        document_id = str(rev[1])
        supersedes_revision_id = str(rev[2]) if rev[2] else None

        async with db.execute(
            "SELECT state FROM memory_mutations WHERE revision_id = ?",
            (revision_id,),
        ) as cursor:
            child_states = [r[0] for r in await cursor.fetchall()]
        if not child_states or any(s != "COMMITTED" for s in child_states):
            return
        async with db.execute(
            "SELECT COUNT(*) FROM source_chunks WHERE revision_id = ?",
            (revision_id,),
        ) as cursor:
            manifest_size = int((await cursor.fetchone())[0])
        async with db.execute(
            "SELECT COUNT(*) FROM source_chunks c WHERE c.revision_id = ? "
            "AND NOT EXISTS (SELECT 1 FROM memory_mutations m "
            "WHERE m.revision_id = c.revision_id AND m.chunk_id = c.chunk_id "
            "AND m.state = 'COMMITTED')",
            (revision_id,),
        ) as cursor:
            missing_work = int((await cursor.fetchone())[0])
        if manifest_size < 1 or missing_work > 0:
            return

        valid_from = ""
        if supersedes_revision_id:
            async with db.execute(
                "SELECT metadata_json FROM memory_mutations WHERE revision_id = ? AND state = 'COMMITTED' LIMIT 1",
                (revision_id,),
            ) as cursor:
                m_row = await cursor.fetchone()
            if m_row and m_row[0]:
                try:
                    meta = json.loads(str(m_row[0]))
                    if isinstance(meta, dict):
                        valid_from = str(meta.get("valid_from") or "")
                except (TypeError, json.JSONDecodeError):
                    pass

            async with db.execute(
                "SELECT status FROM document_revisions WHERE revision_id = ?",
                (supersedes_revision_id,),
            ) as cur:
                pred = await cur.fetchone()
            if pred is None or pred[0] != "ACTIVE":
                raise ValueError(
                    f"revision supersession conflict: predecessor {supersedes_revision_id} is no longer ACTIVE ({pred[0] if pred else 'missing'})"
                )
            predecessor_update = await db.execute(
                "UPDATE document_revisions SET status = 'SUPERSEDED' "
                "WHERE revision_id = ? AND document_id = ? AND status = 'ACTIVE'",
                (supersedes_revision_id, document_id),
            )
            if predecessor_update.rowcount != 1:
                raise ValueError(
                    "revision supersession conflict: predecessor head changed"
                )
            successor_update = await db.execute(
                "UPDATE document_revisions SET status = 'ACTIVE' "
                "WHERE revision_id = ? AND status = 'PENDING'",
                (revision_id,),
            )
            if successor_update.rowcount != 1:
                raise ValueError(
                    "revision supersession conflict: successor is not pending"
                )
            await db.execute(
                "UPDATE v4_assertions SET status = 'SUPERSEDED', "
                "valid_to = CASE WHEN valid_to = '' AND ? != '' THEN ? ELSE valid_to END "
                "WHERE tenant_id = ? AND revision_id = ? AND status = 'ACTIVE'",
                (valid_from, valid_from, tenant_id, supersedes_revision_id),
            )
        else:
            cursor = await db.execute(
                "UPDATE document_revisions SET status = 'ACTIVE' "
                "WHERE revision_id = ? AND status = 'PENDING' AND NOT EXISTS ("
                "SELECT 1 FROM document_revisions active "
                "WHERE active.document_id = document_revisions.document_id "
                "AND active.status = 'ACTIVE')",
                (revision_id,),
            )
            if cursor.rowcount != 1:
                async with db.execute(
                    "SELECT status FROM document_revisions WHERE revision_id = ?",
                    (revision_id,),
                ) as cur:
                    current = await cur.fetchone()
                if current is not None and current[0] == "ACTIVE":
                    return
                raise ValueError(
                    "revision-head conflict: document already has an ACTIVE revision"
                )

    @staticmethod
    async def _activate_committed_revision_in_tx(db: Any, mutation_id: str) -> None:
        """Make supersession visible only after the replacing mutation commits."""
        async with db.execute(
            "SELECT m.revision_id, pr.state "
            "FROM memory_mutations m "
            "LEFT JOIN pipeline_runs pr ON pr.pipeline_run_id = m.pipeline_run_id "
            "WHERE m.mutation_id = ?",
            (mutation_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None and row[1] in (None, "COMMITTED"):
            await MemoryDAO._activate_assertion_supersession_links_in_tx(
                db, mutation_id
            )
        if row is not None and row[0]:
            await MemoryDAO._reconcile_revision_activation_in_tx(db, str(row[0]))

    @staticmethod
    async def _restore_pipeline_supersession_in_tx(
        db: Any, pipeline_run_id: str
    ) -> None:
        """Undo revision activation when its owning pipeline is rolled back."""
        async with db.execute(
            "SELECT DISTINCT l.target_assertion_id FROM v4_assertion_links l "
            "JOIN v4_assertions source ON source.assertion_id = l.source_assertion_id "
            "JOIN memory_mutations m ON m.mutation_id = source.mutation_id "
            "WHERE m.pipeline_run_id = ? AND l.relation_type = 'SUPERSEDES'",
            (pipeline_run_id,),
        ) as cursor:
            fact_level_targets = [str(row[0]) for row in await cursor.fetchall()]
        for target_id in fact_level_targets:
            async with db.execute(
                "SELECT 1 FROM v4_assertion_links l "
                "JOIN v4_assertions source ON source.assertion_id = l.source_assertion_id "
                "JOIN memory_mutations m ON m.mutation_id = source.mutation_id "
                "WHERE l.target_assertion_id = ? AND l.relation_type = 'SUPERSEDES' "
                "AND m.pipeline_run_id != ? AND m.state = 'COMMITTED' LIMIT 1",
                (target_id, pipeline_run_id),
            ) as cursor:
                still_superseded = await cursor.fetchone()
            if still_superseded is None:
                await db.execute(
                    "UPDATE v4_assertions SET status = 'ACTIVE', valid_to = '' "
                    "WHERE assertion_id = ? AND status = 'SUPERSEDED'",
                    (target_id,),
                )
        async with db.execute(
            "SELECT DISTINCT m.tenant_id, m.revision_id, m.metadata_json, "
            "r.document_id, r.supersedes_revision_id, d.status "
            "FROM memory_mutations m "
            "LEFT JOIN document_revisions r ON r.revision_id = m.revision_id "
            "LEFT JOIN documents d ON d.document_id = r.document_id "
            "WHERE m.pipeline_run_id = ?",
            (pipeline_run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            revision_id = str(row[1] or "")
            if not revision_id or row[5] == "PURGED":
                continue
            async with db.execute(
                "SELECT 1 FROM memory_mutations WHERE revision_id = ? "
                "AND pipeline_run_id != ? AND state = 'COMMITTED' LIMIT 1",
                (revision_id, pipeline_run_id),
            ) as cursor:
                if await cursor.fetchone() is not None:
                    continue
            await db.execute(
                "UPDATE document_revisions SET status = 'ROLLED_BACK' "
                "WHERE revision_id = ? AND status IN ('ACTIVE', 'PENDING')",
                (revision_id,),
            )
            predecessor_id = str(row[4] or "")
            if not predecessor_id:
                continue
            try:
                metadata = json.loads(str(row[2] or "{}"))
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            valid_from = (
                str(metadata.get("valid_from") or "")
                if isinstance(metadata, dict)
                else ""
            )
            await db.execute(
                "UPDATE document_revisions SET status = 'ACTIVE' "
                "WHERE revision_id = ? AND document_id = ? "
                "AND status = 'SUPERSEDED'",
                (predecessor_id, row[3]),
            )
            await db.execute(
                "UPDATE v4_assertions SET status = 'ACTIVE', "
                "valid_to = CASE WHEN ? != '' AND valid_to = ? THEN '' ELSE valid_to END "
                "WHERE tenant_id = ? AND revision_id = ? AND status = 'SUPERSEDED'",
                (valid_from, valid_from, row[0], predecessor_id),
            )

    async def _purge_mutations_in_tx(
        self, db: Any, *, where_sql: str, params: tuple[Any, ...]
    ) -> list[str]:
        """Apply the destructive mutation transition without bypassing its CAS fence."""
        async with db.execute(
            "SELECT mutation_id FROM memory_mutations WHERE "
            + where_sql
            + " AND state NOT IN ('PURGED', 'ROLLED_BACK', 'REJECTED', 'CANCELLED')",
            params,
        ) as cursor:
            mutation_ids = [str(row[0]) for row in await cursor.fetchall()]
        purged: list[str] = []
        for mutation_id in mutation_ids:
            if await self._transition_memory_mutation_in_tx(
                db, mutation_id, to_state="PURGED"
            ):
                purged.append(mutation_id)
        return purged

    async def get_pipeline_run(self, pipeline_run_id: str) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM pipeline_runs WHERE pipeline_run_id = ?",
                (pipeline_run_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            result = dict(row)
            if result.get("workspace_id"):
                result["workspace_id"] = await self._catalog.external_id_in_tx(
                    db,
                    tenant_id=str(row["tenant_id"]),
                    kind="workspace",
                    physical_id=str(row["workspace_id"]),
                )
            if result.get("dataset_id"):
                result["dataset_id"] = await self._catalog.external_id_in_tx(
                    db,
                    tenant_id=str(row["tenant_id"]),
                    kind="dataset",
                    physical_id=str(row["dataset_id"]),
                )
            async with db.execute(
                "SELECT from_state, to_state, event_type, detail_json, created_at "
                "FROM pipeline_run_events WHERE pipeline_run_id = ? "
                "ORDER BY created_at, event_id",
                (pipeline_run_id,),
            ) as cursor:
                result["events"] = [dict(item) for item in await cursor.fetchall()]
        return result

    async def set_mutation_state(
        self,
        agent_id: str,
        mutation_id: str,
        state: str,
        *,
        failure_class: str | None = None,
        event_detail: dict[str, Any] | None = None,
    ) -> bool:
        _assert_valid_agent_id(agent_id)
        allowed = {
            "RECEIVED",
            "EXTRACTED",
            "VALIDATED",
            "SQL_APPLIED",
            "VECTOR_APPLIED",
            "GRAPH_APPLIED",
            "COMMITTED",
            "REJECTED",
            "RETRY_PENDING",
            "DEAD_LETTER",
            "ROLLING_BACK",
            "ROLLED_BACK",
            "BLOCKED",
        }
        if state not in allowed:
            raise ValueError("invalid v4 mutation state")
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT pipeline_run_id FROM memory_mutations "
                "WHERE mutation_id = ? AND agent_id = ?",
                (mutation_id, agent_id),
            ) as pipeline_cursor:
                pipeline_row = await pipeline_cursor.fetchone()
            if pipeline_row is None:
                return False
            changed = await self._transition_memory_mutation_in_tx(
                db,
                mutation_id,
                to_state=state,
                failure_class=failure_class[:120] if failure_class else None,
            )
            if changed and state == "VALIDATED":
                await db.execute(
                    "UPDATE projection_outbox SET state = 'PENDING', updated_at = CURRENT_TIMESTAMP "
                    "WHERE mutation_id = ? AND state = 'BLOCKED_VALIDATION'",
                    (mutation_id,),
                )
            elif changed and state == "REJECTED":
                await db.execute(
                    "UPDATE projection_outbox SET state = 'CANCELLED', claim_token = NULL, claimed_by = NULL, "
                    "lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                    "WHERE mutation_id = ? AND state IN ('BLOCKED_VALIDATION', 'PENDING', 'RETRY_PENDING')",
                    (mutation_id,),
                )
            if changed and pipeline_row and pipeline_row[0]:
                await self._recompute_pipeline_state_in_tx(
                    db,
                    str(pipeline_row[0]),
                    event_type=f"MUTATION_{state}",
                    failure_class=failure_class,
                    event_detail=event_detail,
                )
            await db.commit()
        return changed

    async def record_mutation_tier3_audit(
        self, agent_id: str, mutation_id: str, audit: dict[str, Any]
    ) -> bool:
        """Persist the already-redacted Tier-3 receipt with its mutation.

        The receipt is held in a reserved metadata namespace so no raw model
        response, prompt, or caller-provided metadata can be exposed through
        status APIs.  The worker writes this before its state transition so
        the accompanying pipeline event and V4 status are coherent.
        """
        _assert_valid_agent_id(agent_id)
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT metadata_json FROM memory_mutations "
                "WHERE mutation_id = ? AND agent_id = ?",
                (mutation_id, agent_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return False
            try:
                metadata = json.loads(str(row[0] or "{}"))
            except (TypeError, ValueError):
                metadata = {}
            metadata["_mesa_tier3_audit"] = audit
            cursor = await db.execute(
                "UPDATE memory_mutations SET metadata_json = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE mutation_id = ? AND agent_id = ?",
                (json.dumps(metadata, sort_keys=True), mutation_id, agent_id),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def record_mutation_extraction(
        self, agent_id: str, mutation_id: str, triplets: list[dict[str, Any]]
    ) -> bool:
        """Persist the validated extraction that projection workers replay.

        It lives in a reserved namespace inside the immutable candidate's
        metadata document so existing deployments need no destructive schema
        rewrite.  Client supplied metadata cannot overwrite this namespace.
        """
        _assert_valid_agent_id(agent_id)
        normalized: list[dict[str, Any]] = []
        for triplet in triplets:
            tail = triplet.get("tail")
            literal = triplet.get("literal_value")
            if (
                not triplet.get("head")
                or not triplet.get("relation")
                or (tail is None) == (literal is None)
            ):
                continue
            normalized.append(
                {
                    "head": str(triplet["head"]),
                    "relation": str(triplet["relation"]),
                    "tail": str(tail) if tail is not None else None,
                    "literal_value": str(literal) if literal is not None else None,
                    "confidence": float(
                        1.0
                        if triplet.get("confidence") is None
                        else triplet["confidence"]
                    ),
                    "fact_text": str(triplet.get("fact_text") or ""),
                    "valid_from": triplet.get("valid_from"),
                    "valid_to": triplet.get("valid_to"),
                    "source_span": triplet.get("source_span"),
                    "supersedes": triplet.get("supersedes"),
                    "object_type": triplet.get("object_type")
                    or triplet.get("tail_type"),
                    "metadata": (
                        triplet.get("metadata")
                        if isinstance(triplet.get("metadata"), dict)
                        else {}
                    ),
                }
            )
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT metadata_json, pipeline_run_id FROM memory_mutations "
                "WHERE mutation_id = ? AND agent_id = ?",
                (mutation_id, agent_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await db.commit()
                return False
            metadata = json.loads(row[0] or "{}")
            metadata["_mesa_v4_projection_triplets"] = normalized
            cursor = await db.execute(
                "UPDATE memory_mutations SET metadata_json = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE mutation_id = ? AND agent_id = ? AND state IN ('RECEIVED', 'EXTRACTED', 'RETRY_PENDING')",
                (json.dumps(metadata, sort_keys=True), mutation_id, agent_id),
            )
            changed = (
                cursor.rowcount == 1
                and await self._transition_memory_mutation_in_tx(
                    db,
                    mutation_id,
                    to_state="EXTRACTED",
                )
            )
            await db.commit()
        return changed

    @staticmethod
    def _parse_projection_mutation_row(row: Any) -> dict[str, Any]:
        record = dict(row)
        metadata = json.loads(record.get("metadata_json") or "{}")
        record["metadata"] = {
            key: value
            for key, value in metadata.items()
            if not key.startswith("_mesa_")
        }
        record["embedding_identity_snapshot"] = metadata.get("_mesa_embedding_identity")
        record["legal_identity"] = canonicalize_trusted_legal_identity(
            metadata.get("_mesa_legal_identity")
        )
        record["document_chunk"] = metadata.get("_mesa_document_chunk") is True
        record["projection_triplets"] = metadata.get("_mesa_v4_projection_triplets", [])
        return record

    async def get_projection_mutation(self, mutation_id: str) -> dict[str, Any] | None:
        """Load the canonical payload and durable extraction for one projector."""
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM memory_mutations WHERE mutation_id = ?", (mutation_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return self._parse_projection_mutation_row(row)

    async def get_projection_mutations_batch(
        self, mutation_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Load canonical payloads for multiple mutations in one read connection."""
        unique_ids = list(dict.fromkeys(str(mid) for mid in mutation_ids if mid))
        if not unique_ids:
            return {}
        mutations: dict[str, dict[str, Any]] = {}
        async with self._sql.connection() as db:
            for offset in range(0, len(unique_ids), 200):
                batch = unique_ids[offset : offset + 200]
                placeholders = ",".join("?" * len(batch))
                async with db.execute(
                    f"SELECT * FROM memory_mutations WHERE mutation_id IN ({placeholders})",
                    batch,
                ) as cursor:
                    for row in await cursor.fetchall():
                        mutations[str(row["mutation_id"])] = (
                            self._parse_projection_mutation_row(row)
                        )
        return mutations

    async def _record_mutation_artifact_in_tx(
        self,
        db: Any,
        mutation_id: str,
        *,
        store_name: str,
        artifact_kind: str,
        artifact_id: str,
        metadata: dict[str, Any] | None = None,
        owner_cache: dict[str, Any] | None = None,
    ) -> None:
        if not mutation_id or not store_name or not artifact_kind or not artifact_id:
            raise ValueError("artifact identity fields are required")
        mutation = owner_cache.get(mutation_id) if owner_cache is not None else None
        if mutation is None:
            async with db.execute(
                "SELECT m.tenant_id, m.agent_id, m.dataset_id, m.document_id, m.revision_id, m.chunk_id, "
                "m.source_ref, m.evidence_span, m.pipeline_run_id, m.state as mutation_state, pr.state as pipeline_state "
                "FROM memory_mutations m "
                "LEFT JOIN pipeline_runs pr ON pr.pipeline_run_id = m.pipeline_run_id "
                "WHERE m.mutation_id = ?",
                (mutation_id,),
            ) as cursor:
                mutation = await cursor.fetchone()
            if mutation is None:
                raise ValueError("unknown mutation artifact owner")
            if mutation["mutation_state"] in (
                "ROLLING_BACK",
                "ROLLED_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
                "REJECTED",
            ) or mutation["pipeline_state"] in (
                "ROLLING_BACK",
                "ROLLED_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
                "REJECTED",
            ):
                raise ValueError(
                    f"cannot register artifact for mutation in state {mutation['mutation_state']}"
                )
            if mutation["document_id"]:
                async with db.execute(
                    "SELECT status FROM documents WHERE document_id = ?",
                    (mutation["document_id"],),
                ) as doc_cur:
                    doc_row = await doc_cur.fetchone()
                if doc_row and doc_row[0] == "PURGED":
                    raise ValueError(
                        f"cannot register artifact for purged document {mutation['document_id']}"
                    )
            if owner_cache is not None:
                owner_cache[mutation_id] = mutation
        artifact_row_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO memory_artifacts "
            "(artifact_row_id, mutation_id, store_name, artifact_kind, artifact_id, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(mutation_id, store_name, artifact_kind, artifact_id) "
            "DO UPDATE SET metadata_json = excluded.metadata_json",
            (
                artifact_row_id,
                mutation_id,
                store_name,
                artifact_kind,
                artifact_id,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        async with db.execute(
            "SELECT artifact_row_id FROM memory_artifacts WHERE mutation_id = ? "
            "AND store_name = ? AND artifact_kind = ? AND artifact_id = ?",
            (mutation_id, store_name, artifact_kind, artifact_id),
        ) as cursor:
            receipt = await cursor.fetchone()
        if receipt is None:
            raise RuntimeError("artifact receipt was not persisted")
        registry_agent_id = (
            "__tenant__" if store_name == "SQL" else str(mutation[1])
        )
        registry_id = str(
            uuid.uuid5(
                _V4_CATALOG_NAMESPACE,
                "\x1f".join(
                    (
                        str(mutation[0]),
                        registry_agent_id,
                        store_name,
                        artifact_kind,
                        artifact_id,
                    )
                ),
            )
        )
        await db.execute(
            "INSERT INTO artifact_registry "
            "(registry_id, tenant_id, agent_id, dataset_id, store_name, artifact_kind, "
            "physical_artifact_id, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id) "
            "DO UPDATE SET metadata_json = excluded.metadata_json",
            (
                registry_id,
                mutation[0],
                registry_agent_id,
                mutation[2],
                store_name,
                artifact_kind,
                artifact_id,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        ownership_id = str(
            uuid.uuid5(
                _V4_CATALOG_NAMESPACE,
                f"{registry_id}\x1f{mutation_id}\x1f{mutation[5] or ''}",
            )
        )
        await db.execute(
            "INSERT OR IGNORE INTO artifact_sources "
            "(source_ownership_id, registry_id, mutation_id, pipeline_run_id, "
            "dataset_id, document_id, revision_id, chunk_id, source_ref, "
            "evidence_span) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ownership_id,
                registry_id,
                mutation_id,
                mutation[8],
                mutation[2],
                mutation[3],
                mutation[4],
                mutation[5],
                mutation[6] or "",
                mutation[7] or "",
            ),
        )

    async def record_mutation_artifact(
        self,
        mutation_id: str,
        *,
        store_name: str,
        artifact_kind: str,
        artifact_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not mutation_id or not store_name or not artifact_kind or not artifact_id:
            raise ValueError("artifact identity fields are required")
        async with self._sql.transaction() as db:
            await self._record_mutation_artifact_in_tx(
                db,
                mutation_id,
                store_name=store_name,
                artifact_kind=artifact_kind,
                artifact_id=artifact_id,
                metadata=metadata,
            )
            await db.commit()

    async def record_mutation_artifacts_batch(
        self,
        artifacts: list[tuple[str, str, str, str, dict[str, Any] | None]],
    ) -> None:
        """Persist a batch of artifact receipts in one SQLite transaction."""
        if not artifacts:
            return
        async with self._sql.transaction() as db:
            owner_cache: dict[str, Any] = {}
            for mutation_id, store_name, artifact_kind, artifact_id, metadata in artifacts:
                await self._record_mutation_artifact_in_tx(
                    db,
                    mutation_id,
                    store_name=store_name,
                    artifact_kind=artifact_kind,
                    artifact_id=artifact_id,
                    metadata=metadata,
                    owner_cache=owner_cache,
                )
            await db.commit()

    async def claim_projection_outbox(
        self, *, worker_id: str, limit: int = 1, lease_seconds: int = 300
    ) -> list[dict[str, Any]]:
        """Fence bounded projection work; projector retries remain idempotent."""
        if not worker_id or not 1 <= limit <= 100 or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid projection claim bounds")
        token = str(uuid.uuid4())
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT p.projection_id FROM projection_outbox p "
                "WHERE (p.state = 'PENDING' "
                "OR (p.state = 'RETRY_PENDING' AND (p.lease_expires_at IS NULL "
                "OR p.lease_expires_at <= CURRENT_TIMESTAMP)) "
                "OR (p.state = 'IN_FLIGHT' AND p.lease_expires_at <= CURRENT_TIMESTAMP)) "
                "AND (p.projection_name = 'SQL' "
                "OR (p.projection_name = 'VECTOR' AND EXISTS "
                "(SELECT 1 FROM projection_outbox s WHERE s.mutation_id = p.mutation_id "
                "AND s.projection_name = 'SQL' AND s.state = 'COMPLETED')) "
                "OR (p.projection_name = 'GRAPH' AND EXISTS "
                "(SELECT 1 FROM projection_outbox v WHERE v.mutation_id = p.mutation_id "
                "AND v.projection_name = 'VECTOR' AND v.state IN ('COMPLETED', 'SKIPPED')))) "
                "ORDER BY CASE p.projection_name WHEN 'SQL' THEN 0 WHEN 'VECTOR' THEN 1 "
                "WHEN 'GRAPH' THEN 2 ELSE 99 END, p.created_at LIMIT ?",
                (limit,),
            ) as cursor:
                identifiers = [row[0] for row in await cursor.fetchall()]
            claimed: list[str] = []
            for projection_id in identifiers:
                cursor = await db.execute(
                    "UPDATE projection_outbox SET state = 'IN_FLIGHT', claim_token = ?, claimed_by = ?, "
                    "lease_expires_at = datetime('now', ?), attempt_count = attempt_count + 1, updated_at = CURRENT_TIMESTAMP "
                    "WHERE projection_id = ? AND (state = 'PENDING' "
                    "OR (state = 'RETRY_PENDING' AND (lease_expires_at IS NULL "
                    "OR lease_expires_at <= CURRENT_TIMESTAMP)) "
                    "OR (state = 'IN_FLIGHT' AND lease_expires_at <= CURRENT_TIMESTAMP))",
                    (token, worker_id, f"+{lease_seconds} seconds", projection_id),
                )
                if cursor.rowcount == 1:
                    claimed.append(projection_id)
            if not claimed:
                await db.commit()
                return []
            placeholders = ",".join("?" for _ in claimed)
            async with db.execute(
                f"SELECT * FROM projection_outbox WHERE projection_id IN ({placeholders})",
                claimed,
            ) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            await db.commit()
        return rows

    async def _complete_projection_outbox_item_in_tx(
        self,
        db: Any,
        projection_id: str,
        *,
        worker_id: str,
        claim_token: str,
        outcome: str,
    ) -> tuple[bool, bool]:
        """Validate and apply one fenced outbox completion inside an open tx.

        Returns ``(completed, is_fenced_in_flight)``.
        """
        async with db.execute(
            "SELECT p.*, m.state as mutation_state, m.document_id, pr.state as pipeline_state "
            "FROM projection_outbox p "
            "JOIN memory_mutations m ON m.mutation_id = p.mutation_id "
            "LEFT JOIN pipeline_runs pr ON pr.pipeline_run_id = m.pipeline_run_id "
            "WHERE p.projection_id = ?",
            (projection_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return (False, False)
        is_fenced = row["mutation_state"] in (
            "ROLLING_BACK",
            "ROLLED_BACK",
            "PURGING",
            "PURGED",
            "CANCELLED",
            "REJECTED",
        ) or row["pipeline_state"] in (
            "ROLLING_BACK",
            "ROLLED_BACK",
            "PURGING",
            "PURGED",
            "CANCELLED",
            "REJECTED",
        )
        doc_id = row["document_id"] if "document_id" in row.keys() else None
        if not is_fenced and doc_id:
            async with db.execute(
                "SELECT status FROM documents WHERE document_id = ?",
                (row["document_id"],),
            ) as doc_cur:
                doc_row = await doc_cur.fetchone()
            if doc_row and doc_row[0] == "PURGED":
                is_fenced = True
        if (
            row["state"] != "IN_FLIGHT"
            or row["claimed_by"] != worker_id
            or row["claim_token"] != claim_token
            or is_fenced
        ):
            return (False, bool(is_fenced))
        started_at = (
            row["updated_at"]
            if row["updated_at"] is not None
            else row["created_at"]
        )
        await db.execute(
            "INSERT OR IGNORE INTO projection_attempts "
            "(attempt_id, projection_id, attempt_number, outcome, started_at, finished_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (
                str(uuid.uuid4()),
                projection_id,
                row["attempt_count"],
                outcome[:120],
                started_at,
            ),
        )
        if outcome == "SKIPPED" and not (
            (row["projection_name"] == "VECTOR" and not self._embedding_enabled)
            or (row["projection_name"] == "GRAPH" and not self._graph_enabled)
        ):
            raise ValueError("only a disabled VECTOR or GRAPH lane may be skipped")
        cursor = await db.execute(
            "UPDATE projection_outbox SET state = ?, claim_token = NULL, claimed_by = NULL, "
            "lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE projection_id = ? AND claim_token = ?",
            (
                "SKIPPED" if outcome == "SKIPPED" else "COMPLETED",
                projection_id,
                claim_token,
            ),
        )
        if cursor.rowcount == 1:
            await self._advance_mutation_projection_state(
                db, str(row["mutation_id"])
            )
            return (True, False)
        return (False, False)

    async def complete_projection_outbox(
        self, projection_id: str, *, worker_id: str, claim_token: str, outcome: str
    ) -> bool:
        """Record a fenced successful projection and its immutable attempt receipt."""
        async with self._sql.transaction() as db:
            completed, is_fenced_in_flight = (
                await self._complete_projection_outbox_item_in_tx(
                    db,
                    projection_id,
                    worker_id=worker_id,
                    claim_token=claim_token,
                    outcome=outcome,
                )
            )
            if not is_fenced_in_flight:
                await db.commit()
                return completed
            async with db.execute(
                "SELECT p.mutation_id, p.state AS outbox_state, m.agent_id "
                "FROM projection_outbox p "
                "JOIN memory_mutations m ON m.mutation_id = p.mutation_id "
                "WHERE p.projection_id = ?",
                (projection_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row and row["outbox_state"] == "IN_FLIGHT":
                await db.execute(
                    "UPDATE projection_outbox SET state = 'CANCELLED', claim_token = NULL, claimed_by = NULL, "
                    "lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE projection_id = ?",
                    (projection_id,),
                )
            mutation_id_str = str(row["mutation_id"])
            agent_id_str = str(row["agent_id"]) if row and row["agent_id"] else ""
            async with db.execute(
                "SELECT store_name, artifact_kind, artifact_id FROM memory_artifacts WHERE mutation_id = ?",
                (mutation_id_str,),
            ) as art_cur:
                arts = await art_cur.fetchall()
            await db.commit()

        failed_compensations: list[tuple[str, str, str]] = []
        for art in arts:
            s_name, a_kind, a_id = art[0], art[1], art[2]
            if s_name == "VECTOR":
                try:
                    await self._vec.hard_delete(a_id, agent_id_str)
                except Exception:
                    failed_compensations.append((s_name, a_kind, a_id))
            elif s_name == "GRAPH":
                try:
                    g = self._require_graph()
                    if a_kind == "ASSERTION":
                        await g.delete_assertions(
                            agent_id=agent_id_str, assertion_ids=[a_id]
                        )
                    elif a_kind == "ENTITY":
                        await g.delete_nodes(
                            purge_id=f"fence_comp:{mutation_id_str}",
                            agent_id=agent_id_str,
                            node_ids=[a_id],
                        )
                except Exception:
                    failed_compensations.append((s_name, a_kind, a_id))
        if failed_compensations:
            await self._enqueue_unowned_projection_cleanup(
                mutation_id_str, failed_compensations
            )
        else:
            await self._finalize_terminal_projection_compensation(mutation_id_str)
        return False

    async def complete_projection_outbox_batch(
        self, items: list[dict[str, Any]], *, worker_id: str, outcome: str = "APPLIED"
    ) -> int:
        """Record fenced successful projections in one SQLite transaction."""
        if not items:
            return 0

        completed_count = 0
        fenced_items: list[dict[str, Any]] = []
        async with self._sql.transaction() as db:
            for p in items:
                completed, is_fenced_in_flight = (
                    await self._complete_projection_outbox_item_in_tx(
                        db,
                        str(p["projection_id"]),
                        worker_id=worker_id,
                        claim_token=str(p["claim_token"]),
                        outcome=outcome,
                    )
                )
                if completed:
                    completed_count += 1
                elif is_fenced_in_flight:
                    fenced_items.append(p)
            await db.commit()

        for p in fenced_items:
            await self.complete_projection_outbox(
                str(p["projection_id"]),
                worker_id=worker_id,
                claim_token=str(p["claim_token"]),
                outcome=outcome,
            )
        return completed_count

    async def requeue_skipped_vector_projections(
        self, *, projection_name: str = "VECTOR"
    ) -> int:
        """Backfill a disabled lane under the combined runtime writer lock.

        Every mutation whose VECTOR lane is unfinished and that has no vector
        artifact takes the active producer identity, so a vector-free store that
        adopted a new embedding model does not strand its pending lanes.
        """
        if projection_name not in {"VECTOR", "GRAPH"}:
            raise ValueError("only VECTOR and GRAPH lanes can be backfilled")
        producer_identity = (
            getattr(self._vec, "embedding_identity", None)
            if projection_name == "VECTOR"
            else None
        )
        async with self._sql.transaction() as db:
            if producer_identity is not None:
                identity_snapshot: dict[str, Any] = (
                    producer_identity.as_dict()
                    if hasattr(producer_identity, "as_dict")
                    else {
                        "embedding_space_id": str(
                            getattr(producer_identity, "embedding_space_id", "")
                        ),
                        "provider": str(producer_identity.provider),
                        "model": str(producer_identity.model),
                        "version": str(producer_identity.version),
                        "dimension": int(producer_identity.dimension),
                        "normalized": bool(
                            getattr(producer_identity, "normalized", False)
                        ),
                        "model_revision": getattr(
                            producer_identity, "model_revision", None
                        ),
                    }
                )
                async with db.execute(
                    "SELECT m.mutation_id, m.metadata_json FROM memory_mutations m "
                    "JOIN projection_outbox p ON p.mutation_id = m.mutation_id "
                    "WHERE p.projection_name = 'VECTOR' AND p.state <> 'COMPLETED' "
                    "AND NOT EXISTS (SELECT 1 FROM memory_artifacts a "
                    "WHERE a.mutation_id = m.mutation_id AND a.store_name = 'VECTOR')"
                ) as cursor:
                    skipped_mutations = await cursor.fetchall()
                for mutation_id, raw_metadata in skipped_mutations:
                    metadata = json.loads(raw_metadata or "{}")
                    if metadata.get("_mesa_embedding_identity") == identity_snapshot:
                        continue
                    metadata["_mesa_embedding_identity"] = identity_snapshot
                    await db.execute(
                        "UPDATE memory_mutations SET embedding_provider = ?, "
                        "embedding_model = ?, embedding_version = ?, "
                        "embedding_dimension = ?, metadata_json = ?, "
                        "updated_at = CURRENT_TIMESTAMP WHERE mutation_id = ?",
                        (
                            str(identity_snapshot["provider"]),
                            str(identity_snapshot["model"]),
                            str(identity_snapshot["version"]),
                            int(identity_snapshot["dimension"]),
                            json.dumps(metadata, sort_keys=True),
                            str(mutation_id),
                        ),
                    )
            cursor = await db.execute(
                "UPDATE projection_outbox SET state = 'PENDING', attempt_count = 0, "
                "claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL, "
                "last_error_class = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE projection_name = ? AND state = 'SKIPPED'",
                (projection_name,),
            )
            await db.commit()
            return cursor.rowcount

    async def fail_projection_outbox(
        self,
        projection_id: str,
        *,
        worker_id: str,
        claim_token: str,
        error_class: str,
        retryable: bool,
    ) -> bool:
        """Fence a failed lane and move it to retry or durable DLQ."""
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT p.*, m.state AS mutation_state FROM projection_outbox p "
                "JOIN memory_mutations m ON m.mutation_id = p.mutation_id "
                "WHERE p.projection_id = ?",
                (projection_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if (
                row is None
                or row["state"] != "IN_FLIGHT"
                or row["claimed_by"] != worker_id
                or row["claim_token"] != claim_token
            ):
                await db.commit()
                return False
            terminal = (not retryable) or int(row["attempt_count"]) >= int(
                row["retry_limit"]
            )
            next_state = "DEAD_LETTER" if terminal else "RETRY_PENDING"
            started_at = (
                row["updated_at"]
                if row["updated_at"] is not None
                else row["created_at"]
            )
            await db.execute(
                "INSERT OR IGNORE INTO projection_attempts "
                "(attempt_id, projection_id, attempt_number, outcome, error_class, started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (
                    str(uuid.uuid4()),
                    projection_id,
                    row["attempt_count"],
                    next_state,
                    error_class[:120],
                    started_at,
                ),
            )
            cursor = await db.execute(
                "UPDATE projection_outbox SET state = ?, claim_token = NULL, claimed_by = NULL, "
                "lease_expires_at = datetime('now', ?), last_error_class = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE projection_id = ? AND claim_token = ?",
                (
                    next_state,
                    None
                    if terminal
                    else f"+{_retry_delay_seconds(int(row['attempt_count']))} seconds",
                    error_class[:120],
                    projection_id,
                    claim_token,
                ),
            )
            if cursor.rowcount == 1 and row["mutation_state"] != "COMMITTED":
                mutation_state = "DEAD_LETTER" if terminal else "RETRY_PENDING"
                await self._transition_memory_mutation_in_tx(
                    db,
                    str(row["mutation_id"]),
                    to_state=mutation_state,
                    failure_class=error_class[:120],
                )
                async with db.execute(
                    "SELECT pipeline_run_id FROM memory_mutations WHERE mutation_id = ?",
                    (row["mutation_id"],),
                ) as pipeline_cursor:
                    pipeline = await pipeline_cursor.fetchone()
                if pipeline and pipeline[0]:
                    await self._transition_pipeline_run_in_tx(
                        db,
                        str(pipeline[0]),
                        to_state="DLQ" if terminal else "RETRY_PENDING",
                        event_type="PROJECTION_FAILED",
                        failure_class=error_class[:120],
                    )
            await db.commit()
        return cursor.rowcount == 1

    async def request_pipeline_rollback(self, pipeline_run_id: str) -> dict[str, Any]:
        """Release only this run's ownership and queue zero-owner cleanup."""
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT * FROM pipeline_runs WHERE pipeline_run_id = ?",
                (pipeline_run_id,),
            ) as cursor:
                run = await cursor.fetchone()
            if run is None:
                raise ValueError("unknown pipeline run")
            if run["state"] == "ROLLED_BACK":
                await db.commit()
                return {"pipeline_run_id": pipeline_run_id, "cleanup_count": 0}
            async with db.execute(
                "SELECT DISTINCT r.document_id, r.revision_id, r.status "
                "FROM memory_mutations m "
                "JOIN document_revisions r ON r.revision_id = m.revision_id "
                "WHERE m.pipeline_run_id = ?",
                (pipeline_run_id,),
            ) as cursor:
                pipeline_revisions = await cursor.fetchall()
            for doc_id, rev_id, rev_status in pipeline_revisions:
                if doc_id and rev_id and rev_status in {"ACTIVE", "SUPERSEDED"}:
                    async with db.execute(
                        "SELECT revision_id FROM document_revisions "
                        "WHERE document_id = ? AND status = 'ACTIVE'",
                        (doc_id,),
                    ) as cursor:
                        active_head = await cursor.fetchone()
                    if rev_status == "SUPERSEDED" or (
                        active_head and active_head[0] != rev_id
                    ):
                        current_head = active_head[0] if active_head else "missing"
                        raise NonHeadRollbackConflictError(
                            "409 NON_HEAD_ROLLBACK_CONFLICT: revision "
                            f"{rev_id} is not current active head {current_head} "
                            f"for document {doc_id}"
                        )
            async with db.execute(
                "SELECT COUNT(*) FROM projection_outbox WHERE state = 'IN_FLIGHT' "
                "AND mutation_id IN (SELECT mutation_id FROM memory_mutations "
                "WHERE pipeline_run_id = ?)",
                (pipeline_run_id,),
            ) as cursor:
                in_flight_row = await cursor.fetchone()
            in_flight_projection_count = int(in_flight_row[0]) if in_flight_row else 0
            transitioned = await self._transition_pipeline_run_in_tx(
                db,
                pipeline_run_id,
                to_state="ROLLING_BACK",
                event_type="ROLLBACK_REQUESTED",
            )
            if not transitioned:
                raise ValueError(f"pipeline cannot rollback from {run['state']}")
            await db.execute(
                "UPDATE artifact_sources SET state = 'RELEASED', "
                "invalidated_at = CURRENT_TIMESTAMP WHERE pipeline_run_id = ? "
                "AND state = 'ACTIVE'",
                (pipeline_run_id,),
            )
            async with db.execute(
                "SELECT DISTINCT r.registry_id, r.store_name, r.artifact_kind, "
                "r.physical_artifact_id FROM artifact_registry r "
                "JOIN artifact_sources owned ON owned.registry_id = r.registry_id "
                "WHERE owned.pipeline_run_id = ? AND NOT EXISTS "
                "(SELECT 1 FROM artifact_sources active "
                "WHERE active.registry_id = r.registry_id AND active.state = 'ACTIVE')",
                (pipeline_run_id,),
            ) as cursor:
                cleanup = [dict(row) for row in await cursor.fetchall()]
            for artifact in cleanup:
                await db.execute(
                    "UPDATE artifact_registry SET state = 'TOMBSTONED', "
                    "invalidated_at = CURRENT_TIMESTAMP WHERE registry_id = ?",
                    (artifact["registry_id"],),
                )
                if (
                    artifact["store_name"] == "SQL"
                    and artifact["artifact_kind"] == "ENTITY"
                ):
                    await db.execute(
                        "UPDATE v4_entities SET status = 'RETRACTED', "
                        "updated_at = CURRENT_TIMESTAMP WHERE entity_id = ?",
                        (artifact["physical_artifact_id"],),
                    )
                elif (
                    artifact["store_name"] == "SQL"
                    and artifact["artifact_kind"] == "ASSERTION"
                ):
                    await db.execute(
                        "UPDATE v4_assertions SET status = 'RETRACTED' "
                        "WHERE assertion_id = ?",
                        (artifact["physical_artifact_id"],),
                    )
                await db.execute(
                    "INSERT OR IGNORE INTO artifact_cleanup_outbox "
                    "(cleanup_id, pipeline_run_id, registry_id) VALUES (?, ?, ?)",
                    (
                        str(
                            uuid.uuid5(
                                _V4_CATALOG_NAMESPACE,
                                f"cleanup:{pipeline_run_id}:{artifact['registry_id']}",
                            )
                        ),
                        pipeline_run_id,
                        artifact["registry_id"],
                    ),
                )
            await self._transition_pipeline_mutations_in_tx(
                db, pipeline_run_id, to_state="ROLLING_BACK"
            )
            await self._restore_pipeline_supersession_in_tx(db, pipeline_run_id)
            await db.execute(
                "UPDATE projection_outbox SET state = 'CANCELLED', claim_token = NULL, claimed_by = NULL, "
                "lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE mutation_id IN (SELECT mutation_id FROM memory_mutations WHERE pipeline_run_id = ?)",
                (pipeline_run_id,),
            )
            if not cleanup and in_flight_projection_count == 0:
                await self._transition_pipeline_run_in_tx(
                    db,
                    pipeline_run_id,
                    to_state="ROLLED_BACK",
                    event_type="ROLLBACK_NO_EXCLUSIVE_ARTIFACTS",
                )
                await self._transition_pipeline_mutations_in_tx(
                    db, pipeline_run_id, to_state="ROLLED_BACK"
                )
            await db.commit()
        return {
            "pipeline_run_id": pipeline_run_id,
            "cleanup_count": len(cleanup),
            "state": (
                "ROLLING_BACK"
                if cleanup or in_flight_projection_count
                else "ROLLED_BACK"
            ),
        }

    async def replay_pipeline_run(
        self,
        pipeline_run_id: str,
        *,
        target_mutation_id: str | None = None,
    ) -> dict[str, Any]:
        """Operator replay for a fenced DLQ/BLOCKED projection or cleanup."""
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT state FROM pipeline_runs WHERE pipeline_run_id = ?",
                (pipeline_run_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise ValueError("unknown pipeline run")
            if row[0] not in {"DLQ", "BLOCKED"}:
                raise ValueError("pipeline run is not replayable")
            async with db.execute(
                "SELECT mutation_id, state FROM memory_mutations "
                "WHERE pipeline_run_id = ?",
                (pipeline_run_id,),
            ) as cursor:
                children = {
                    str(child[0]): str(child[1]) for child in await cursor.fetchall()
                }
            if target_mutation_id is not None and target_mutation_id not in children:
                raise ValueError("target mutation does not belong to pipeline run")
            target_is_rejected = (
                children.get(target_mutation_id) == "REJECTED"
                if target_mutation_id is not None
                else any(state == "REJECTED" for state in children.values())
            )
            if target_is_rejected:
                raise NonReplayableMutationConflictError(
                    "semantic rejection is terminal and non-replayable"
                )
            async with db.execute(
                "SELECT 1 FROM artifact_cleanup_outbox "
                "WHERE pipeline_run_id = ? AND state = 'BLOCKED' LIMIT 1",
                (pipeline_run_id,),
            ) as cleanup_cursor:
                cleanup_replay = await cleanup_cursor.fetchone() is not None
            replay_state = "ROLLING_BACK" if cleanup_replay else "RETRY_PENDING"
            transitioned = await self._transition_pipeline_run_in_tx(
                db,
                pipeline_run_id,
                to_state=replay_state,
                event_type="OPERATOR_REPLAY",
            )
            if not transitioned:
                raise RuntimeError("pipeline replay fence lost")
            await db.execute(
                "UPDATE projection_outbox SET state = 'RETRY_PENDING', "
                "claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL, "
                "last_error_class = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE mutation_id IN (SELECT mutation_id FROM memory_mutations "
                "WHERE pipeline_run_id = ?) AND state = 'DEAD_LETTER'",
                (pipeline_run_id,),
            )
            await db.execute(
                "UPDATE artifact_cleanup_outbox SET state = 'RETRY_PENDING', "
                "claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL, "
                "last_error_class = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE pipeline_run_id = ? AND state = 'BLOCKED'",
                (pipeline_run_id,),
            )
            await self._transition_pipeline_mutations_in_tx(
                db, pipeline_run_id, to_state=replay_state
            )
            if replay_state == "RETRY_PENDING":
                async with db.execute(
                    "SELECT q.queue_record_id, q.payload_reference, q.agent_id "
                    "FROM dispatch_queue q JOIN memory_mutations m "
                    "ON m.raw_log_id = q.payload_reference AND m.agent_id = q.agent_id "
                    "AND m.tenant_id = q.tenant_id "
                    "WHERE m.pipeline_run_id = ? AND m.state = 'RETRY_PENDING' "
                    "AND q.state = 'DEAD_LETTER'",
                    (pipeline_run_id,),
                ) as dispatch_cursor:
                    dispatches = await dispatch_cursor.fetchall()
                for dispatch in dispatches:
                    await db.execute(
                        "UPDATE dispatch_queue SET state = 'RETRY_PENDING', attempt_count = 0, "
                        "claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL, "
                        "last_error_class = NULL WHERE queue_record_id = ? AND state = 'DEAD_LETTER'",
                        (dispatch["queue_record_id"],),
                    )
                    await db.execute(
                        "UPDATE raw_logs SET status = 'DEFERRED', claim_token = NULL, "
                        "claimed_by = NULL, lease_expires_at = NULL WHERE id = ? AND agent_id = ?",
                        (dispatch["payload_reference"], dispatch["agent_id"]),
                    )
            async with db.execute(
                "SELECT state FROM pipeline_runs WHERE pipeline_run_id = ?",
                (pipeline_run_id,),
            ) as state_cursor:
                final_row = await state_cursor.fetchone()
            final_state = str(final_row[0]) if final_row is not None else replay_state
            await db.commit()
        return {"pipeline_run_id": pipeline_run_id, "state": final_state}

    async def claim_artifact_cleanup(
        self, *, worker_id: str, limit: int = 1, lease_seconds: int = 300
    ) -> list[dict[str, Any]]:
        if not worker_id or not 1 <= limit <= 100:
            raise ValueError("invalid cleanup claim")
        token = str(uuid.uuid4())
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT c.cleanup_id FROM artifact_cleanup_outbox c "
                "JOIN artifact_registry r ON r.registry_id = c.registry_id "
                "WHERE c.state IN ('PENDING', 'RETRY_PENDING') "
                "OR (c.state = 'IN_FLIGHT' AND c.lease_expires_at <= CURRENT_TIMESTAMP) "
                "ORDER BY CASE r.artifact_kind WHEN 'ASSERTION' THEN 0 "
                "WHEN 'ENTITY_VECTOR' THEN 1 ELSE 2 END, c.created_at LIMIT ?",
                (limit,),
            ) as cursor:
                identifiers = [row[0] for row in await cursor.fetchall()]
            claimed: list[str] = []
            for cleanup_id in identifiers:
                cursor = await db.execute(
                    "UPDATE artifact_cleanup_outbox SET state = 'IN_FLIGHT', "
                    "claim_token = ?, claimed_by = ?, lease_expires_at = datetime('now', ?), "
                    "attempt_count = attempt_count + 1, updated_at = CURRENT_TIMESTAMP "
                    "WHERE cleanup_id = ? AND (state IN ('PENDING', 'RETRY_PENDING') "
                    "OR (state = 'IN_FLIGHT' AND lease_expires_at <= CURRENT_TIMESTAMP))",
                    (token, worker_id, f"+{lease_seconds} seconds", cleanup_id),
                )
                if cursor.rowcount == 1:
                    claimed.append(cleanup_id)
            if not claimed:
                await db.commit()
                return []
            placeholders = ",".join("?" for _ in claimed)
            async with db.execute(
                "SELECT c.*, r.tenant_id, r.dataset_id, r.store_name, "
                "r.artifact_kind, r.physical_artifact_id, p.agent_id "
                "FROM artifact_cleanup_outbox c "
                "JOIN artifact_registry r ON r.registry_id = c.registry_id "
                "JOIN pipeline_runs p ON p.pipeline_run_id = c.pipeline_run_id "
                f"WHERE c.cleanup_id IN ({placeholders})",
                claimed,
            ) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            await db.commit()
        return rows

    async def apply_artifact_cleanup(self, cleanup: dict[str, Any]) -> None:
        """Idempotently remove one zero-owner physical artifact."""
        store = str(cleanup["store_name"])
        kind = str(cleanup["artifact_kind"])
        artifact_id = str(cleanup["physical_artifact_id"])
        agent_id = str(cleanup["agent_id"])
        if store == "VECTOR":
            async with self._sql.connection() as db:
                async with db.execute(
                    "SELECT 1 FROM v4_assertions WHERE assertion_id = ? AND status != 'RETRACTED' "
                    "UNION ALL "
                    "SELECT 1 FROM v4_entities WHERE entity_id = ? AND status != 'RETRACTED'",
                    (artifact_id, artifact_id),
                ) as cursor:
                    if await cursor.fetchone() is not None:
                        logger.warning(
                            "SAFETY: skipping deletion of active vector artifact %s",
                            artifact_id,
                        )
                        return
            await self._vec.hard_delete(artifact_id, agent_id)
        elif store == "GRAPH":
            graph = self._require_graph()
            if kind == "ASSERTION":
                async with self._sql.connection() as db:
                    async with db.execute(
                        "SELECT 1 FROM v4_assertions WHERE assertion_id = ? AND status != 'RETRACTED'",
                        (artifact_id,),
                    ) as cursor:
                        if await cursor.fetchone() is not None:
                            logger.warning(
                                "SAFETY: skipping deletion of active assertion graph artifact %s",
                                artifact_id,
                            )
                            return
                await graph.delete_assertions(
                    agent_id=agent_id, assertion_ids=[artifact_id]
                )
            elif kind == "ENTITY":
                async with self._sql.connection() as db:
                    async with db.execute(
                        "SELECT 1 FROM v4_entities WHERE entity_id = ? AND status != 'RETRACTED' "
                        "AND EXISTS (SELECT 1 FROM v4_assertions WHERE subject_id = ? OR object_entity_id = ?)",
                        (artifact_id, artifact_id, artifact_id),
                    ) as cursor:
                        if await cursor.fetchone() is not None:
                            logger.warning(
                                "SAFETY: skipping deletion of active entity graph artifact %s",
                                artifact_id,
                            )
                            return
                await graph.delete_nodes(
                    purge_id=f"rollback:{cleanup['cleanup_id']}",
                    agent_id=agent_id,
                    node_ids=[artifact_id],
                )
        elif store == "SQL":
            async with self._sql.transaction() as db:
                if kind == "ASSERTION":
                    await db.execute(
                        "DELETE FROM v4_assertion_links "
                        "WHERE source_assertion_id = ? OR target_assertion_id = ?",
                        (artifact_id, artifact_id),
                    )
                    await db.execute(
                        "DELETE FROM v4_assertions WHERE assertion_id = ?",
                        (artifact_id,),
                    )
                elif kind == "ENTITY":
                    await db.execute(
                        "DELETE FROM entity_aliases WHERE entity_id = ?",
                        (artifact_id,),
                    )
                    await db.execute(
                        "DELETE FROM entity_external_ids WHERE entity_id = ?",
                        (artifact_id,),
                    )
                    await db.execute(
                        "DELETE FROM v4_entities WHERE entity_id = ? "
                        "AND NOT EXISTS (SELECT 1 FROM v4_assertions "
                        "WHERE subject_id = ? OR object_entity_id = ?)",
                        (artifact_id, artifact_id, artifact_id),
                    )
                if sqlite3.sqlite_version_info < (3, 46, 1):
                    # Cleanup can remove entity text after the purge transaction.
                    for fts_table in ("v4_assertions_fts", "v4_entities_fts"):
                        await db.execute(
                            f"INSERT INTO {fts_table}({fts_table}) VALUES('rebuild')"
                        )
                await db.commit()

    async def finish_artifact_cleanup(
        self,
        cleanup_id: str,
        *,
        worker_id: str,
        claim_token: str,
        error_class: str | None = None,
    ) -> bool:
        """Fence cleanup completion; terminal failures block the pipeline."""
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT * FROM artifact_cleanup_outbox WHERE cleanup_id = ?",
                (cleanup_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if (
                row is None
                or row["state"] != "IN_FLIGHT"
                or row["claimed_by"] != worker_id
                or row["claim_token"] != claim_token
            ):
                await db.commit()
                return False
            terminal = bool(error_class) and int(row["attempt_count"]) >= int(
                row["retry_limit"]
            )
            next_state = (
                "BLOCKED"
                if terminal
                else "RETRY_PENDING"
                if error_class
                else "COMPLETED"
            )
            cursor = await db.execute(
                "UPDATE artifact_cleanup_outbox SET state = ?, claim_token = NULL, "
                "claimed_by = NULL, lease_expires_at = NULL, last_error_class = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE cleanup_id = ? AND claim_token = ?",
                (
                    next_state,
                    error_class[:120] if error_class else None,
                    cleanup_id,
                    claim_token,
                ),
            )
            if cursor.rowcount == 1 and not error_class:
                await db.execute(
                    "UPDATE artifact_registry SET state = 'DELETED' "
                    "WHERE registry_id = ?",
                    (row["registry_id"],),
                )
            pipeline_run_id = str(row["pipeline_run_id"])
            if terminal:
                await self._transition_pipeline_run_in_tx(
                    db,
                    pipeline_run_id,
                    to_state="BLOCKED",
                    event_type="ROLLBACK_CLEANUP_BLOCKED",
                    failure_class=error_class,
                )
                await self._transition_pipeline_mutations_in_tx(
                    db,
                    pipeline_run_id,
                    to_state="BLOCKED",
                    failure_class=(error_class or "cleanup_failed")[:120],
                )
            elif not error_class:
                async with db.execute(
                    "SELECT COUNT(*) FROM artifact_cleanup_outbox "
                    "WHERE pipeline_run_id = ? AND state != 'COMPLETED'",
                    (pipeline_run_id,),
                ) as pending_cursor:
                    pending_row = await pending_cursor.fetchone()
                if pending_row is None:
                    raise RuntimeError("cleanup backlog query returned no row")
                pending = int(pending_row[0])
                if pending == 0:
                    await self._transition_pipeline_run_in_tx(
                        db,
                        pipeline_run_id,
                        to_state="ROLLED_BACK",
                        event_type="ROLLBACK_COMPLETED",
                    )
                    await self._transition_pipeline_mutations_in_tx(
                        db, pipeline_run_id, to_state="ROLLED_BACK"
                    )
            await db.commit()
        return cursor.rowcount == 1

    async def reconcile_v4_projection_parity(
        self, *, limit: int = 500, repair: bool = False
    ) -> dict[str, int]:
        """Compare active ledger artifacts with their owning projection store.

        The ledger is the authority: a missing physical artifact is never
        silently ignored.  With ``repair=True`` only its completed lane is
        returned to ``RETRY_PENDING``; the idempotent projector recreates it
        on the next combined-runtime iteration.
        """
        if not 1 <= limit <= 10_000:
            raise ValueError("invalid parity reconciliation limit")
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT a.mutation_id, m.tenant_id, m.agent_id, "
                "a.store_name, a.artifact_kind, a.artifact_id "
                "FROM memory_artifacts a JOIN memory_mutations m ON m.mutation_id = a.mutation_id "
                "WHERE a.state = 'ACTIVE' AND m.state = 'COMMITTED' "
                "ORDER BY a.created_at LIMIT ?",
                (limit,),
            ) as cursor:
                artifacts = [dict(row) for row in await cursor.fetchall()]
        result = {
            "checked_artifacts": len(artifacts),
            "missing_artifacts": 0,
            "missing_sql": 0,
            "missing_vector": 0,
            "missing_graph": 0,
            "requeued_lanes": 0,
        }
        missing: set[tuple[str, str]] = set()
        by_scope: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for artifact in artifacts:
            scope = (str(artifact["tenant_id"]), str(artifact["agent_id"]))
            by_scope.setdefault(scope, []).append(artifact)
        for (tenant_id, agent_id), rows in by_scope.items():
            sql_entities = [
                row["artifact_id"]
                for row in rows
                if row["store_name"] == "SQL" and row["artifact_kind"] == "ENTITY"
            ]
            sql_assertions = [
                row["artifact_id"]
                for row in rows
                if row["store_name"] == "SQL" and row["artifact_kind"] == "ASSERTION"
            ]
            existing_sql: set[str] = set()
            if sql_entities:
                placeholders = ",".join("?" for _ in sql_entities)
                async with self._sql.connection() as db:
                    async with db.execute(
                        f"SELECT entity_id FROM v4_entities WHERE tenant_id = ? "
                        f"AND status = 'ACTIVE' AND entity_id IN ({placeholders})",
                        (tenant_id, *sql_entities),
                    ) as cursor:
                        existing_sql.update(row[0] for row in await cursor.fetchall())
            if sql_assertions:
                placeholders = ",".join("?" for _ in sql_assertions)
                async with self._sql.connection() as db:
                    async with db.execute(
                        f"SELECT assertion_id FROM v4_assertions WHERE tenant_id = ? "
                        f"AND status = 'ACTIVE' AND assertion_id IN ({placeholders})",
                        (tenant_id, *sql_assertions),
                    ) as cursor:
                        existing_sql.update(row[0] for row in await cursor.fetchall())
            for row in rows:
                if (
                    row["store_name"] == "SQL"
                    and row["artifact_id"] not in existing_sql
                ):
                    missing.add((row["mutation_id"], "SQL"))
            vector_ids = [
                row["artifact_id"] for row in rows if row["store_name"] == "VECTOR"
            ]
            if vector_ids:
                existing_vector = await self._vec.get_existing_node_ids(
                    agent_id, vector_ids
                )
                for row in rows:
                    if (
                        row["store_name"] == "VECTOR"
                        and row["artifact_id"] not in existing_vector
                    ):
                        missing.add((row["mutation_id"], "VECTOR"))
            if self._graph is not None:
                # An Assertion is linked to both Graph V2 Entity endpoints;
                # its existence is therefore the graph lane's compact parity
                # proof.  Checking Entity artifacts separately would report a
                # false mismatch for a duplicate canonical entity owned by a
                # different assertion/mutation.
                graph_rows = [
                    row
                    for row in rows
                    if row["store_name"] == "GRAPH"
                    and row["artifact_kind"] == "ASSERTION"
                ]
                if graph_rows:
                    graph_ids = [
                        self._graph._composite_id(agent_id, row["artifact_id"])
                        for row in graph_rows
                    ]
                    found_rows = await self._graph.execute_query(
                        "MATCH (n:Assertion {agent_id: $agent_id}) WHERE n.id IN $ids RETURN n.id",
                        {"agent_id": agent_id, "ids": graph_ids},
                    )
                    # KuzuGraphProvider normalizes tenant composite IDs back
                    # to their artifact IDs for every agent-scoped read.
                    existing_graph = {row[0] for row in found_rows}
                    for row in graph_rows:
                        if row["artifact_id"] not in existing_graph:
                            missing.add((row["mutation_id"], "GRAPH"))
        result["missing_artifacts"] = len(missing)
        for _mutation_id, lane in missing:
            result[f"missing_{lane.lower()}"] += 1
        if repair and missing:
            lanes_by_mutation: dict[str, set[str]] = {}
            for mutation_id, lane in missing:
                lanes_by_mutation.setdefault(mutation_id, set()).add(lane)
            async with self._sql.transaction() as db:
                for mutation_id, lanes in sorted(lanes_by_mutation.items()):
                    async with db.execute(
                        "SELECT m.state, pr.state, d.status, m.pipeline_run_id "
                        "FROM memory_mutations m "
                        "LEFT JOIN pipeline_runs pr ON pr.pipeline_run_id = m.pipeline_run_id "
                        "LEFT JOIN documents d ON d.document_id = m.document_id "
                        "WHERE m.mutation_id = ?",
                        (mutation_id,),
                    ) as state_cursor:
                        state_row = await state_cursor.fetchone()
                    if (
                        state_row is None
                        or state_row[0] != "COMMITTED"
                        or state_row[1] != "COMMITTED"
                        or state_row[2] == "PURGED"
                    ):
                        continue
                    requeued = 0
                    for lane in sorted(lanes):
                        cursor = await db.execute(
                            "UPDATE projection_outbox SET state = 'RETRY_PENDING', claim_token = NULL, "
                            "claimed_by = NULL, lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                            "WHERE mutation_id = ? AND projection_name = ? AND state = 'COMPLETED'",
                            (mutation_id, lane),
                        )
                        requeued += cursor.rowcount
                    result["requeued_lanes"] += requeued
                    if requeued:
                        mutation_changed = await self._transition_memory_mutation_in_tx(
                            db,
                            mutation_id,
                            to_state="RETRY_PENDING",
                            failure_class="ProjectionParityMismatch",
                        )
                        pipeline_changed = await self._transition_pipeline_run_in_tx(
                            db,
                            str(state_row[3]),
                            to_state="RETRY_PENDING",
                            event_type="PROJECTION_PARITY_MISMATCH",
                            failure_class="ProjectionParityMismatch",
                        )
                        if not mutation_changed or not pipeline_changed:
                            raise RuntimeError("projection parity repair fence lost")
                await db.commit()
        return result

    async def reconcile_v4_bidirectional(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        dataset_ids: list[str],
        repair: bool = False,
    ) -> dict[str, int]:
        """Detect physical artifacts with no canonical ownership registry."""
        _assert_valid_agent_id(agent_id)
        if not tenant_id or not dataset_ids:
            raise ValueError("reconciliation requires tenant and dataset scope")
        async with self._sql.connection() as db:
            resolved_datasets = [
                await self._catalog.resolve_id_in_tx(
                    db,
                    tenant_id=tenant_id,
                    kind="dataset",
                    external_id=ds,
                )
                for ds in dataset_ids
            ]
            async with db.execute(
                "SELECT store_name, artifact_kind, physical_artifact_id "
                "FROM artifact_registry WHERE tenant_id = ? "
                "AND agent_id IN ('__tenant__', ?) AND state != 'DELETED'",
                (tenant_id, agent_id),
            ) as cursor:
                registered = {
                    (str(row[0]), str(row[1]), str(row[2]))
                    for row in await cursor.fetchall()
                }
            async with db.execute(
                "SELECT entity_id FROM v4_entities WHERE tenant_id = ? "
                "AND status != 'RETRACTED'",
                (tenant_id,),
            ) as cursor:
                sql_entities = {str(row[0]) for row in await cursor.fetchall()}
            placeholders = ",".join("?" for _ in resolved_datasets)
            async with db.execute(
                f"SELECT assertion_id FROM v4_assertions WHERE tenant_id = ? "
                f"AND dataset_id IN ({placeholders}) AND status != 'RETRACTED'",
                (tenant_id, *resolved_datasets),
            ) as cursor:
                sql_assertions = {str(row[0]) for row in await cursor.fetchall()}

            # Active assertions in other datasets of this tenant
            async with db.execute(
                f"SELECT assertion_id FROM v4_assertions WHERE tenant_id = ? "
                f"AND dataset_id NOT IN ({placeholders}) AND status != 'RETRACTED'",
                (tenant_id, *resolved_datasets),
            ) as cursor:
                other_dataset_assertions = {
                    str(row[0]) for row in await cursor.fetchall()
                }

            # Entities referenced by other datasets of this tenant
            other_dataset_params = (
                tenant_id,
                *resolved_datasets,
                tenant_id,
                *resolved_datasets,
            )
            async with db.execute(
                f"SELECT DISTINCT subject_id FROM v4_assertions WHERE tenant_id = ? "
                f"AND dataset_id NOT IN ({placeholders}) AND status != 'RETRACTED' "
                f"UNION "
                f"SELECT DISTINCT object_entity_id FROM v4_assertions WHERE tenant_id = ? "
                f"AND dataset_id NOT IN ({placeholders}) AND object_entity_id IS NOT NULL AND status != 'RETRACTED'",
                other_dataset_params,
            ) as cursor:
                other_dataset_entities = {
                    str(row[0]) for row in await cursor.fetchall()
                }

            # Artifacts registered under other datasets
            async with db.execute(
                f"SELECT store_name, artifact_kind, physical_artifact_id FROM artifact_registry "
                f"WHERE tenant_id = ? AND dataset_id NOT IN ({placeholders}) AND dataset_id IS NOT NULL AND state != 'DELETED'",
                (tenant_id, *resolved_datasets),
            ) as cursor:
                other_registered = {
                    (str(row[0]), str(row[1]), str(row[2]))
                    for row in await cursor.fetchall()
                }

        # Exclude entities owned/referenced by other datasets
        candidate_entities = {
            e for e in sql_entities if e not in other_dataset_entities
        }

        vector_entities = set(await self._vec.get_active_node_ids(agent_id))
        # Exclude vectors belonging to other datasets
        vector_entities = {
            item
            for item in vector_entities
            if item not in other_dataset_assertions
            and ("VECTOR", "ASSERTION_VECTOR", item) not in other_registered
            and ("VECTOR", "ENTITY_VECTOR", item) not in other_registered
        }

        graph_entities: set[str] = set()
        graph_assertions: set[str] = set()
        if self._graph is not None:
            raw_graph_entities = {
                str(row[0])
                for row in await self._graph.execute_query(
                    "MATCH (n:Entity {agent_id: $agent_id}) RETURN n.id",
                    {"agent_id": agent_id},
                )
            }
            graph_entities = {
                e
                for e in raw_graph_entities
                if e not in other_dataset_entities
                and ("GRAPH", "ENTITY", e) not in other_registered
            }
            raw_graph_assertions = {
                str(row[0])
                for row in await self._graph.execute_query(
                    "MATCH (n:Assertion {agent_id: $agent_id}) RETURN n.id",
                    {"agent_id": agent_id},
                )
            }
            graph_assertions = {
                a
                for a in raw_graph_assertions
                if a not in other_dataset_assertions
                and ("GRAPH", "ASSERTION", a) not in other_registered
            }
        physical = {
            *(("SQL", "ENTITY", item) for item in candidate_entities),
            *(("SQL", "ASSERTION", item) for item in sql_assertions),
            *(
                (
                    "VECTOR",
                    "ASSERTION_VECTOR" if item in sql_assertions else "ENTITY_VECTOR",
                    item,
                )
                for item in vector_entities
            ),
            *(("GRAPH", "ENTITY", item) for item in graph_entities),
            *(("GRAPH", "ASSERTION", item) for item in graph_assertions),
        }
        orphans = sorted(physical - registered)
        enqueued = 0
        if repair and orphans:
            pipeline_run_id = str(uuid.uuid4())
            async with self._sql.transaction() as db:
                await db.execute(
                    "INSERT INTO pipeline_runs "
                    "(pipeline_run_id, tenant_id, workspace_id, dataset_id, "
                    "session_id, agent_id, state) "
                    "VALUES (?, ?, NULL, ?, 'reconciliation', ?, 'ROLLING_BACK')",
                    (
                        pipeline_run_id,
                        tenant_id,
                        resolved_datasets[0] if len(resolved_datasets) == 1 else None,
                        agent_id,
                    ),
                )
                for store, kind, physical_id in orphans:
                    registry_id = str(
                        uuid.uuid5(
                            _V4_CATALOG_NAMESPACE,
                            f"orphan:{tenant_id}:{agent_id}:{store}:{kind}:{physical_id}",
                        )
                    )
                    await db.execute(
                        "INSERT OR IGNORE INTO artifact_registry "
                        "(registry_id, tenant_id, agent_id, dataset_id, store_name, "
                        "artifact_kind, physical_artifact_id, state) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 'TOMBSTONED')",
                        (
                            registry_id,
                            tenant_id,
                            "__tenant__" if store == "SQL" else agent_id,
                            (
                                resolved_datasets[0]
                                if len(resolved_datasets) == 1
                                else None
                            ),
                            store,
                            kind,
                            physical_id,
                        ),
                    )
                    cursor = await db.execute(
                        "INSERT OR IGNORE INTO artifact_cleanup_outbox "
                        "(cleanup_id, pipeline_run_id, registry_id) VALUES (?, ?, ?)",
                        (
                            str(
                                uuid.uuid5(
                                    _V4_CATALOG_NAMESPACE,
                                    f"cleanup:{pipeline_run_id}:{registry_id}",
                                )
                            ),
                            pipeline_run_id,
                            registry_id,
                        ),
                    )
                    enqueued += cursor.rowcount
                await db.commit()
        return {
            "physical_artifacts": len(physical),
            "registered_artifacts": len(registered),
            "physical_orphans": len(orphans),
            "cleanup_enqueued": enqueued,
        }

    async def _project_v4_sql_entity_in_tx(
        self,
        db: Any,
        *,
        mutation: dict[str, Any],
        entity_name: str,
        resolved_entities: dict[tuple[str, str], str] | None = None,
        owner_cache: dict[str, Any] | None = None,
    ) -> str:
        agent_id = str(mutation["agent_id"])
        _assert_valid_agent_id(agent_id)
        tenant_id = str(mutation["tenant_id"])
        entity_name = LegalEntityResolver().canonicalize_entity(entity_name)
        cache_key = (tenant_id, entity_name)
        node_id = (
            resolved_entities.get(cache_key)
            if resolved_entities is not None
            else None
        )
        if node_id is None:
            entity = await self._resolve_v4_entity_in_tx(
                db,
                tenant_id=tenant_id,
                canonical_name=entity_name,
            )
            node_id = str(entity["entity_id"])
            if resolved_entities is not None:
                resolved_entities[cache_key] = node_id
        await self._record_mutation_artifact_in_tx(
            db,
            str(mutation["mutation_id"]),
            store_name="SQL",
            artifact_kind="ENTITY",
            artifact_id=node_id,
            metadata={
                "entity_name": entity_name,
                "dataset_id": mutation.get("dataset_id"),
            },
            owner_cache=owner_cache,
        )
        return node_id

    async def project_v4_sql_entity(
        self, *, mutation: dict[str, Any], entity_name: str
    ) -> str:
        """Apply only the SQLite Entity projection, idempotently."""
        agent_id = str(mutation["agent_id"])
        _assert_valid_agent_id(agent_id)
        async with self._sql.transaction() as db:
            node_id = await self._project_v4_sql_entity_in_tx(
                db,
                mutation=mutation,
                entity_name=entity_name,
            )
            await db.commit()
        return node_id

    async def project_v4_vector_entity(
        self, *, mutation: dict[str, Any], entity_name: str
    ) -> str:
        """Legacy/auxiliary entity vector projection.

        Canonical V4 memory retrieval projects assertions with
        :meth:`project_v4_vector_assertion` instead.
        """
        node_id = self.v4_entity_id(str(mutation["tenant_id"]), entity_name)
        return await self._project_v4_vector_payload(
            mutation=mutation,
            vector_id=node_id,
            payload_text=entity_name,
            artifact_kind="ENTITY_VECTOR",
        )

    async def project_v4_vector_assertion(
        self, *, mutation: dict[str, Any], assertion: dict[str, Any]
    ) -> str:
        """Embed canonical assertion text for supported V4 memory retrieval."""
        subject = str(assertion.get("head") or "").strip()
        predicate = str(assertion.get("predicate") or "").strip()
        object_value = (
            str(assertion["tail"]).strip()
            if assertion.get("tail") is not None
            else str(assertion.get("literal_value") or "").strip()
        )
        evidence_span = str(assertion.get("evidence_span") or "").strip()
        payload_text = build_v4_assertion_vector_payload(
            subject=subject,
            predicate=predicate,
            object_value=object_value,
            evidence_span=evidence_span,
        )
        if not payload_text:
            raise ValueError("assertion vector representation is empty")

        return await self._project_v4_vector_payload(
            mutation=mutation,
            vector_id=str(assertion["assertion_id"]),
            payload_text=payload_text,
            artifact_kind="ASSERTION_VECTOR",
        )

    async def _project_v4_vector_payload(
        self,
        *,
        mutation: dict[str, Any],
        vector_id: str,
        payload_text: str,
        artifact_kind: str,
    ) -> str:
        """Project one explicitly selected semantic payload through EmbeddingService."""
        agent_id = str(mutation["agent_id"])
        _assert_valid_agent_id(agent_id)
        # This is deliberately checked immediately before the non-transactional
        # write.  ``record_mutation_artifact`` below is the post-write receipt
        # fence; if it loses, its caller compensates the physical write.
        await self._assert_v4_projection_write_allowed(str(mutation["mutation_id"]))
        node_id = vector_id
        producer_identity = getattr(self._vec, "embedding_identity", None)
        if producer_identity is None and isinstance(self._vec, VectorEngine):
            raise ValueError("canonical vector projection requires producer identity")
        expected_identity = {
            "provider": str(mutation.get("embedding_provider") or ""),
            "model": str(mutation.get("embedding_model") or ""),
            "version": str(mutation.get("embedding_version") or ""),
            "dimension": int(mutation.get("embedding_dimension") or 0),
        }
        snapshot = mutation.get("embedding_identity_snapshot")
        if not isinstance(snapshot, dict):
            # Projection callers may carry only the admission response rather
            # than a fully hydrated work record.  The identity authority is
            # still the durable admission snapshot, never a fresh runtime
            # configuration or an inferred fallback.
            persisted = await self.get_projection_mutation(str(mutation["mutation_id"]))
            snapshot = (
                persisted.get("embedding_identity_snapshot")
                if persisted is not None
                else None
            )
        if not isinstance(snapshot, dict):
            raise ValueError("durable embedding identity snapshot is required")
        required_snapshot_fields = {
            "embedding_space_id",
            "provider",
            "model",
            "model_revision",
            "version",
            "dimension",
            "normalized",
        }
        if not required_snapshot_fields.issubset(snapshot):
            raise ValueError("durable embedding identity snapshot is incomplete")
        if producer_identity is None:
            # Existing deterministic test doubles predate EmbeddingService.
            # They are not constructible in production composition; bind them
            # explicitly to the mutation identity rather than inventing a
            # fallback provider.
            actual_identity = dict(expected_identity)
            revision = expected_identity["version"]
            producer_identity_metadata = {
                "embedding_space_id": (
                    f"{expected_identity['provider']}:{expected_identity['model']}:"
                    f"{revision}:{expected_identity['dimension']}:norm=true"
                ),
                **expected_identity,
                "normalized": True,
                "model_revision": None,
            }
        else:
            actual_identity = {
                "provider": str(producer_identity.provider),
                "model": str(producer_identity.model),
                "version": str(producer_identity.version),
                "dimension": int(producer_identity.dimension),
            }
            if hasattr(producer_identity, "as_dict"):
                producer_identity_metadata = producer_identity.as_dict()
            else:
                producer_identity_metadata = {
                    "embedding_space_id": getattr(
                        producer_identity, "embedding_space_id", ""
                    ),
                    "provider": getattr(producer_identity, "provider", ""),
                    "model": getattr(producer_identity, "model", ""),
                    "version": getattr(producer_identity, "version", ""),
                    "dimension": getattr(producer_identity, "dimension", 0),
                    "normalized": getattr(producer_identity, "normalized", False),
                    "model_revision": getattr(
                        producer_identity, "model_revision", None
                    ),
                }
        if expected_identity["dimension"] != actual_identity["dimension"]:
            raise ValueError(
                "embedding dimension mismatch: expected "
                f"{expected_identity['dimension']}, got {actual_identity['dimension']}"
            )
        if expected_identity != actual_identity:
            raise ValueError(
                "embedding identity mismatch: mutation identity does not match "
                "the canonical vector producer"
            )
        snapshot_identity = {
            key: snapshot[key]
            for key in (
                "embedding_space_id",
                "provider",
                "model",
                "model_revision",
                "version",
                "dimension",
                "normalized",
            )
        }
        actual_snapshot_identity = {
            key: producer_identity_metadata[key] for key in snapshot_identity
        }
        if snapshot_identity != actual_snapshot_identity:
            raise ValueError(
                "embedding space identity mismatch: re-embedding migration is required"
            )
        embedding = await self._vec.compute_embedding(payload_text)
        expected_dim = mutation.get("embedding_dimension")
        if expected_dim is not None and int(expected_dim) != len(embedding):
            raise ValueError(
                f"embedding dimension mismatch: expected {expected_dim}, got {len(embedding)}"
            )
        await self._vec.upsert(
            node_id=node_id,
            agent_id=agent_id,
            embedding=embedding,
            content_hash=hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
        )
        try:
            await self.record_mutation_artifact(
                str(mutation["mutation_id"]),
                store_name="VECTOR",
                artifact_kind=artifact_kind,
                artifact_id=node_id,
                metadata={
                    **producer_identity_metadata,
                    "embedding_dimension": len(embedding),
                    "representation_version": (
                        V4_VECTOR_REPRESENTATION_VERSION
                        if artifact_kind == "ASSERTION_VECTOR"
                        else "entity-v1"
                    ),
                },
            )
        except Exception as exc:
            try:
                await self._vec.hard_delete(node_id, agent_id)
            except Exception:
                await self._enqueue_unowned_projection_cleanup(
                    str(mutation["mutation_id"]),
                    [("VECTOR", artifact_kind, node_id)],
                )
            else:
                await self._finalize_terminal_projection_compensation(
                    str(mutation["mutation_id"])
                )
            raise exc
        return node_id

    async def _assert_v4_projection_write_allowed_in_db(
        self, db: Any, mutation_id: str
    ) -> None:
        async with db.execute(
            "SELECT m.state AS mutation_state, pr.state AS pipeline_state, "
            "d.status AS document_status FROM memory_mutations m "
            "LEFT JOIN pipeline_runs pr ON pr.pipeline_run_id = m.pipeline_run_id "
            "LEFT JOIN documents d ON d.document_id = m.document_id "
            "WHERE m.mutation_id = ?",
            (mutation_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise ValueError("unknown projection mutation")
        terminal = {
            "ROLLING_BACK",
            "ROLLED_BACK",
            "PURGING",
            "PURGED",
            "CANCELLED",
            "REJECTED",
        }
        if (
            row["mutation_state"] in terminal
            or row["pipeline_state"] in terminal
            or row["document_status"] == "PURGED"
        ):
            raise ValueError("projection mutation is fenced or terminal")

    async def _assert_v4_projection_write_allowed(self, mutation_id: str) -> None:
        """Fail closed before a V4 projector touches a secondary store.

        SQLite cannot make a vector/graph write atomic with the lifecycle
        ledger.  The paired post-write receipt in ``record_mutation_artifact``
        handles a race after this check, but skipping the pre-check would still
        make already-terminal work create needless physical side effects.
        """
        async with self._sql.connection() as db:
            await self._assert_v4_projection_write_allowed_in_db(db, mutation_id)

    async def _enqueue_unowned_projection_cleanup(
        self,
        mutation_id: str,
        artifacts: list[tuple[str, str, str]],
    ) -> None:
        """Durably retain failed immediate compensation without false success."""
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT tenant_id, agent_id, dataset_id, pipeline_run_id "
                "FROM memory_mutations WHERE mutation_id = ?",
                (mutation_id,),
            ) as cursor:
                mutation = await cursor.fetchone()
            if mutation is None or not mutation[3]:
                raise RuntimeError("unowned projection cleanup lacks pipeline scope")
            pipeline_run_id = str(mutation[3])
            # This is cleanup-only reopening: terminal work remains fenced and
            # can never return to a forward projection state.
            await db.execute(
                "UPDATE pipeline_runs SET state = 'ROLLING_BACK', "
                "failure_class = 'PhysicalCompensationPending', "
                "updated_at = CURRENT_TIMESTAMP WHERE pipeline_run_id = ? "
                "AND state IN ('ROLLED_BACK', 'ROLLING_BACK')",
                (pipeline_run_id,),
            )
            await db.execute(
                "UPDATE memory_mutations SET state = 'ROLLING_BACK', "
                "failure_class = 'PhysicalCompensationPending', "
                "updated_at = CURRENT_TIMESTAMP WHERE mutation_id = ? "
                "AND state IN ('ROLLED_BACK', 'ROLLING_BACK')",
                (mutation_id,),
            )
            for store_name, artifact_kind, artifact_id in artifacts:
                registry_agent_id = (
                    "__tenant__" if store_name == "SQL" else str(mutation[1])
                )
                registry_id = str(
                    uuid.uuid5(
                        _V4_CATALOG_NAMESPACE,
                        "\x1f".join(
                            (
                                str(mutation[0]),
                                registry_agent_id,
                                store_name,
                                artifact_kind,
                                artifact_id,
                            )
                        ),
                    )
                )
                await db.execute(
                    "INSERT OR IGNORE INTO artifact_registry "
                    "(registry_id, tenant_id, agent_id, dataset_id, store_name, "
                    "artifact_kind, physical_artifact_id, state) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'TOMBSTONED')",
                    (
                        registry_id,
                        mutation[0],
                        registry_agent_id,
                        mutation[2],
                        store_name,
                        artifact_kind,
                        artifact_id,
                    ),
                )
                await db.execute(
                    "UPDATE artifact_registry SET state = 'TOMBSTONED', "
                    "invalidated_at = CURRENT_TIMESTAMP WHERE registry_id = ?",
                    (registry_id,),
                )
                cleanup_id = str(
                    uuid.uuid5(
                        _V4_CATALOG_NAMESPACE,
                        f"cleanup:{pipeline_run_id}:{registry_id}",
                    )
                )
                await db.execute(
                    "INSERT OR IGNORE INTO artifact_cleanup_outbox "
                    "(cleanup_id, pipeline_run_id, registry_id) VALUES (?, ?, ?)",
                    (cleanup_id, pipeline_run_id, registry_id),
                )
            await db.commit()

    async def _finalize_terminal_projection_compensation(
        self, mutation_id: str
    ) -> None:
        """Finish rollback only when no physical cleanup remains outstanding."""
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT pipeline_run_id FROM memory_mutations WHERE mutation_id = ?",
                (mutation_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or not row[0]:
                await db.commit()
                return
            pipeline_run_id = str(row[0])
            async with db.execute(
                "SELECT COUNT(*) FROM artifact_cleanup_outbox WHERE pipeline_run_id = ? "
                "AND state != 'COMPLETED'",
                (pipeline_run_id,),
            ) as cursor:
                pending_row = await cursor.fetchone()
            if pending_row and int(pending_row[0]) == 0:
                await self._transition_pipeline_run_in_tx(
                    db,
                    pipeline_run_id,
                    to_state="ROLLED_BACK",
                    event_type="POST_WRITE_COMPENSATION_COMPLETED",
                )
                await self._transition_pipeline_mutations_in_tx(
                    db, pipeline_run_id, to_state="ROLLED_BACK"
                )
            await db.commit()

    async def _project_v4_sql_assertion_in_tx(
        self,
        db: Any,
        *,
        mutation: dict[str, Any],
        triplet: dict[str, Any],
        resolved_entities: dict[tuple[str, str], str] | None = None,
        owner_cache: dict[str, Any] | None = None,
    ) -> str:
        agent_id = str(mutation["agent_id"])
        _assert_valid_agent_id(agent_id)
        tenant_id = str(mutation["tenant_id"])
        legal_resolver = LegalEntityResolver()
        head = legal_resolver.canonicalize_entity(str(triplet["head"]))
        raw_tail = str(triplet["tail"]) if triplet.get("tail") is not None else None
        raw_literal = (
            str(triplet["literal_value"])
            if triplet.get("literal_value") is not None
            else None
        )
        explicit_type = (
            str(triplet.get("object_type") or triplet.get("tail_type") or "")
            if (triplet.get("object_type") or triplet.get("tail_type"))
            else None
        )
        tail, literal_value, object_type = classify_graph_object(
            tail=raw_tail, literal_value=raw_literal, object_type=explicit_type
        )
        if tail is not None:
            tail = legal_resolver.canonicalize_entity(tail)
        if (tail is None) == (literal_value is None):
            raise ValueError("assertion requires one entity or literal object")
        relation = str(triplet["relation"])
        evidence_span = str(
            triplet.get("evidence_span")
            or triplet.get("source_span")
            or mutation.get("evidence_span")
            or ""
        )
        mutation_metadata = mutation.get("metadata") or {}
        fact_metadata = triplet.get("metadata") or {}
        jurisdiction = str(
            fact_metadata.get("jurisdiction")
            or mutation_metadata.get("jurisdiction")
            or ""
        )
        authority_level = str(
            fact_metadata.get("authority_level")
            or mutation_metadata.get("authority_level")
            or ""
        )
        valid_from = str(
            triplet.get("valid_from") or mutation_metadata.get("valid_from") or ""
        )
        valid_to = str(
            triplet.get("valid_to") or mutation_metadata.get("valid_to") or ""
        )
        observed_at = str(
            fact_metadata.get("observed_at")
            or mutation_metadata.get("observed_at")
            or ""
        )
        legal_identity = canonicalize_trusted_legal_identity(
            mutation.get("legal_identity")
        )
        subject_id = self.v4_entity_id(tenant_id, head)
        object_id = self.v4_entity_id(tenant_id, tail) if tail is not None else None
        mutation_id = str(mutation["mutation_id"])

        if owner_cache is None or mutation_id not in owner_cache:
            await self._assert_v4_projection_write_allowed_in_db(db, mutation_id)
        for entity_name in (head, tail):
            if entity_name is None:
                continue
            cache_key = (tenant_id, entity_name)
            if resolved_entities is None or cache_key not in resolved_entities:
                ent = await self._resolve_v4_entity_in_tx(
                    db, tenant_id=tenant_id, canonical_name=entity_name
                )
                if resolved_entities is not None:
                    resolved_entities[cache_key] = str(ent["entity_id"])

        superseded_assertion_ids: list[str] = []
        async with db.execute(
            "SELECT tenant_id, dataset_id, document_id, revision_id, chunk_id, "
            "source_ref, evidence_span, pipeline_run_id FROM memory_mutations "
            "WHERE mutation_id = ?",
            (mutation_id,),
        ) as m_cur:
            m_row = await m_cur.fetchone()
        if m_row is not None:
            p_dataset_id = str(m_row[1])
            p_doc_id = str(m_row[2])
            p_rev_id = str(m_row[3])
            p_chunk_id = str(m_row[4])
            source_ref = str(m_row[5] or mutation.get("source_ref") or "")
            pipeline_run_id = str(m_row[7] or mutation.get("pipeline_run_id") or "")
        else:
            p_dataset_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="dataset",
                external_id=str(mutation["dataset_id"]),
                create=True,
            )
            p_doc_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="document",
                external_id=str(mutation["document_id"]),
                create=True,
            )
            p_rev_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="revision",
                external_id=str(mutation["revision_id"]),
                create=True,
            )
            p_chunk_id = await self._catalog.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="chunk",
                external_id=str(mutation["chunk_id"]),
                create=True,
            )
            source_ref = str(mutation.get("source_ref") or "")
            pipeline_run_id = str(mutation.get("pipeline_run_id") or "")

        assertion_id = self.v4_assertion_id(
            tenant_id=tenant_id,
            dataset_id=p_dataset_id,
            revision_id=p_rev_id,
            chunk_id=p_chunk_id,
            subject_id=subject_id,
            predicate=relation,
            object_entity_id=object_id,
            literal_value=literal_value,
            evidence_span=evidence_span,
        )
        await db.execute(
            "INSERT OR IGNORE INTO v4_assertions "
            "(assertion_id, tenant_id, dataset_id, subject_id, predicate, "
            "object_entity_id, literal_value, source_ref, document_id, revision_id, chunk_id, "
            "evidence_span, jurisdiction, authority_level, valid_from, valid_to, "
            "observed_at, confidence, object_type, representation_version, "
            "law_id, statute_code, article_id, paragraph_id, clause_id, "
            "legal_version_id, legal_identity_source, "
            "status, mutation_id, pipeline_run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (
                assertion_id,
                tenant_id,
                p_dataset_id,
                subject_id,
                relation,
                object_id,
                literal_value,
                source_ref,
                p_doc_id,
                p_rev_id,
                p_chunk_id,
                evidence_span,
                jurisdiction,
                authority_level,
                valid_from,
                valid_to,
                observed_at,
                float(triplet.get("confidence", 1.0)),
                object_type,
                V4_ASSERTION_REPRESENTATION_VERSION,
                str((legal_identity or {}).get("law_id") or ""),
                str((legal_identity or {}).get("statute_code") or ""),
                str((legal_identity or {}).get("article_id") or ""),
                str((legal_identity or {}).get("paragraph_id") or ""),
                str((legal_identity or {}).get("clause_id") or ""),
                str((legal_identity or {}).get("version_id") or ""),
                str((legal_identity or {}).get("identity_source") or ""),
                mutation["mutation_id"],
                pipeline_run_id,
            ),
        )
        async with db.execute(
            "SELECT supersedes_revision_id FROM document_revisions "
            "WHERE revision_id = ?",
            (p_rev_id,),
        ) as cursor:
            revision = await cursor.fetchone()
        if revision and revision[0]:
            async with db.execute(
                "SELECT assertion_id FROM v4_assertions "
                "WHERE tenant_id = ? AND revision_id = ? "
                "AND subject_id = ? AND predicate = ? "
                "AND status IN ('ACTIVE', 'SUPERSEDED')",
                (tenant_id, revision[0], subject_id, relation),
            ) as cursor:
                superseded_assertion_ids = [
                    str(row[0]) for row in await cursor.fetchall()
                ]
        elif triplet.get("supersedes"):
            async with db.execute(
                "SELECT assertion_id FROM v4_assertions "
                "LEFT JOIN v4_entities object_entity "
                "ON object_entity.entity_id = v4_assertions.object_entity_id "
                "WHERE v4_assertions.tenant_id = ? AND v4_assertions.dataset_id = ? "
                "AND v4_assertions.subject_id = ? AND v4_assertions.predicate = ? "
                "AND v4_assertions.assertion_id != ? "
                "AND v4_assertions.status = 'ACTIVE' "
                "AND lower(trim(COALESCE(object_entity.canonical_name, "
                "v4_assertions.literal_value, ''))) = lower(trim(?))",
                (
                    tenant_id,
                    mutation["dataset_id"],
                    subject_id,
                    relation,
                    assertion_id,
                    str(triplet["supersedes"]),
                ),
            ) as cursor:
                superseded_assertion_ids = [
                    str(row[0]) for row in await cursor.fetchall()
                ]
        if superseded_assertion_ids:
            for old_assertion_id in superseded_assertion_ids:
                await db.execute(
                    "INSERT OR IGNORE INTO v4_assertion_links "
                    "(source_assertion_id, target_assertion_id, relation_type, mutation_id) "
                    "VALUES (?, ?, 'SUPERSEDES', ?)",
                    (
                        assertion_id,
                        old_assertion_id,
                        mutation["mutation_id"],
                    ),
                )
        await self._record_mutation_artifact_in_tx(
            db,
            mutation_id,
            store_name="SQL",
            artifact_kind="ASSERTION",
            artifact_id=assertion_id,
            metadata={
                "predicate": relation,
                "fact_text": str(triplet.get("fact_text") or ""),
                "valid_from": valid_from,
                "valid_to": valid_to,
                "source_span": evidence_span,
                "supersedes": triplet.get("supersedes"),
            },
            owner_cache=owner_cache,
        )
        return assertion_id

    async def project_v4_sql_assertion(
        self, *, mutation: dict[str, Any], triplet: dict[str, Any]
    ) -> str:
        """Persist one canonical assertion after the SQL entity projection.

        This method intentionally has no graph dependency.  Kuzu is a later,
        derived projection and must never decide whether canonical fact truth
        becomes durable.
        """
        resolved_entities: dict[tuple[str, str], str] = {}
        owner_cache: dict[str, Any] = {}
        async with self._sql.transaction() as db:
            assertion_id = await self._project_v4_sql_assertion_in_tx(
                db,
                mutation=mutation,
                triplet=triplet,
                resolved_entities=resolved_entities,
                owner_cache=owner_cache,
            )
            await db.commit()
        return assertion_id

    async def apply_v4_sql_projections_batch(
        self,
        items: list[tuple[dict[str, Any], list[str], list[dict[str, Any]]]],
    ) -> None:
        """Apply a batch of SQL entity and assertion projections in one tx."""
        if not items:
            return
        resolved_entities: dict[tuple[str, str], str] = {}
        owner_cache: dict[str, Any] = {}
        async with self._sql.transaction() as db:
            for mutation, entities, triplets in items:
                for entity_name in entities:
                    await self._project_v4_sql_entity_in_tx(
                        db,
                        mutation=mutation,
                        entity_name=entity_name,
                        resolved_entities=resolved_entities,
                        owner_cache=owner_cache,
                    )
                for triplet in triplets:
                    await self._project_v4_sql_assertion_in_tx(
                        db,
                        mutation=mutation,
                        triplet=triplet,
                        resolved_entities=resolved_entities,
                        owner_cache=owner_cache,
                    )
            await db.commit()

    @staticmethod
    def _unpack_v4_assertion_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        meta_raw = item.pop("artifact_meta", None)
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
                if isinstance(meta, dict) and meta.get("fact_text"):
                    item["fact_text"] = meta["fact_text"]
            except Exception:
                pass
        return item

    async def list_v4_assertions_for_mutation(
        self, mutation_id: str
    ) -> list[dict[str, Any]]:
        """Load durable canonical assertions for the derived graph projector."""
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT a.*, subject.canonical_name AS head, "
                "object_entity.canonical_name AS tail, "
                "reg.metadata_json AS artifact_meta "
                "FROM v4_assertions a "
                "JOIN v4_entities subject ON subject.entity_id = a.subject_id "
                "LEFT JOIN v4_entities object_entity "
                "ON object_entity.entity_id = a.object_entity_id "
                "LEFT JOIN artifact_registry reg "
                "ON reg.tenant_id = a.tenant_id "
                "AND reg.physical_artifact_id = a.assertion_id "
                "AND reg.store_name = 'SQL' "
                "AND reg.artifact_kind = 'ASSERTION' "
                "WHERE a.mutation_id = ? AND a.status IN ('ACTIVE', 'SUPERSEDED') "
                "ORDER BY a.assertion_id",
                (mutation_id,),
            ) as cursor:
                raw_assertions = await cursor.fetchall()
            assertions = [
                self._unpack_v4_assertion_row(row) for row in raw_assertions
            ]
            for a_item in assertions:
                async with db.execute(
                    "SELECT target_assertion_id FROM v4_assertion_links "
                    "WHERE source_assertion_id = ? AND relation_type = 'SUPERSEDES'",
                    (str(a_item["assertion_id"]),),
                ) as l_cur:
                    a_item["superseded_ids"] = [
                        str(r[0]) for r in await l_cur.fetchall()
                    ]
            return assertions

    async def load_v4_graph_projection_inputs_batch(
        self, mutation_ids: list[str]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], set[str]]:
        """Load mutations, terminal-fence flags, and assertions in one connection."""
        unique_ids = list(dict.fromkeys(str(mid) for mid in mutation_ids if mid))
        if not unique_ids:
            return ({}, {}, set())
        terminal = {
            "ROLLING_BACK",
            "ROLLED_BACK",
            "PURGING",
            "PURGED",
            "CANCELLED",
            "REJECTED",
        }
        mutations_by_id: dict[str, dict[str, Any]] = {}
        assertions_by_mutation: dict[str, list[dict[str, Any]]] = {
            mid: [] for mid in unique_ids
        }
        fenced_ids: set[str] = set()
        all_assertion_ids: list[str] = []
        superseded_by_source: dict[str, list[str]] = {}
        all_assertions: list[dict[str, Any]] = []

        async with self._sql.connection() as db:
            for offset in range(0, len(unique_ids), 200):
                batch_ids = unique_ids[offset : offset + 200]
                placeholders = ",".join("?" * len(batch_ids))
                async with db.execute(
                    "SELECT m.*, pr.state AS _pipeline_state, d.status AS _document_status "
                    "FROM memory_mutations m "
                    "LEFT JOIN pipeline_runs pr ON pr.pipeline_run_id = m.pipeline_run_id "
                    "LEFT JOIN documents d ON d.document_id = m.document_id "
                    f"WHERE m.mutation_id IN ({placeholders})",
                    batch_ids,
                ) as cursor:
                    rows = await cursor.fetchall()
                for row in rows:
                    row_dict = dict(row)
                    pipeline_state = row_dict.pop("_pipeline_state", None)
                    document_status = row_dict.pop("_document_status", None)
                    mid = str(row_dict["mutation_id"])
                    if (
                        row_dict.get("state") in terminal
                        or pipeline_state in terminal
                        or document_status == "PURGED"
                    ):
                        fenced_ids.add(mid)
                    mutations_by_id[mid] = self._parse_projection_mutation_row(row_dict)

                async with db.execute(
                    "SELECT a.*, subject.canonical_name AS head, "
                    "object_entity.canonical_name AS tail, "
                    "reg.metadata_json AS artifact_meta "
                    "FROM v4_assertions a "
                    "JOIN v4_entities subject ON subject.entity_id = a.subject_id "
                    "LEFT JOIN v4_entities object_entity "
                    "ON object_entity.entity_id = a.object_entity_id "
                    "LEFT JOIN artifact_registry reg "
                    "ON reg.tenant_id = a.tenant_id "
                    "AND reg.physical_artifact_id = a.assertion_id "
                    "AND reg.store_name = 'SQL' "
                    "AND reg.artifact_kind = 'ASSERTION' "
                    f"WHERE a.mutation_id IN ({placeholders}) "
                    "AND a.status IN ('ACTIVE', 'SUPERSEDED') "
                    "ORDER BY a.mutation_id, a.assertion_id",
                    batch_ids,
                ) as cursor:
                    a_rows = await cursor.fetchall()
                for a_row in a_rows:
                    item = self._unpack_v4_assertion_row(a_row)
                    all_assertions.append(item)
                    all_assertion_ids.append(str(item["assertion_id"]))

            for offset in range(0, len(all_assertion_ids), 200):
                a_batch = all_assertion_ids[offset : offset + 200]
                a_placeholders = ",".join("?" * len(a_batch))
                async with db.execute(
                    "SELECT source_assertion_id, target_assertion_id "
                    "FROM v4_assertion_links "
                    f"WHERE source_assertion_id IN ({a_placeholders}) "
                    "AND relation_type = 'SUPERSEDES'",
                    a_batch,
                ) as l_cur:
                    for src_id, dst_id in await l_cur.fetchall():
                        superseded_by_source.setdefault(str(src_id), []).append(
                            str(dst_id)
                        )

        for item in all_assertions:
            item["superseded_ids"] = superseded_by_source.get(
                str(item["assertion_id"]), []
            )
            assertions_by_mutation.setdefault(str(item["mutation_id"]), []).append(
                item
            )
        return (mutations_by_id, assertions_by_mutation, fenced_ids)

    async def _write_v4_graph_assertion_in_tx(
        self,
        graph: Any,
        *,
        mutation: dict[str, Any],
        assertion: dict[str, Any],
    ) -> tuple[str, list[tuple[str, str]]]:
        """Write one assertion and its endpoint entities into an active graph tx."""
        agent_id = str(mutation["agent_id"])
        _assert_valid_agent_id(agent_id)
        assertion_id = str(assertion["assertion_id"])
        subject_id = str(assertion["subject_id"])
        object_id = (
            str(assertion["object_entity_id"])
            if assertion.get("object_entity_id") is not None
            else None
        )
        head = str(assertion["head"])
        tail = str(assertion["tail"]) if assertion.get("tail") is not None else None
        literal_value = (
            str(assertion["literal_value"])
            if assertion.get("literal_value") is not None
            else None
        )
        if (object_id is None) == (literal_value is None):
            raise ValueError("canonical assertion object is invalid")
        graph_entities = [(subject_id, head)]
        if object_id is not None:
            graph_entities.append((object_id, tail or object_id))
        superseded_assertion_ids = assertion.get("superseded_ids")
        if superseded_assertion_ids is None:
            async with self._sql.connection() as db:
                async with db.execute(
                    "SELECT target_assertion_id FROM v4_assertion_links "
                    "WHERE source_assertion_id = ? AND relation_type = 'SUPERSEDES'",
                    (assertion_id,),
                ) as cursor:
                    superseded_assertion_ids = [
                        str(row[0]) for row in await cursor.fetchall()
                    ]
        for entity_id, entity_name in graph_entities:
            await graph.insert_node(entity_id, entity_name, agent_id)
        await graph.insert_assertion(
            assertion_id=assertion_id,
            subject_id=subject_id,
            object_id=object_id,
            object_value="" if literal_value is not None else None,
            agent_id=agent_id,
            predicate=str(assertion["predicate"]),
            mutation_id=str(mutation["mutation_id"]),
            source_ref="",
            evidence_span="",
            jurisdiction=str(assertion["jurisdiction"] or ""),
            authority_level=str(assertion["authority_level"] or ""),
            valid_from=str(assertion["valid_from"] or ""),
            valid_to=str(assertion["valid_to"] or ""),
            observed_at=str(assertion["observed_at"] or ""),
            confidence=float(assertion["confidence"]),
            pipeline_run_id=str(assertion["pipeline_run_id"] or ""),
            object_type=str(assertion.get("object_type") or "LEGACY"),
            representation_version=str(
                assertion.get("representation_version") or "legacy-v0"
            ),
        )
        for old_assertion_id in superseded_assertion_ids:
            await graph.link_assertions(
                source_assertion_id=assertion_id,
                target_assertion_id=old_assertion_id,
                agent_id=agent_id,
                relation_type="SUPERSEDES",
            )
        return (assertion_id, graph_entities)

    async def project_v4_graph_assertion(
        self, *, mutation: dict[str, Any], assertion: dict[str, Any]
    ) -> str:
        """Project an already-persisted canonical SQL assertion into Kuzu."""
        agent_id = str(mutation["agent_id"])
        _assert_valid_agent_id(agent_id)
        assertion_id = str(assertion["assertion_id"])
        subject_id = str(assertion["subject_id"])
        object_id = (
            str(assertion["object_entity_id"])
            if assertion.get("object_entity_id") is not None
            else None
        )
        head = str(assertion["head"])
        tail = str(assertion["tail"]) if assertion.get("tail") is not None else None
        graph_entities = [(subject_id, head)]
        if object_id is not None:
            graph_entities.append((object_id, tail or object_id))
        graph = self._require_graph()
        try:
            assertion_id, graph_entities = await self._write_v4_graph_assertion_in_tx(
                graph, mutation=mutation, assertion=assertion
            )
            for entity_id, entity_name in graph_entities:
                await self.record_mutation_artifact(
                    str(mutation["mutation_id"]),
                    store_name="GRAPH",
                    artifact_kind="ENTITY",
                    artifact_id=entity_id,
                    metadata={"entity_name": entity_name},
                )
            await self.record_mutation_artifact(
                str(mutation["mutation_id"]),
                store_name="GRAPH",
                artifact_kind="ASSERTION",
                artifact_id=assertion_id,
                metadata={"predicate": str(assertion["predicate"])},
            )
        except Exception:
            await self._compensate_v4_graph_projection(
                mutation=mutation,
                assertion_id=assertion_id,
                graph_entities=graph_entities,
            )
            raise
        return assertion_id

    async def project_v4_graph_triplet(
        self, *, mutation: dict[str, Any], triplet: dict[str, Any]
    ) -> str:
        """Compatibility helper; supported workers use separate SQL/GRAPH lanes."""
        assertion_id = await self.project_v4_sql_assertion(
            mutation=mutation, triplet=triplet
        )
        assertions = await self.list_v4_assertions_for_mutation(
            str(mutation["mutation_id"])
        )
        assertion = next(
            item for item in assertions if item["assertion_id"] == assertion_id
        )
        return await self.project_v4_graph_assertion(
            mutation=mutation, assertion=assertion
        )

    async def _compensate_v4_graph_projection(
        self,
        *,
        mutation: dict[str, Any],
        assertion_id: str,
        graph_entities: list[tuple[str, str]],
    ) -> None:
        """Remove or durably queue every graph write after a lost fence."""
        graph = self._require_graph()
        agent_id = str(mutation["agent_id"])
        failed: list[tuple[str, str, str]] = []
        try:
            await graph.delete_assertions(
                agent_id=agent_id, assertion_ids=[assertion_id]
            )
        except Exception:
            failed.append(("GRAPH", "ASSERTION", assertion_id))
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT m.state, pr.state FROM memory_mutations m "
                "LEFT JOIN pipeline_runs pr ON pr.pipeline_run_id = m.pipeline_run_id "
                "WHERE m.mutation_id = ?",
                (str(mutation["mutation_id"]),),
            ) as cursor:
                state_row = await cursor.fetchone()
        terminal_states = {
            "ROLLING_BACK",
            "ROLLED_BACK",
            "PURGING",
            "PURGED",
            "CANCELLED",
            "REJECTED",
        }
        terminal = state_row is not None and (
            state_row[0] in terminal_states or state_row[1] in terminal_states
        )
        if terminal:
            # A concurrent terminal lifecycle transition owns canonical
            # removal.  Ordinary graph unavailability never enters this path.
            async with self._sql.transaction() as db:
                await db.execute(
                    "DELETE FROM v4_assertion_links WHERE source_assertion_id = ? "
                    "OR target_assertion_id = ?",
                    (assertion_id, assertion_id),
                )
                await db.execute(
                    "DELETE FROM v4_assertions WHERE assertion_id = ? AND mutation_id = ?",
                    (assertion_id, str(mutation["mutation_id"])),
                )
                await db.execute(
                    "DELETE FROM memory_artifacts WHERE mutation_id = ? "
                    "AND store_name = 'SQL' AND artifact_kind = 'ASSERTION' "
                    "AND artifact_id = ?",
                    (str(mutation["mutation_id"]), assertion_id),
                )
                await db.commit()
        for entity_id, _entity_name in graph_entities:
            async with self._sql.connection() as db:
                async with db.execute(
                    "SELECT 1 FROM artifact_registry r "
                    "JOIN artifact_sources s ON s.registry_id = r.registry_id "
                    "WHERE r.store_name = 'GRAPH' AND r.artifact_kind = 'ENTITY' "
                    "AND r.physical_artifact_id = ? AND s.state = 'ACTIVE' LIMIT 1",
                    (entity_id,),
                ) as cursor:
                    has_owner = await cursor.fetchone()
            if has_owner is None:
                try:
                    await graph.delete_nodes(
                        purge_id=f"fence_comp:{mutation['mutation_id']}",
                        agent_id=agent_id,
                        node_ids=[entity_id],
                    )
                except Exception:
                    failed.append(("GRAPH", "ENTITY", entity_id))
        if failed:
            await self._enqueue_unowned_projection_cleanup(
                str(mutation["mutation_id"]), failed
            )

    async def search_v4_memory(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        dataset_ids: list[str],
        query: str,
        limit: int = 10,
        jurisdiction: str | None = None,
        valid_at: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        rrf_k: int | None = None,
        rrf_weights: Mapping[str, float] | None = None,
        graph_enabled: bool = True,
        request_principal_id: str | None = None,
        certification_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Reuse only version-valid authorized scope data, never query results."""
        _assert_valid_agent_id(agent_id)
        if not tenant_id or not dataset_ids or not query.strip() or not 1 <= limit <= 50:
            raise ValueError("invalid v4 search scope")
        query_vector: list[float] | None = None
        query_error: Exception | None = None
        if self._embedding_enabled and self._vec is not None:
            try:
                query_vector = await self._vec.compute_query_embedding(query)
            except Exception as exc:
                # Preserve degradation/error semantics: a provider error matters
                # only if the authorized scope actually has compatible vectors.
                query_error = exc
        kwargs: dict[str, Any] = dict(
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=dataset_ids,
            query=query,
            limit=max(limit, self._rerank_depth) if self._reranker is not None else limit,
            jurisdiction=jurisdiction,
            valid_at=valid_at,
            valid_from=valid_from,
            valid_to=valid_to,
            rrf_k=rrf_k,
            rrf_weights=rrf_weights,
            graph_enabled=graph_enabled,
            request_principal_id=request_principal_id,
            certification_metadata=certification_metadata,
            _query_vector=query_vector,
            _query_error=query_error,
        )
        if not self._search_scope_cache_enabled:
            if self._reranker is None:
                return await self._search_v4_memory_uncached(**kwargs)
            result = await self._search_v4_memory_uncached(**kwargs)
            return await self._rerank_results(query, result, limit)
        async with self._sql.search_connection() as db:
            if self._search_cache_db is not db:
                self._search_scope_cache.clear()
                self._search_data_version = None
                self._search_cache_db = db
            previous_tables = {
                scope["fts_table"] for scope in self._search_scope_cache.values()
            }
            try:
                await db.execute("BEGIN")
                # Pin the WAL read snapshot before comparing the connection-local
                # version. A concurrent commit cannot mix old FTS with new rows.
                async with db.execute(
                    "SELECT rootpage FROM main.sqlite_master LIMIT 1"
                ) as cursor:
                    await cursor.fetchone()
                async with db.execute("PRAGMA data_version") as cursor:
                    version = int((await cursor.fetchone())[0])
                if version != self._search_data_version:
                    await self._clear_search_scope_cache(db)
                    self._search_data_version = version
                key = (
                    tenant_id,
                    tuple(sorted(set(dataset_ids))),
                    agent_id,
                    jurisdiction,
                    valid_at,
                    valid_from,
                    valid_to,
                )
                scope = self._search_scope_cache.get(key)
                if scope is None:
                    if len(self._search_scope_cache) == 4:
                        _old_key, old_scope = self._search_scope_cache.popitem(
                            last=False
                        )
                        await db.execute(
                            f"DROP TABLE IF EXISTS temp.{old_scope['fts_table']}"
                        )
                    scope = {
                        "fts_table": "v4_scoped_assertions_fts_" + uuid.uuid4().hex
                    }
                    self._search_scope_cache[key] = scope
                self._search_scope_cache.move_to_end(key)
                result = await self._search_v4_memory_uncached(
                    **kwargs, _search_db=db, _scope=scope
                )
                await db.commit()  # Only TEMP can be written on this connection.
            except BaseException:
                await db.rollback()
                # Rollback can restore tables evicted earlier in this search.
                for table in previous_tables:
                    await db.execute(f"DROP TABLE IF EXISTS temp.{table}")
                await self._clear_search_scope_cache(db)
                self._search_data_version = None
                raise
        if self._reranker is not None:
            return await self._rerank_results(query, result, limit)
        return result

    async def _rerank_results(
        self, query: str, results: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        if not results:
            return []
        texts = [row.pop("_rerank_text") for row in results]
        scores = await asyncio.to_thread(self._reranker.score, query, texts)
        if len(scores) != len(results) or any(not math.isfinite(s) for s in scores):
            raise RuntimeError("reranker returned invalid scores")
        for row, score in zip(results, scores):
            row["rerank_score"] = float(score)
        # Python's stable sort retains fused order for tied scores.
        ordered = sorted(results, key=lambda row: -row["rerank_score"])
        exact = sorted(
            (row for row in ordered if "exact_match" in row["retrieval_provenance"]),
            key=lambda row: row["retrieval_provenance"]["exact_match"]["order"],
        )[:3]
        return (exact + [row for row in ordered if row not in exact])[:limit]

    async def _clear_search_scope_cache(self, db: aiosqlite.Connection) -> None:
        for scope in self._search_scope_cache.values():
            await db.execute(f"DROP TABLE IF EXISTS temp.{scope['fts_table']}")
        self._search_scope_cache.clear()

    @asynccontextmanager
    async def _search_read_connection(
        self, retained: aiosqlite.Connection | None
    ) -> AsyncIterator[aiosqlite.Connection]:
        if retained is not None:
            yield retained
        else:
            async with self._sql.connection() as db:
                yield db

    async def _search_v4_memory_uncached(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        dataset_ids: list[str],
        query: str,
        limit: int = 10,
        jurisdiction: str | None = None,
        valid_at: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        rrf_k: int | None = None,
        rrf_weights: Mapping[str, float] | None = None,
        graph_enabled: bool = True,
        request_principal_id: str | None = None,
        certification_metadata: dict[str, Any] | None = None,
        _search_db: aiosqlite.Connection | None = None,
        _scope: dict[str, Any] | None = None,
        _query_vector: list[float] | None = None,
        _query_error: Exception | None = None,
    ) -> list[dict[str, Any]]:
        """Dataset-filter every retrieval lane, then combine ranks with RRF."""
        _assert_valid_agent_id(agent_id)
        graph_enabled = graph_enabled and self._graph_enabled
        if (
            not tenant_id
            or not dataset_ids
            or not query.strip()
            or not 1 <= limit <= 50
        ):
            raise ValueError("invalid v4 search scope")

        effective_k = rrf_k if rrf_k is not None else self._rrf_k
        effective_weights = (
            rrf_weights if rrf_weights is not None else self._rrf_weights
        )

        scope = _scope if _scope is not None else {}
        scoped_fts = scope.get("fts_table", "v4_scoped_assertions_fts")
        text_features = scope.setdefault("text_features", {})
        historical_query = bool(valid_at or valid_from or valid_to)
        assertion_status_sql = (
            "(a.status = 'ACTIVE' OR (a.status = 'SUPERSEDED' AND a.valid_to != ''))"
            if historical_query
            else "a.status = 'ACTIVE'"
        )
        provenance_filters: list[str] = []
        provenance_params: list[Any] = []
        if jurisdiction:
            provenance_filters.append("a.jurisdiction = ?")
            provenance_params.append(jurisdiction)
        if valid_at:
            provenance_filters.extend(
                (
                    "(a.valid_from = '' OR julianday(a.valid_from) <= julianday(?))",
                    "(a.valid_to = '' OR julianday(a.valid_to) >= julianday(?))",
                )
            )
            provenance_params.extend((valid_at, valid_at))
        if valid_from:
            provenance_filters.append(
                "(a.valid_to = '' OR julianday(a.valid_to) >= julianday(?))"
            )
            provenance_params.append(valid_from)
        if valid_to:
            provenance_filters.append(
                "(a.valid_from = '' OR julianday(a.valid_from) <= julianday(?))"
            )
            provenance_params.append(valid_to)

        async with self._search_read_connection(_search_db) as db:
            if "datasets" in scope:
                datasets = scope["datasets"]
            else:
                datasets = sorted(
                    {
                        await self._catalog.resolve_id_in_tx(
                            db,
                            tenant_id=tenant_id,
                            kind="dataset",
                            external_id=dataset_id,
                        )
                        for dataset_id in dataset_ids
                    }
                )
                scope["datasets"] = datasets
            placeholders = ",".join("?" for _ in datasets)
            if "artifact_rows" in scope:
                artifact_rows = scope["artifact_rows"]
            else:
                async with db.execute(
                    "SELECT DISTINCT r.artifact_kind, r.physical_artifact_id, "
                    "r.metadata_json, r.registry_id FROM artifact_registry r "
                    "JOIN artifact_sources s ON s.registry_id = r.registry_id "
                    "JOIN memory_mutations m ON m.mutation_id = s.mutation_id "
                    "LEFT JOIN v4_entities e ON r.artifact_kind = 'ENTITY' "
                    "AND e.entity_id = r.physical_artifact_id "
                    f"WHERE r.tenant_id = ? AND s.dataset_id IN ({placeholders}) "
                    "AND m.agent_id = ? AND m.state = 'COMMITTED' "
                    "AND (r.artifact_kind != 'ASSERTION_VECTOR' OR r.agent_id = ?) "
                    "AND r.state = 'ACTIVE' AND s.state = 'ACTIVE' "
                    "AND r.artifact_kind IN ('ENTITY', 'ASSERTION_VECTOR') "
                    "AND (r.artifact_kind = 'ASSERTION_VECTOR' OR "
                    "(e.tenant_id = r.tenant_id AND e.status = 'ACTIVE'))",
                    (tenant_id, *datasets, agent_id, agent_id),
                ) as cursor:
                    artifact_rows = await cursor.fetchall()

                scope["artifact_rows"] = artifact_rows

            # Pre-filter in-scope assertions before rank contribution in ANY lane
            in_scope_assertion_query = (
                "SELECT a.* FROM v4_assertions a "
                f"WHERE a.tenant_id = ? AND a.dataset_id IN ({placeholders}) "
                f"AND {assertion_status_sql} "
                "AND EXISTS (SELECT 1 FROM memory_mutations m "
                "WHERE m.mutation_id = a.mutation_id AND m.agent_id = ? AND m.state = 'COMMITTED') "
                + (
                    "AND " + " AND ".join(provenance_filters)
                    if provenance_filters
                    else ""
                )
            )
            if "assertion_rows" in scope:
                in_scope_assertion_rows = scope["assertion_rows"]
            else:
                async with db.execute(
                    in_scope_assertion_query,
                    (tenant_id, *datasets, agent_id, *provenance_params),
                ) as cursor:
                    in_scope_assertion_rows = await cursor.fetchall()

                scope["assertion_rows"] = in_scope_assertion_rows

            if certification_metadata is not None:
                if "degraded_sources" in scope:
                    degraded_sources = scope["degraded_sources"]
                else:
                    degraded_sources = []
                    for lane, enabled in (
                        ("VECTOR", self._embedding_enabled),
                        ("GRAPH", self._graph_enabled),
                    ):
                        incomplete = False
                        if enabled:
                            async with db.execute(
                                "SELECT 1 FROM projection_outbox p "
                                f"JOIN ({in_scope_assertion_query}) scoped "
                                "ON scoped.mutation_id = p.mutation_id "
                                "WHERE p.projection_name = ? "
                                "AND p.state != 'COMPLETED' LIMIT 1",
                                (
                                    tenant_id,
                                    *datasets,
                                    agent_id,
                                    *provenance_params,
                                    lane,
                                ),
                            ) as cursor:
                                incomplete = await cursor.fetchone() is not None
                        if not enabled or incomplete:
                            degraded_sources.append(lane.lower())
                    scope["degraded_sources"] = degraded_sources
                certification_metadata["degraded_sources"] = list(degraded_sources)

            audit_rows: list[aiosqlite.Row] = []
            if certification_metadata is not None:
                if "audit_rows" in scope:
                    audit_rows = scope["audit_rows"]
                else:
                    audit_query = (
                        "SELECT a.assertion_id, a.tenant_id, a.dataset_id, "
                        "COALESCE(c.external_id, '') AS external_dataset_id, "
                        "m.agent_id, a.jurisdiction, a.status, a.valid_from, a.valid_to "
                        "FROM v4_assertions a "
                        "JOIN memory_mutations m ON m.mutation_id = a.mutation_id "
                        "LEFT JOIN v4_catalog_identities c ON c.tenant_id = a.tenant_id "
                        "AND c.kind = 'dataset' AND c.physical_id = a.dataset_id "
                        f"WHERE a.tenant_id = ? AND a.dataset_id IN ({placeholders}) AND {assertion_status_sql} "
                        "AND m.agent_id = ? AND m.state = 'COMMITTED' "
                        + (
                            "AND " + " AND ".join(provenance_filters)
                            if provenance_filters
                            else ""
                        )
                        + " ORDER BY a.assertion_id, a.tenant_id, m.agent_id"
                    )
                    async with db.execute(
                        audit_query,
                        (tenant_id, *datasets, agent_id, *provenance_params),
                    ) as cursor:
                        audit_rows = list(await cursor.fetchall())

                    scope["audit_rows"] = audit_rows

        if certification_metadata is not None:
            requested_scope = {
                "tenant_id": tenant_id,
                "dataset_ids": sorted(set(dataset_ids)),
                "agent_id": agent_id,
                **(
                    {"principal_id": request_principal_id}
                    if request_principal_id is not None
                    else {}
                ),
                "jurisdiction": jurisdiction,
                "valid_at": valid_at,
                "valid_from": valid_from,
                "valid_to": valid_to,
            }
            requested_scope_identity = stable_contract_hash(requested_scope)
            query_identity = stable_contract_hash({"query": query})
            retrieval_config_identity = stable_contract_hash(
                {
                    "limit": limit,
                    "rrf_k": effective_k,
                    "rrf_weights": dict(effective_weights),
                }
            )
            if "audit_decisions" in scope:
                audit_decisions, eligible_count = scope["audit_decisions"]
            else:
                audit_decisions = []
                eligible_count = 0
                for row in audit_rows:
                    eligible = (
                        str(row["tenant_id"]) == tenant_id
                        and str(row["dataset_id"]) in datasets
                        and str(row["agent_id"]) == agent_id
                    )
                    eligible_count += int(eligible)
                    audit_decisions.append(
                        {
                            "assertion_id": str(row["assertion_id"]),
                            "tenant_id": str(row["tenant_id"]),
                            "dataset_id": str(row["external_dataset_id"]),
                            "agent_id": str(row["agent_id"]),
                            "jurisdiction": str(row["jurisdiction"] or ""),
                            "status": str(row["status"]),
                            "valid_from": str(row["valid_from"] or ""),
                            "valid_to": str(row["valid_to"] or ""),
                            "eligible": eligible,
                        }
                    )
                scope["audit_decisions"] = (audit_decisions, eligible_count)
            certification_metadata.update(
                {
                    "scope_audit": {
                        "contract_version": V4_SCOPE_AUDIT_CONTRACT_VERSION,
                        "enforcement_stage": "pre_rank",
                        "requested_scope": requested_scope,
                        "requested_scope_identity": requested_scope_identity,
                        "query_identity": query_identity,
                        "evaluated_candidate_count": len(audit_decisions),
                        "excluded_candidate_count": len(audit_decisions)
                        - eligible_count,
                        "eligible_candidate_count": eligible_count,
                        "exclusion_audit_hash": stable_contract_hash(
                            {
                                "query_identity": query_identity,
                                "requested_scope_identity": requested_scope_identity,
                                "decisions": audit_decisions,
                            }
                        ),
                    },
                    "graph_ablation": {
                        "contract_version": V4_GRAPH_ABLATION_CONTRACT_VERSION,
                        "mode": "enabled" if graph_enabled else "disabled",
                        "pair_identity": stable_contract_hash(
                            {
                                "query_identity": query_identity,
                                "retrieval_config_identity": retrieval_config_identity,
                                "scope_identity": requested_scope_identity,
                            }
                        ),
                        "query_identity": query_identity,
                        "retrieval_config_identity": retrieval_config_identity,
                        "scope_identity": requested_scope_identity,
                    },
                }
            )

        raw_allowed_entity_ids = {
            str(row[1]) for row in artifact_rows if row[0] == "ENTITY"
        }
        embedding_identity = getattr(self._vec, "embedding_identity", None)
        allowed_vector_registry_ids: set[str] = set()
        vector_registry_artifacts: dict[str, dict[str, Any]] = {}
        vector_rejection_reasons: Counter[str] = Counter()
        vector_registry_count = 0
        for artifact_row in artifact_rows:
            if artifact_row[0] != "ASSERTION_VECTOR":
                continue
            vector_registry_count += 1
            artifact_metadata = _decode_registry_metadata(artifact_row[2])
            registry_id = str(artifact_row[3]) if len(artifact_row) > 3 else ""
            errors = list(
                V4_VECTOR_CONSUMER_CONTRACT.compatibility_errors(
                    artifact_metadata,
                    embedding_identity=embedding_identity,
                )
            )
            if not registry_id:
                errors.append("registry_id")
            if errors:
                vector_rejection_reasons.update(errors)
                continue
            vector_id = str(artifact_row[1])
            allowed_vector_registry_ids.add(vector_id)
            vector_registry_artifacts[vector_id] = {
                "registry_id": registry_id,
                "vector_id": vector_id,
                "representation_version": artifact_metadata["representation_version"],
                "embedding_space_id": artifact_metadata["embedding_space_id"],
                "embedding_dimension": artifact_metadata["embedding_dimension"],
            }
        if "assertions" not in scope:
            scope["assertions"] = {
                str(row["assertion_id"]): dict(row) for row in in_scope_assertion_rows
            }
        in_scope_assertions = scope["assertions"]
        in_scope_assertion_ids = set(in_scope_assertions)
        if "assertion_stable_keys" not in scope:
            # Build a deterministic content-identity tie-break key per assertion
            # once per scope/data_version so two fresh stores with different
            # opaque physical UUIDs break every lane and fusion tie identically.
            chunk_stable_order: dict[str, tuple[str, int, int, str]] = {}
            authorized_chunk_ids = sorted(
                {str(a["chunk_id"]) for a in in_scope_assertions.values() if a.get("chunk_id")}
            )
            async with self._search_read_connection(_search_db) as db:
                for offset in range(0, len(authorized_chunk_ids), 500):
                    batch = authorized_chunk_ids[offset : offset + 500]
                    marks = ",".join("?" for _ in batch)
                    async with db.execute(
                        "SELECT c.chunk_id, c.ordinal, r.revision_number, "
                        "COALESCE(ci_doc.external_id, r.document_id) AS ext_document_id, "
                        "COALESCE(ci_chunk.external_id, c.chunk_id) AS ext_chunk_id "
                        "FROM source_chunks c "
                        "JOIN document_revisions r ON r.revision_id = c.revision_id "
                        "LEFT JOIN v4_catalog_identities ci_doc "
                        "ON ci_doc.kind = 'document' AND ci_doc.physical_id = r.document_id "
                        "LEFT JOIN v4_catalog_identities ci_chunk "
                        "ON ci_chunk.kind = 'chunk' AND ci_chunk.physical_id = c.chunk_id "
                        f"WHERE c.tenant_id = ? AND c.chunk_id IN ({marks})",
                        (tenant_id, *batch),
                    ) as cursor:
                        for row in await cursor.fetchall():
                            chunk_stable_order[str(row["chunk_id"])] = (
                                str(row["ext_document_id"] or ""),
                                int(row["revision_number"] or 0),
                                int(row["ordinal"] or 0),
                                str(row["ext_chunk_id"] or ""),
                            )
            assertion_stable_keys: dict[str, tuple[Any, ...]] = {}
            for aid, assertion in in_scope_assertions.items():
                cid = str(assertion.get("chunk_id") or "")
                chunk_key = chunk_stable_order.get(
                    cid,
                    (
                        str(assertion.get("document_id") or ""),
                        0,
                        0,
                        cid,
                    ),
                )
                assertion_stable_keys[aid] = (
                    *chunk_key,
                    str(assertion.get("predicate") or ""),
                    str(assertion.get("subject_id") or ""),
                    str(assertion.get("object_entity_id") or ""),
                    str(assertion.get("literal_value") or ""),
                    str(assertion.get("evidence_span") or ""),
                    str(assertion.get("source_ref") or ""),
                    aid,
                )
            scope["chunk_stable_order"] = chunk_stable_order
            scope["assertion_stable_keys"] = assertion_stable_keys
        chunk_stable_order = scope["chunk_stable_order"]
        assertion_stable_keys = scope["assertion_stable_keys"]

        def _assertion_stable_key(aid: str) -> tuple[Any, ...]:
            key = assertion_stable_keys.get(aid)
            if key is not None:
                return key
            return ("", 0, 0, "", "", "", "", "", "", "", aid)
        if self._legal_rules_enabled and "legal_context" not in scope:
            # Only authorized assertions select revisions. Header/title names and
            # chunk ordinals share the existing data_version scope-cache lifetime.
            revisions = sorted(
                {str(a["revision_id"]) for a in in_scope_assertions.values()}
            )
            revision_laws: dict[str, set[str]] = {}
            identities: dict[str, dict[str, str]] = {}
            for assertion in in_scope_assertions.values():
                identity = _assertion_legal_identity(assertion)
                if identity:
                    identities[str(assertion["assertion_id"])] = identity
                    revision_laws.setdefault(str(assertion["revision_id"]), set()).add(
                        identity["law_id"]
                    )
            names: dict[str, set[str]] = {}
            decisions: dict[tuple[str, str], set[str]] = {}
            authorized_mutations: dict[str, set[str]] = {}
            for aid, assertion in in_scope_assertions.items():
                authorized_mutations.setdefault(
                    str(assertion["mutation_id"]), set()
                ).add(aid)
            chunk_order: dict[str, tuple[str, str, int]] = {}
            authorized_chunks = {str(a["chunk_id"]) for a in in_scope_assertions.values()}
            named_revisions: set[str] = set()
            async with self._search_read_connection(_search_db) as db:
                for offset in range(0, len(revisions), 500):
                    batch = revisions[offset : offset + 500]
                    marks = ",".join("?" for _ in batch)
                    async with db.execute(
                        "SELECT c.chunk_id,c.ordinal,c.revision_id,r.document_id,d.title,"
                        "CASE WHEN c.ordinal=0 THEN c.content_payload ELSE '' END AS header,"
                        "m.mutation_id,CASE WHEN c.ordinal=0 THEN m.metadata_json ELSE '{}' END AS rule_metadata "
                        "FROM source_chunks c JOIN document_revisions r ON r.revision_id=c.revision_id "
                        "JOIN documents d ON d.document_id=r.document_id "
                        "LEFT JOIN memory_mutations m ON m.chunk_id=c.chunk_id AND m.tenant_id=c.tenant_id "
                        f"WHERE c.tenant_id=? AND c.revision_id IN ({marks})",
                        (tenant_id, *batch),
                    ) as cursor:
                        for row in await cursor.fetchall():
                            if str(row["chunk_id"]) not in authorized_chunks:
                                continue
                            revision = str(row["revision_id"])
                            chunk_order[str(row["chunk_id"])] = (
                                str(row["document_id"]),
                                revision,
                                int(row["ordinal"]),
                            )
                            # Only the authorized first chunk can teach its own
                            # künye; later quotations and out-of-scope mutations
                            # of the same revision cannot enter the map.
                            mid = str(row["mutation_id"])
                            if row["ordinal"] == 0 and mid in authorized_mutations:
                                item_set = _decode_registry_metadata(
                                    row["rule_metadata"]
                                ).get("_mesa_legal_items")
                                items = (
                                    item_set.get("items", [])
                                    if isinstance(item_set, dict)
                                    else []
                                )
                                for item in items if isinstance(items, list) else ():
                                    if not isinstance(item, dict):
                                        continue
                                    start = item.get("start")
                                    docket = item.get("docket_no")
                                    decision = item.get("decision_no")
                                    if (
                                        item.get("type") == "decision_ref"
                                        and isinstance(start, int)
                                        and 0 <= start < 300
                                        and isinstance(docket, str)
                                        and docket
                                        and isinstance(decision, str)
                                        and decision
                                    ):
                                        decisions.setdefault(
                                            (docket, decision), set()
                                        ).update(authorized_mutations[mid])
                            laws = revision_laws.get(revision, set())
                            if len(laws) != 1:
                                continue
                            header = re.split(
                                r"\bKanun(?:un)?\s+(?:Numarası|No\.)",
                                str(row["header"] or "")[:3000],
                                maxsplit=1,
                                flags=re.IGNORECASE,
                            )
                            header_name = ""
                            if len(header) == 2:
                                header_tail = header[0].rstrip()
                                end = len(header_tail)
                                while end and header_tail[end - 1].isdecimal():
                                    end -= 1
                                header_name = header_tail[:end].strip()
                            revision_names = [header_name]
                            if revision not in named_revisions:
                                revision_names.append(str(row["title"] or ""))
                                named_revisions.add(revision)
                            for name in revision_names:
                                if name and len(name) <= 200:
                                    normalized_name = normalize_law_name(name)
                                    if len(normalized_name) <= 200:
                                        names.setdefault(normalized_name, set()).update(laws)
            scope["legal_context"] = {
                "names": {
                    name: next(iter(laws))
                    for name, laws in names.items()
                    if len(laws) == 1
                },
                "chunk_order": chunk_order,
                "identities": identities,
                "decisions": decisions,
                "ordered_assertions": sorted(
                    in_scope_assertions,
                    key=_assertion_stable_key,
                ),
            }
        legal_context = scope["legal_context"] if self._legal_rules_enabled else {
            "names": {}, "chunk_order": {}, "identities": {}, "decisions": {},
            "ordered_assertions": [],
        }
        if self._legal_rules_enabled:
            if "legal_resolver" not in scope:
                scope["legal_resolver"] = await asyncio.to_thread(
                    LegalEntityResolver, law_names=legal_context["names"]
                )
            legal_resolver = scope["legal_resolver"]
            # Resolver positions stay internal; outer whitespace carries no identity.
            # Avoid retrying its boundary patterns along an empty query prefix.
            legal_citations = await asyncio.to_thread(
                legal_resolver.extract_citations, query.strip()
            )
        else:
            legal_citations = []
        target_statutes = {c.statute_code for c in legal_citations}
        exact_keys = {(c.statute_code, c.article) for c in legal_citations if c.article}
        decision_ids: set[str] = set()
        query_items = (
            await asyncio.to_thread(legal_rules.extract, query)
            if legal_context["decisions"]
            else ()
        )
        for item in query_items:
            if item["type"] == "decision_ref":
                decision_ids.update(
                    legal_context["decisions"].get(
                        (item.get("docket_no"), item.get("decision_no")), ()
                    )
                )
        exact_ids: list[str] = []
        exact_kinds: dict[str, str] = {}
        seen_chunks: set[str] = set()
        for aid in (
            legal_context["ordered_assertions"] if exact_keys or decision_ids else ()
        ):
            assertion = in_scope_assertions[aid]
            identity = legal_context["identities"].get(aid)
            chunk = str(assertion["chunk_id"])
            if (
                identity
                and (identity["statute_code"], identity["article_id"]) in exact_keys
                or aid in decision_ids
            ) and chunk not in seen_chunks:
                exact_ids.append(aid)
                exact_kinds[aid] = (
                    "article"
                    if identity
                    and (identity["statute_code"], identity["article_id"]) in exact_keys
                    else "decision"
                )
                seen_chunks.add(chunk)
                if len(exact_ids) == 3:
                    break
        typed_legal_candidate_ids: set[str] = set()
        if legal_citations:
            identity_clauses: list[str] = []
            identity_params: list[str] = []
            for citation in legal_citations:
                if citation.article:
                    identity_clauses.append("(a.statute_code = ? AND a.article_id = ?)")
                    identity_params.extend((citation.statute_code, citation.article))
                else:
                    identity_clauses.append("a.statute_code = ?")
                    identity_params.append(citation.statute_code)
            typed_candidate_query = (
                "SELECT a.assertion_id FROM v4_assertions a INDEXED BY "
                "idx_v4_assertions_legal_identity_scope "
                "LEFT JOIN source_chunks c ON c.chunk_id = a.chunk_id "
                "LEFT JOIN document_revisions r ON r.revision_id = a.revision_id "
                "LEFT JOIN v4_catalog_identities ci_doc "
                "ON ci_doc.kind = 'document' AND ci_doc.physical_id = a.document_id "
                "LEFT JOIN v4_catalog_identities ci_chunk "
                "ON ci_chunk.kind = 'chunk' AND ci_chunk.physical_id = a.chunk_id "
                f"WHERE a.tenant_id = ? AND a.dataset_id IN ({placeholders}) "
                f"AND {assertion_status_sql} "
                "AND a.legal_identity_source IN ('catalog', 'verified_ingestion') "
                f"AND ({' OR '.join(identity_clauses)}) "
                "AND EXISTS (SELECT 1 FROM memory_mutations m "
                "WHERE m.mutation_id = a.mutation_id AND m.agent_id = ? "
                "AND m.state = 'COMMITTED') "
                + (
                    "AND " + " AND ".join(provenance_filters)
                    if provenance_filters
                    else ""
                )
                + " ORDER BY a.statute_code, a.article_id, a.legal_version_id, "
                "COALESCE(ci_doc.external_id, a.document_id), "
                "COALESCE(r.revision_number, 0), COALESCE(c.ordinal, 0), "
                "COALESCE(ci_chunk.external_id, a.chunk_id), a.assertion_id LIMIT ?"
            )
            async with self._search_read_connection(_search_db) as db:
                async with db.execute(
                    typed_candidate_query,
                    (
                        tenant_id,
                        *datasets,
                        *identity_params,
                        agent_id,
                        *provenance_params,
                        min(1000, max(limit * 25, 100)),
                    ),
                ) as cursor:
                    typed_legal_candidate_ids = {
                        str(row["assertion_id"]) for row in await cursor.fetchall()
                    }
        typed_legal_candidate_ids.update(
            aid for aid in exact_ids if exact_kinds[aid] == "article"
        )
        in_scope_entity_ids_from_assertions = {
            str(row["subject_id"])
            for row in in_scope_assertion_rows
            if row["subject_id"]
        } | {
            str(row["object_entity_id"])
            for row in in_scope_assertion_rows
            if row["object_entity_id"]
        }

        if provenance_filters:
            allowed_entity_ids = (
                raw_allowed_entity_ids & in_scope_entity_ids_from_assertions
            )
        else:
            allowed_entity_ids = raw_allowed_entity_ids

        allowed_vector_ids = allowed_vector_registry_ids & in_scope_assertion_ids
        identity_metadata = V4_VECTOR_CONSUMER_CONTRACT._identity_metadata(
            embedding_identity
        )
        vector_lane_diagnostics: dict[str, Any] = {
            "contract_version": V4_VECTOR_LANE_DIAGNOSTIC_CONTRACT_VERSION,
            "status": (
                "no_registry_artifacts"
                if vector_registry_count == 0
                else (
                    "no_compatible_artifacts"
                    if not allowed_vector_registry_ids
                    else (
                        "no_in_scope_artifacts" if not allowed_vector_ids else "ready"
                    )
                )
            ),
            "representation_version": V4_VECTOR_CONSUMER_CONTRACT.representation_version,
            "embedding_space_id": (
                identity_metadata["embedding_space_id"]
                if identity_metadata is not None
                else None
            ),
            "registry_artifact_count": vector_registry_count,
            "compatible_registry_artifact_count": len(allowed_vector_registry_ids),
            "allowed_vector_id_count": len(allowed_vector_ids),
            "rejected_artifact_count": (
                vector_registry_count - len(allowed_vector_registry_ids)
            ),
            "rejection_reasons": dict(sorted(vector_rejection_reasons.items())),
            "search_executed": False,
            "search_result_count": 0,
            "candidate_count": 0,
            "physical_vector_id_count": len(allowed_vector_ids),
            "missing_vector_id_count": 0,
        }
        if not self._embedding_enabled:
            vector_lane_diagnostics["status"] = "semantic_runtime_disabled"
        if certification_metadata is not None:
            certification_metadata["vector_lane"] = vector_lane_diagnostics
        if vector_registry_count and not allowed_vector_registry_ids:
            logger.warning(
                "V4_VECTOR_LANE_NO_COMPATIBLE_ARTIFACTS | tenant_id=%s "
                "agent_id=%s registry_artifacts=%s rejection_reasons=%s",
                tenant_id,
                agent_id,
                vector_registry_count,
                dict(sorted(vector_rejection_reasons.items())),
            )
        if not allowed_entity_ids and not in_scope_assertion_ids:
            return []

        vector_lane: list[str] = []
        vector_raw_distances: dict[str, float] = {}
        vector_assertions: dict[str, dict[str, Any]] = {}
        if self._embedding_enabled and self._vec is not None and allowed_vector_ids:
            try:
                searchable_vector_ids = allowed_vector_ids
                if callable(getattr(type(self._vec), "get_existing_node_ids", None)):
                    existing_vector_ids = await self._vec.get_existing_node_ids(
                        agent_id, sorted(allowed_vector_ids)
                    )
                    searchable_vector_ids = allowed_vector_ids & existing_vector_ids
                    missing_vector_ids = allowed_vector_ids - existing_vector_ids
                    vector_lane_diagnostics["physical_vector_id_count"] = len(
                        searchable_vector_ids
                    )
                    vector_lane_diagnostics["missing_vector_id_count"] = len(
                        missing_vector_ids
                    )
                    if missing_vector_ids:
                        logger.warning(
                            "V4_VECTOR_REGISTRY_PHYSICAL_MISMATCH | tenant_id=%s "
                            "agent_id=%s missing_vector_ids=%s",
                            tenant_id,
                            agent_id,
                            len(missing_vector_ids),
                        )
                    if not searchable_vector_ids:
                        vector_lane_diagnostics["status"] = "registry_vector_mismatch"
                if not searchable_vector_ids:
                    vector_rows = []
                else:
                    if _query_error is not None:
                        raise _query_error
                    if _query_vector is None:
                        raise VectorSearchError("query embedding is unavailable")
                    vector_rows = await self._vec.search(
                        _query_vector,
                        agent_id=agent_id,
                        allowed_node_ids=searchable_vector_ids,
                        limit=min(500, max(limit * 10, 50)),
                    )
                    vector_lane_diagnostics["search_executed"] = True
                vector_lane_diagnostics["search_result_count"] = len(vector_rows)
                seen_vec: set[str] = set()
                ranked_assertion_ids: list[str] = []
                has_explicit_distance = False
                for v_row in vector_rows:
                    node_id = str(v_row.get("node_id", ""))
                    if node_id in allowed_vector_ids and node_id not in seen_vec:
                        ranked_assertion_ids.append(node_id)
                        seen_vec.add(node_id)
                        if "_distance" in v_row and v_row["_distance"] is not None:
                            has_explicit_distance = True
                        vector_raw_distances[node_id] = float(
                            v_row.get("_distance", 0.0)
                        )
                if has_explicit_distance and len(ranked_assertion_ids) > 1:
                    ranked_assertion_ids.sort(
                        key=lambda aid: (
                            vector_raw_distances[aid],
                            _assertion_stable_key(aid),
                        )
                    )
                if ranked_assertion_ids:
                    vector_placeholders = ",".join("?" for _ in ranked_assertion_ids)
                    async with self._search_read_connection(_search_db) as db:
                        async with db.execute(
                            "SELECT * FROM v4_assertions "
                            f"WHERE assertion_id IN ({vector_placeholders})",
                            ranked_assertion_ids,
                        ) as cursor:
                            vector_assertions = {
                                str(row["assertion_id"]): dict(row)
                                for row in await cursor.fetchall()
                            }
                    for assertion_id in ranked_assertion_ids:
                        vector_assertion = vector_assertions.get(assertion_id)
                        if vector_assertion is None:
                            continue
                        if (
                            assertion_id in in_scope_assertion_ids
                            and str(vector_assertion["subject_id"])
                            in allowed_entity_ids
                            and assertion_id not in vector_lane
                        ):
                            vector_lane.append(assertion_id)
                vector_lane_diagnostics["candidate_count"] = len(vector_lane)
                if vector_lane:
                    vector_lane_diagnostics["status"] = "active"
                elif not vector_rows and searchable_vector_ids:
                    vector_lane_diagnostics["status"] = "registry_vector_mismatch"
                    logger.warning(
                        "V4_VECTOR_REGISTRY_PHYSICAL_MISMATCH | tenant_id=%s "
                        "agent_id=%s allowed_vector_ids=%s",
                        tenant_id,
                        agent_id,
                        len(allowed_vector_ids),
                    )
                else:
                    vector_lane_diagnostics["status"] = "provenance_mismatch"
                    logger.warning(
                        "V4_VECTOR_PROVENANCE_MISMATCH | tenant_id=%s "
                        "agent_id=%s search_results=%s candidates=%s",
                        tenant_id,
                        agent_id,
                        len(vector_rows),
                        len(vector_lane),
                    )
            except SemanticRuntimeDisabledError:
                vector_lane = []
                vector_lane_diagnostics["status"] = "semantic_runtime_disabled"

        # Phase 4: Structured Query Parsing & Passage/Article Lexical Retrieval
        TURKISH_LEGAL_STOPWORDS = {
            "m",
            "md",
            "madde",
            "maddesi",
            "maddesine",
            "maddesinde",
            "fıkra",
            "fıkrası",
            "bent",
            "bendi",
            "uyarınca",
            "gereğince",
            "göre",
            "ve",
            "veya",
            "ile",
            "için",
            "olan",
            "bir",
            "bu",
            "şu",
            "hükmü",
            "hükmünce",
            "kapsamında",
            "ilgili",
            "nedir",
            "nelerdir",
            "hakkında",
            "tarafından",
            "sayılı",
            "kanun",
            "kanunu",
        }
        raw_tokens = list(
            dict.fromkeys(re.findall(r"\w+", _normalize_identity_text(query)))
        )
        citation_statutes_norm: set[str] = set()
        for c in legal_citations:
            citation_statutes_norm.add(_normalize_identity_text(c.statute_code))
            citation_statutes_norm.add(_normalize_identity_text(c.statute_canonical))
            for a in c.aliases:
                citation_statutes_norm.add(_normalize_identity_text(a))
        citation_articles = {c.article for c in legal_citations if c.article}

        content_tokens: list[str] = []
        for t in raw_tokens:
            if t in TURKISH_LEGAL_STOPWORDS:
                continue
            if t in citation_statutes_norm:
                continue
            if t in citation_articles:
                continue
            if len(t) < 2:
                continue
            content_tokens.append(t)

        # Keep the existing filters/order and substring matching, but bound
        # repeated scans of API-sized candidate text.
        scoring_content_tokens = content_tokens[:64]

        lexical_lane: list[str] = []
        lexical_assertions: dict[str, dict[str, Any]] = {}
        lexical_scores: dict[str, float] = {}
        lexical_entity_lane: list[str] = []

        # 1. Passage/article FTS5 retrieval.  Authorization and assertion
        # lifecycle predicates execute in the same query before BM25 LIMIT.
        fts_query_tokens = list(content_tokens)
        for citation in legal_citations:
            fts_query_tokens.extend(
                re.findall(r"\w+", _normalize_identity_text(citation.statute_code))
            )
            if citation.article:
                fts_query_tokens.append(citation.article)
        if not fts_query_tokens:
            fts_query_tokens = [token for token in raw_tokens if len(token) >= 2]
        fts_query_tokens = list(
            dict.fromkeys(
                token.replace('"', "") for token in fts_query_tokens if len(token) >= 2
            )
        )[:12]
        passage_candidate_rows: list[dict[str, Any]] = []

        if fts_query_tokens:
            fts_terms: list[str] = []
            for token in fts_query_tokens:
                if len(token) >= 6 and not any(ch.isdigit() for ch in token):
                    base = token[:5]
                    suffix = "*"
                else:
                    base = token
                    suffix = ""
                for variant in (base, base.replace("ı", "i")):
                    fts_terms.append(f'"{variant}"{suffix}')
            fts_expression = " OR ".join(dict.fromkeys(fts_terms))
            passage_sql = (
                "SELECT a.*, s.canonical_name AS subject_name, "
                "o.canonical_name AS object_name, "
                f"bm25({scoped_fts}, 0.0, 1.5, 5.0, 3.0, 1.0, 3.0, 2.0, 4.0, 0.0) "
                f"AS bm25_score FROM {scoped_fts} "
                f"JOIN v4_assertions a ON a.rowid = {scoped_fts}.rowid "
                "LEFT JOIN v4_entities s ON s.entity_id = a.subject_id "
                "LEFT JOIN v4_entities o ON o.entity_id = a.object_entity_id "
                f"WHERE {scoped_fts} MATCH ? AND a.tenant_id = ? "
                f"AND a.dataset_id IN ({placeholders}) "
                f"AND {assertion_status_sql} "
                "AND EXISTS (SELECT 1 FROM memory_mutations m "
                "WHERE m.mutation_id = a.mutation_id AND m.agent_id = ? "
                "AND m.state = 'COMMITTED') "
                + (
                    "AND " + " AND ".join(provenance_filters)
                    if provenance_filters
                    else ""
                )
                + " "
                f"ORDER BY bm25_score, {scoped_fts}.stable_key, a.assertion_id LIMIT ?"
            )
            async with self._search_read_connection(_search_db) as db:
                if not scope.get("fts_built"):
                    # WHERE filters do not scope FTS5's corpus statistics. Build a
                    # connection-local index of the authorized corpus so foreign
                    # rows cannot change scores, rank or the pre-rank LIMIT. The
                    # cache retains it only for this scope and main data_version.
                    await db.execute(
                        f"CREATE VIRTUAL TABLE temp.{scoped_fts} USING fts5("
                        "assertion_id UNINDEXED, predicate, evidence_span, literal_value, "
                        "source_ref, subject_name, object_name, chunk_payload, "
                        "stable_key UNINDEXED, "
                        "tokenize='unicode61 remove_diacritics 2', prefix='5')"
                    )
                    await db.execute(
                        f"INSERT INTO temp.{scoped_fts} "
                        "(rowid, assertion_id, predicate, evidence_span, literal_value, "
                        "source_ref, subject_name, object_name, chunk_payload, stable_key) "
                        "SELECT f.rowid, f.assertion_id, f.predicate, f.evidence_span, "
                        "f.literal_value, f.source_ref, "
                        "CASE WHEN f.subject_name IN ("
                        "scoped.document_id, scoped.revision_id, scoped.chunk_id, "
                        "scoped.assertion_id, scoped.mutation_id, scoped.subject_id, "
                        "COALESCE(scoped.object_entity_id, '')) "
                        "OR f.subject_name LIKE 'mesa-document-%' "
                        "OR f.subject_name LIKE 'mesa-revision-%' "
                        "OR f.subject_name LIKE 'mesa-chunk-%' "
                        "OR f.subject_name LIKE 'mesa-tenant-%' "
                        "OR f.subject_name LIKE 'mesa-workspace-%' "
                        "OR f.subject_name LIKE 'mesa-dataset-%' "
                        "OR f.subject_name LIKE 'mesa-session-%' "
                        "THEN '' ELSE f.subject_name END, "
                        "CASE WHEN f.object_name IN ("
                        "scoped.document_id, scoped.revision_id, scoped.chunk_id, "
                        "scoped.assertion_id, scoped.mutation_id, scoped.subject_id, "
                        "COALESCE(scoped.object_entity_id, '')) "
                        "OR f.object_name LIKE 'mesa-document-%' "
                        "OR f.object_name LIKE 'mesa-revision-%' "
                        "OR f.object_name LIKE 'mesa-chunk-%' "
                        "OR f.object_name LIKE 'mesa-tenant-%' "
                        "OR f.object_name LIKE 'mesa-workspace-%' "
                        "OR f.object_name LIKE 'mesa-dataset-%' "
                        "OR f.object_name LIKE 'mesa-session-%' "
                        "THEN '' ELSE f.object_name END, "
                        "f.chunk_payload, "
                        "COALESCE(ci_doc.external_id, scoped.document_id) || char(0) || "
                        "printf('%010d', COALESCE(r.revision_number, 0)) || char(0) || "
                        "printf('%010d', COALESCE(c.ordinal, 0)) || char(0) || "
                        "COALESCE(ci_chunk.external_id, scoped.chunk_id) || char(0) || "
                        "COALESCE(scoped.predicate, '') "
                        "FROM v4_assertions_fts f "
                        f"JOIN ({in_scope_assertion_query}) scoped "
                        "ON scoped.assertion_id = f.assertion_id "
                        "LEFT JOIN source_chunks c ON c.chunk_id = scoped.chunk_id "
                        "LEFT JOIN document_revisions r ON r.revision_id = scoped.revision_id "
                        "LEFT JOIN v4_catalog_identities ci_doc "
                        "ON ci_doc.kind = 'document' AND ci_doc.physical_id = scoped.document_id "
                        "LEFT JOIN v4_catalog_identities ci_chunk "
                        "ON ci_chunk.kind = 'chunk' AND ci_chunk.physical_id = scoped.chunk_id",
                        (tenant_id, *datasets, agent_id, *provenance_params),
                    )
                    scope["fts_built"] = True
                async with db.execute(
                    passage_sql,
                    (
                        fts_expression,
                        tenant_id,
                        *datasets,
                        agent_id,
                        *provenance_params,
                        min(1000, max(limit * 25, 100)),
                    ),
                ) as cursor:
                    passage_candidate_rows = [
                        dict(row) for row in await cursor.fetchall()
                    ]

            # Trusted typed legal identity participates in candidate generation
            # before lane ranking.  It is sourced only from canonical assertion
            # columns; arbitrary free-form metadata and bare article text never
            # enter this path.
            passage_ids = {str(row["assertion_id"]) for row in passage_candidate_rows}
            for assertion_id in sorted(
                typed_legal_candidate_ids - passage_ids, key=_assertion_stable_key
            ):
                typed_row = dict(in_scope_assertions[assertion_id])
                typed_row["subject_name"] = ""
                typed_row["object_name"] = ""
                typed_row["bm25_score"] = 0.0
                passage_candidate_rows.append(typed_row)

            scored_passage_candidates: list[tuple[float, str, dict[str, Any]]] = []
            scoring_deadline = time.monotonic() + 0.002
            for p_row in passage_candidate_rows:
                aid = str(p_row["assertion_id"])
                features = text_features.setdefault(
                    (aid, p_row.get("subject_name"), p_row.get("object_name")), {}
                )
                if "passage_text" not in features:
                    p_subject_name = _scoring_entity_name(
                        p_row.get("subject_name"), p_row
                    )
                    p_object_name = _scoring_entity_name(
                        p_row.get("object_name"), p_row
                    )
                    p_raw_text = (
                        f"{p_subject_name} {p_row.get('source_ref') or ''} "
                        f"{p_row.get('literal_value') or ''} {p_row.get('evidence_span') or ''} "
                        f"{p_row.get('predicate') or ''} {p_object_name}"
                    )
                    features["passage_raw_text"] = p_raw_text
                    features["passage_text"] = _normalize_identity_text(p_raw_text)
                p_raw_text = features["passage_raw_text"]
                p_text = features["passage_text"]
                # SQLite BM25 is lower-is-better (usually negative).  Preserve
                # it verbatim for observability while using its negation as a
                # small relevance component in the combined lexical order.
                raw_bm25 = float(p_row.get("bm25_score") or 0.0)
                p_score = -raw_bm25

                matched_tokens = 0
                for token in scoring_content_tokens:
                    if token in p_text:
                        matched_tokens += 1
                        p_score += 10.0 + min(len(token), 10)
                if scoring_content_tokens and matched_tokens == len(
                    scoring_content_tokens
                ):
                    p_score += 50.0

                clean_query = " ".join(scoring_content_tokens)
                if clean_query and clean_query in p_text:
                    p_score += 100.0

                if legal_citations:
                    typed_identity = _assertion_legal_identity(p_row)
                    if typed_identity is not None:
                        for citation in legal_citations:
                            if (
                                citation.article
                                and typed_identity["statute_code"]
                                == citation.statute_code
                                and typed_identity["article_id"] == citation.article
                            ):
                                p_score += 200.0
                            elif (
                                typed_identity["statute_code"] == citation.statute_code
                            ):
                                p_score += 40.0
                    else:
                        if "passage_citations" not in features:
                            features["passage_citations"] = (
                                await asyncio.to_thread(
                                    legal_resolver.extract_citations, p_raw_text
                                )
                            )
                        row_citations = features["passage_citations"]
                        row_identities = {
                            (c.statute_code, c.article) for c in row_citations
                        }
                        row_statutes = {c.statute_code for c in row_citations}
                        for c in legal_citations:
                            if (
                                c.article
                                and (
                                    c.statute_code,
                                    c.article,
                                )
                                in row_identities
                            ):
                                p_score += 200.0
                            elif c.statute_code in row_statutes:
                                p_score += 40.0
                        if row_statutes & (legal_resolver.laws - target_statutes):
                            p_score -= 150.0

                scored_passage_candidates.append((p_score, aid, p_row))
                if time.monotonic() >= scoring_deadline:
                    # Let threaded I/O finish between CPU slices. sleep(0)
                    # requeues scoring ahead of each /health SQLite await.
                    await asyncio.sleep(0.001)
                    scoring_deadline = time.monotonic() + 0.002

            scored_passage_candidates.sort(
                key=lambda x: (-x[0], _assertion_stable_key(x[1]))
            )
            for score, aid, p_row in scored_passage_candidates:
                if (
                    str(p_row["subject_id"]) in allowed_entity_ids
                    and aid not in lexical_lane
                ):
                    lexical_lane.append(aid)
                    lexical_assertions[aid] = p_row
                    lexical_scores[aid] = float(p_row.get("bm25_score") or 0.0)

        # 2. Entity FTS5 Retrieval with clean content tokens (anti-pattern fix: no stopwords / OR pollution)
        fts_tokens = content_tokens if content_tokens else raw_tokens
        if fts_tokens:
            clean_fts_tokens = [t.replace('"', "") for t in fts_tokens if len(t) >= 2][
                :8
            ]
            if clean_fts_tokens:
                variants = dict.fromkeys(
                    variant
                    for token in clean_fts_tokens
                    for variant in (token, token.replace("ı", "i"))
                )
                expression = " OR ".join(f'"{t}"' for t in variants)
                async with self._search_read_connection(_search_db) as db:
                    async with db.execute(
                        build_v4_lexical_query(dataset_count=len(datasets)),
                        (
                            expression,
                            tenant_id,
                            agent_id,
                            *datasets,
                            min(500, max(limit * 10, 50)),
                        ),
                    ) as cursor:
                        for row in await cursor.fetchall():
                            eid = str(row[0])
                            if (
                                eid in allowed_entity_ids
                                and eid not in lexical_entity_lane
                            ):
                                lexical_entity_lane.append(eid)

        # Assertion Lane Redesign (Phase 5)
        tokens_to_match = (content_tokens if content_tokens else raw_tokens)[:64]
        # Reuse the scoped FTS candidate pool.  This avoids admitting rows
        # through SQLite's ASCII-only LIKE casing before Turkish-aware scoring.
        assertion_rows = passage_candidate_rows

        # Composite multi-signal scoring for assertion candidates
        query_norm = _normalize_identity_text(query)
        scored_assertions: list[tuple[float, str, dict[str, Any]]] = []
        scoring_deadline = time.monotonic() + 0.002

        for a_row in assertion_rows:
            aid = str(a_row["assertion_id"])
            if str(a_row["subject_id"]) not in allowed_entity_ids:
                continue

            score = 0.0
            features = text_features.setdefault(
                (aid, a_row.get("subject_name"), a_row.get("object_name")), {}
            )
            if "assertion_text" not in features:
                pred_raw = str(a_row.get("predicate") or "").lower()
                pred_norm = _normalize_identity_text(pred_raw)
                s_name_norm = _normalize_identity_text(
                    _scoring_entity_name(a_row.get("subject_name"), a_row)
                )
                o_name_norm = _normalize_identity_text(
                    _scoring_entity_name(a_row.get("object_name"), a_row)
                )
                lit_norm = _normalize_identity_text(
                    str(a_row.get("literal_value") or "")
                )
                ev_norm = _normalize_identity_text(
                    str(a_row.get("evidence_span") or "")
                )
                combined_text = (
                    f"{pred_norm} {s_name_norm} {o_name_norm} {lit_norm} {ev_norm}"
                )
                features["assertion_text"] = (
                    pred_norm,
                    s_name_norm,
                    o_name_norm,
                    lit_norm,
                    ev_norm,
                    combined_text,
                )
            pred_norm, s_name_norm, o_name_norm, lit_norm, ev_norm, combined_text = (
                features["assertion_text"]
            )

            # 1. Predicate Match
            if pred_norm and pred_norm in query_norm:
                score += 50.0
            elif any(
                t in pred_norm or pred_norm in t for t in tokens_to_match if len(t) >= 3
            ):
                score += 35.0

            # 2. Subject Match
            if any(t in s_name_norm for t in tokens_to_match if len(t) >= 2):
                score += 25.0

            # 3. Object / Literal Match
            if any(
                t in o_name_norm or t in lit_norm
                for t in tokens_to_match
                if len(t) >= 2
            ):
                score += 25.0

            # 4. Evidence Match
            if any(t in ev_norm for t in tokens_to_match if len(t) >= 2):
                score += 15.0

            # 5. Token Coverage Bonus
            if tokens_to_match:
                match_count = sum(1 for t in tokens_to_match if t in combined_text)
                coverage = match_count / len(tokens_to_match)
                score += coverage * 40.0

            # 6. Match complete statute/article identities, never free numbers
            # or short aliases inside unrelated words.
            if legal_citations:
                typed_identity = _assertion_legal_identity(a_row)
                if typed_identity is not None:
                    for citation in legal_citations:
                        if (
                            citation.article
                            and typed_identity["statute_code"] == citation.statute_code
                            and typed_identity["article_id"] == citation.article
                        ):
                            score += 150.0
                        elif typed_identity["statute_code"] == citation.statute_code:
                            score += 40.0
                else:
                    if "assertion_citations" not in features:
                        features["assertion_citations"] = (
                            await asyncio.to_thread(
                                legal_resolver.extract_citations,
                                " ".join(
                                    (
                                        str(a_row.get("predicate") or ""),
                                        _scoring_entity_name(
                                            a_row.get("subject_name"), a_row
                                        ),
                                        _scoring_entity_name(
                                            a_row.get("object_name"), a_row
                                        ),
                                        str(a_row.get("literal_value") or ""),
                                        str(a_row.get("evidence_span") or ""),
                                        str(a_row.get("source_ref") or ""),
                                    )
                                ),
                            )
                        )
                    row_citations = features["assertion_citations"]
                    row_identities = {
                        (c.statute_code, c.article) for c in row_citations
                    }
                    row_statutes = {c.statute_code for c in row_citations}
                    for c in legal_citations:
                        if (
                            c.article
                            and (
                                c.statute_code,
                                c.article,
                            )
                            in row_identities
                        ):
                            score += 150.0
                        elif c.statute_code in row_statutes:
                            score += 40.0
                    if row_statutes & (legal_resolver.laws - target_statutes):
                        score -= 150.0

            # 7. Confidence Scaling
            raw_conf = a_row.get("confidence")
            confidence = float(raw_conf) if raw_conf is not None else 1.0
            score *= max(0.5, min(1.0, confidence))
            score = max(score, float(raw_conf) if raw_conf is not None else 0.1)

            scored_assertions.append((score, aid, a_row))
            if time.monotonic() >= scoring_deadline:
                await asyncio.sleep(0.001)
                scoring_deadline = time.monotonic() + 0.002

        scored_assertions.sort(
            key=lambda x: (
                -x[0],
                -float(
                    x[2]["confidence"] if x[2].get("confidence") is not None else 0.0
                ),
                _assertion_stable_key(x[1]),
            )
        )

        assertion_lane: list[str] = []
        assertion_records: dict[str, dict[str, Any]] = {}
        assertion_scores: dict[str, float] = {}
        for score, aid, a_row in scored_assertions:
            if aid not in assertion_lane:
                assertion_lane.append(aid)
                assertion_records[aid] = a_row
                assertion_scores[aid] = score
                if len(assertion_lane) >= limit:
                    break

        # Real Kùzu Graph V2 Traversal Lane
        graph_lane: list[str] = []
        graph_evidence_by_candidate: dict[str, dict[str, Any]] = {}
        allowed_graph_assertion_ids = in_scope_assertion_ids
        graph_provider = self._graph
        effective_graph = (
            graph_enabled
            and graph_provider is not None
            and self.graph_implementation_available
            and bool(allowed_graph_assertion_ids)
        )
        if effective_graph:
            seed_scores: dict[str, float] = {}

            # 1. Legal citation entity seeds (high relevance)
            if legal_citations and allowed_entity_ids:
                legal_entity_names = set(legal_resolver.extract_entities(query))
                for c in legal_citations:
                    legal_entity_names.add(c.statute_code)
                    legal_entity_names.add(c.statute_canonical)
                    if c.article:
                        legal_entity_names.add(f"{c.statute_code} {c.article}")
                        legal_entity_names.add(f"{c.statute_code} m.{c.article}")
                norm_legal_names = {
                    _normalize_identity_text(n) for n in legal_entity_names
                }
                async with self._search_read_connection(_search_db) as db:
                    placeholders_ae = ",".join("?" for _ in allowed_entity_ids)
                    async with db.execute(
                        f"SELECT entity_id, normalized_name FROM v4_entities "
                        f"WHERE tenant_id = ? AND entity_id IN ({placeholders_ae})",
                        (tenant_id, *allowed_entity_ids),
                    ) as cursor:
                        for row in await cursor.fetchall():
                            eid = str(row["entity_id"])
                            nname = str(row["normalized_name"] or "")
                            if nname in norm_legal_names:
                                seed_scores[eid] = 1.0

            # 2. Query text direct entity matches in allowed_entity_ids
            if allowed_entity_ids:
                clean_query_norm = _normalize_identity_text(query).strip()
                async with self._search_read_connection(_search_db) as db:
                    placeholders_ae = ",".join("?" for _ in allowed_entity_ids)
                    async with db.execute(
                        f"SELECT entity_id, normalized_name FROM v4_entities "
                        f"WHERE tenant_id = ? AND entity_id IN ({placeholders_ae})",
                        (tenant_id, *allowed_entity_ids),
                    ) as cursor:
                        for row in await cursor.fetchall():
                            eid = str(row["entity_id"])
                            nname = str(row["normalized_name"] or "")
                            if nname and (
                                nname in clean_query_norm or clean_query_norm in nname
                            ):
                                seed_scores[eid] = max(seed_scores.get(eid, 0.0), 0.95)

            # 3. Top vector lane assertions: subjects and objects
            for rank, aid in enumerate(vector_lane[:8], start=1):
                cand_assertion = vector_assertions.get(aid)
                if cand_assertion:
                    s_id = str(cand_assertion.get("subject_id") or "")
                    o_id = str(cand_assertion.get("object_entity_id") or "")
                    v_rel = max(0.4, 0.9 - 0.05 * rank)
                    if s_id in allowed_entity_ids:
                        seed_scores[s_id] = max(seed_scores.get(s_id, 0.0), v_rel)
                    if o_id in allowed_entity_ids:
                        seed_scores[o_id] = max(seed_scores.get(o_id, 0.0), v_rel * 0.9)

            # 4. Top lexical evidence plus entity-name-only graph seeds.  Entity
            # name hits are deliberately not fused as assertion evidence.
            for rank, hit_id in enumerate(lexical_lane[:8], start=1):
                l_rel = max(0.35, 0.85 - 0.05 * rank)
                if hit_id in lexical_assertions:
                    ass = lexical_assertions[hit_id]
                    s_id = str(ass.get("subject_id") or "")
                    o_id = str(ass.get("object_entity_id") or "")
                    if s_id in allowed_entity_ids:
                        seed_scores[s_id] = max(seed_scores.get(s_id, 0.0), l_rel)
                    if o_id in allowed_entity_ids:
                        seed_scores[o_id] = max(seed_scores.get(o_id, 0.0), l_rel * 0.9)
            for hit_id in lexical_entity_lane[:8]:
                seed_scores[hit_id] = max(seed_scores.get(hit_id, 0.0), 0.9)

            graph_seed_ids = sorted(
                seed_scores, key=lambda sid: (-seed_scores[sid], sid)
            )
        else:
            graph_seed_ids = []

        if effective_graph and graph_seed_ids and graph_provider is not None:
            if not self.graph_operational:
                raise GraphSearchError("configured graph backend is unavailable")
            graph_legal_options: dict[str, Any] = {}
            if not self._legal_rules_enabled:
                graph_legal_options["legal_rules_enabled"] = False
            try:
                graph_hits = await graph_provider.search_v4_graph(
                    agent_id=agent_id,
                    seed_entity_ids=graph_seed_ids[:20],
                    allowed_entity_ids=allowed_entity_ids,
                    allowed_assertion_ids=allowed_graph_assertion_ids,
                    assertion_evidence={
                        aid: str(assertion.get("evidence_span") or "")
                        for aid, assertion in in_scope_assertions.items()
                    },
                    assertion_sort_keys=assertion_stable_keys,
                    max_hops=3,
                    limit=min(500, max(limit * 10, 50)),
                    query=query,
                    seed_scores=seed_scores,
                    **graph_legal_options,
                )
                seen_graph_paths: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
                for hit in graph_hits:
                    target_entity_id = str(hit.get("entity_id") or "")
                    if target_entity_id not in allowed_entity_ids:
                        continue
                    paths = hit.get("paths") or [
                        {
                            "assertion_ids": hit.get(
                                "best_path_assertion_ids",
                                hit.get("path_assertion_ids", []),
                            ),
                            "entity_ids": hit.get("path_entity_ids", []),
                            "seed_id": hit.get("seed_id", ""),
                            "score": hit.get("score", 0.0),
                        }
                    ]
                    for path in paths:
                        path_aids = [str(aid) for aid in path.get("assertion_ids", [])]
                        path_entities = [str(eid) for eid in path.get("entity_ids", [])]
                        # Reject an incomplete proof; never trim unauthorized edges
                        # into an apparently valid shorter path.
                        if not path_aids or any(
                            aid not in allowed_graph_assertion_ids for aid in path_aids
                        ):
                            continue
                        if len(path_entities) != len(path_aids) + 1 or any(
                            eid not in allowed_entity_ids for eid in path_entities
                        ):
                            continue
                        directions: list[str] = []
                        predicates: list[str] = []
                        for idx, aid in enumerate(path_aids):
                            assertion = in_scope_assertions[aid]
                            endpoints = (
                                str(assertion["subject_id"]),
                                str(assertion["object_entity_id"]),
                            )
                            traversal = (path_entities[idx], path_entities[idx + 1])
                            if endpoints == traversal:
                                directions.append("forward")
                            elif endpoints == traversal[::-1]:
                                directions.append("reverse")
                            else:
                                break
                            predicates.append(str(assertion["predicate"]))
                        if len(directions) != len(path_aids):
                            continue
                        cand_id = path_aids[-1]
                        evidence = graph_evidence_by_candidate.setdefault(
                            cand_id,
                            {
                                "matched_assertion_id": cand_id,
                                "graph_paths": [],
                            },
                        )
                        normalized_path = {
                            "assertion_ids": path_aids,
                            "entity_ids": path_entities,
                            "edge_directions": directions,
                            "predicates": predicates,
                            "seed_id": path_entities[0],
                            "score": float(path.get("score") or 0.0),
                        }
                        normalized_path["graph_path_id"] = stable_graph_path_id(
                            assertion_ids=path_aids,
                            entity_ids=path_entities,
                            edge_directions=directions,
                            predicates=predicates,
                        )
                        path_key = (tuple(path_entities), tuple(path_aids))
                        if path_key not in seen_graph_paths:
                            seen_graph_paths.add(path_key)
                            evidence["graph_paths"].append(normalized_path)

                for cand_id, evidence in graph_evidence_by_candidate.items():
                    paths = evidence["graph_paths"]
                    paths.sort(
                        key=lambda p: (
                            -p["score"],
                            len(p["assertion_ids"]),
                            tuple(p["entity_ids"]),
                            tuple(
                                _assertion_stable_key(aid)
                                for aid in p["assertion_ids"]
                            ),
                        )
                    )
                    best = paths[0]
                    seeds = sorted({p["seed_id"] for p in paths})
                    targets = sorted({p["entity_ids"][-1] for p in paths})
                    support_bonus = min(
                        0.5, 0.12 * (len(paths) - 1) + 0.08 * (len(seeds) - 1)
                    )
                    evidence.update(
                        {
                            "graph_hop_count": len(best["assertion_ids"]),
                            "graph_seed_entity_id": best["seed_id"],
                            "graph_seed_entity_ids": seeds,
                            "graph_distinct_seed_count": len(seeds),
                            "graph_target_entity_id": best["entity_ids"][-1],
                            "graph_target_entity_ids": targets,
                            "graph_path_assertion_ids": list(best["assertion_ids"]),
                            "graph_path_entity_ids": list(best["entity_ids"]),
                            "graph_path_id": best["graph_path_id"],
                            "graph_edge_directions": list(best["edge_directions"]),
                            "graph_predicates": list(best["predicates"]),
                            "graph_direction": (
                                best["edge_directions"][0]
                                if len(set(best["edge_directions"])) == 1
                                else "mixed"
                            ),
                            "graph_support_count": len(paths),
                            "graph_score": best["score"] * (1.0 + support_bonus),
                            "graph_supporting_assertion_ids": list(
                                dict.fromkeys(
                                    aid for p in paths for aid in p["assertion_ids"]
                                )
                            ),
                        }
                    )
                graph_lane = sorted(
                    graph_evidence_by_candidate,
                    key=lambda aid: (
                        -graph_evidence_by_candidate[aid]["graph_score"],
                        _assertion_stable_key(aid),
                    ),
                )
            except GraphSearchError as exc:
                logger.warning(
                    "V4_GRAPH_RETRIEVAL_DEGRADED | agent_id=%s seeds=%s error=%s",
                    agent_id,
                    graph_seed_ids[:20],
                    exc,
                )
                raise

        # Internal candidate aggregation and fusion
        candidates: dict[str, dict[str, Any]] = {}
        candidate_assertions = dict(in_scope_assertions)
        candidate_assertions.update(vector_assertions)
        candidate_assertions.update(lexical_assertions)
        candidate_assertions.update(assertion_records)
        lane_rankings = {
            "vector": vector_lane,
            "bm25": lexical_lane,
            "assertion": assertion_lane,
            "graph": graph_lane,
        }
        lane_rankings["assertion"] = assertion_lane + [
            aid for aid in exact_ids if aid not in assertion_lane
        ]
        for candidate_id, fused_score, lane_ranks in rrf_fuse_lanes(
            lane_rankings,
            k=effective_k,
            weights=effective_weights,
            tie_break_key=_assertion_stable_key,
        ):
            fused_assertion = candidate_assertions.get(candidate_id)
            if fused_assertion is None:
                continue
            raw_scores: dict[str, float] = {}
            if candidate_id in vector_raw_distances:
                raw_scores["vector"] = vector_raw_distances[candidate_id]
            if candidate_id in lexical_scores:
                raw_scores["bm25"] = lexical_scores[candidate_id]
            if candidate_id in assertion_scores:
                raw_scores["assertion"] = assertion_scores[candidate_id]
            graph_evidence = graph_evidence_by_candidate.get(candidate_id, {})
            assertion_id = candidate_id
            origins_set = set(lane_ranks)
            if origins_set - {"graph"}:
                cand_entity_id = str(
                    fused_assertion.get("subject_id")
                    or graph_evidence.get("graph_target_entity_id")
                    or ""
                )
            else:
                cand_entity_id = str(
                    graph_evidence.get("graph_target_entity_id")
                    or fused_assertion.get("subject_id")
                    or ""
                )
            candidates[candidate_id] = {
                "candidate_id": candidate_id,
                "assertion_id": assertion_id,
                "entity_id": cand_entity_id,
                "assertion": fused_assertion,
                "source_chunk_id": str(fused_assertion.get("chunk_id") or "") or None,
                "document_id": str(fused_assertion.get("document_id") or "") or None,
                "evidence_span": str(fused_assertion.get("evidence_span") or "")
                or None,
                "origins": origins_set,
                "lane_ranks": lane_ranks,
                "raw_scores": raw_scores,
                "vector_artifact": (
                    vector_registry_artifacts.get(candidate_id)
                    if "vector" in origins_set
                    else None
                ),
                "supporting_evidence_ids": list(
                    graph_evidence.get("graph_supporting_assertion_ids", [])
                ),
                "graph_evidence": graph_evidence,
                "rrf_score": fused_score,
                "legal_factor": 1.0,
                "materialized_provenance": [],
            }

        if not candidates:
            return []

        cand_entity_ids: set[str] = {
            str(candidate["entity_id"])
            for candidate in candidates.values()
            if candidate.get("entity_id")
        }
        for candidate in candidates.values():
            candidate_assertion = cast(
                dict[str, Any] | None, candidate.get("assertion")
            )
            if candidate_assertion is not None:
                if candidate_assertion.get("subject_id"):
                    cand_entity_ids.add(str(candidate_assertion["subject_id"]))
                if candidate_assertion.get("object_entity_id"):
                    cand_entity_ids.add(str(candidate_assertion["object_entity_id"]))
            for supporting_id in candidate.get("supporting_evidence_ids", []):
                supporting = in_scope_assertions.get(str(supporting_id))
                if supporting and supporting.get("subject_id"):
                    cand_entity_ids.add(str(supporting["subject_id"]))
                if supporting and supporting.get("object_entity_id"):
                    cand_entity_ids.add(str(supporting["object_entity_id"]))
        cand_entity_names: dict[str, str] = {}
        if cand_entity_ids:
            ce_placeholders = ",".join("?" for _ in cand_entity_ids)
            async with self._search_read_connection(_search_db) as db:
                async with db.execute(
                    f"SELECT entity_id, canonical_name FROM v4_entities "
                    f"WHERE tenant_id = ? AND entity_id IN ({ce_placeholders})",
                    (tenant_id, *cand_entity_ids),
                ) as cursor:
                    for row in await cursor.fetchall():
                        cand_entity_names[str(row["entity_id"])] = str(
                            row["canonical_name"] or ""
                        )

        for cand in candidates.values():
            if legal_citations:
                ass = cand["assertion"]
                typed_identity = (
                    _assertion_legal_identity(ass) if ass is not None else None
                )
                if typed_identity is not None:
                    target_statute_match = any(
                        typed_identity["statute_code"] == citation.statute_code
                        for citation in legal_citations
                    )
                    target_article_match = any(
                        citation.article
                        and typed_identity["statute_code"] == citation.statute_code
                        and typed_identity["article_id"] == citation.article
                        for citation in legal_citations
                    )
                    competing_statute_match = not target_statute_match
                    if target_article_match:
                        cand["legal_factor"] = 1.5
                    elif target_statute_match:
                        cand["legal_factor"] = 1.25
                    elif competing_statute_match:
                        cand["legal_factor"] = 0.25
                    continue
                cand_texts: list[str] = []
                if ass is not None:
                    cand_texts.extend(
                        [
                            str(ass.get("source_ref") or ""),
                            str(ass.get("predicate") or ""),
                            str(ass.get("literal_value") or ""),
                            str(ass.get("evidence_span") or ""),
                        ]
                    )
                    sub_name = _scoring_entity_name(
                        cand_entity_names.get(str(ass.get("subject_id") or ""), ""),
                        ass,
                    )
                    if sub_name:
                        cand_texts.append(sub_name)
                    obj_name = _scoring_entity_name(
                        cand_entity_names.get(
                            str(ass.get("object_entity_id") or ""), ""
                        ),
                        ass,
                    )
                    if obj_name:
                        cand_texts.append(obj_name)
                eid = str(cand.get("entity_id") or "")
                if eid and eid in cand_entity_names:
                    ent_name = _scoring_entity_name(cand_entity_names[eid], ass)
                    if ent_name:
                        cand_texts.append(ent_name)

                citation_key = (
                    "candidate_citations",
                    cand["assertion_id"],
                    tuple(cand_texts),
                )
                if citation_key not in text_features:
                    text_features[citation_key] = await asyncio.to_thread(
                        legal_resolver.extract_citations,
                        " ".join(cand_texts)
                    )
                candidate_citations = text_features[citation_key]
                candidate_identities = {
                    (c.statute_code, c.article) for c in candidate_citations
                }
                candidate_statutes = {c.statute_code for c in candidate_citations}
                target_statute_match = bool(target_statutes & candidate_statutes)
                target_article_match = any(
                    c.article and (c.statute_code, c.article) in candidate_identities
                    for c in legal_citations
                )
                competing_statute_match = bool(
                    candidate_statutes & (legal_resolver.laws - target_statutes)
                )

                if target_statute_match and target_article_match:
                    cand["legal_factor"] = 1.5
                elif target_statute_match:
                    cand["legal_factor"] = 1.25
                elif competing_statute_match and not target_statute_match:
                    cand["legal_factor"] = 0.25
                else:
                    cand["legal_factor"] = 1.0
            else:
                # No query legal citations: no query-independent boost allowed
                cand["legal_factor"] = 1.0

        ordered_candidates = sorted(
            candidates.values(),
            key=lambda c: (
                -(c["rrf_score"] * c["legal_factor"]),
                _assertion_stable_key(c["candidate_id"]),
            ),
        )
        exact_candidates = [candidates[aid] for aid in exact_ids if aid in candidates]
        ordered_candidates = (
            exact_candidates
            + [c for c in ordered_candidates if c["candidate_id"] not in exact_ids]
        )[:limit]

        needed_entity_ids = sorted(
            {
                c["entity_id"]
                for c in ordered_candidates
                if c["entity_id"] in allowed_entity_ids
            }
        )
        entities: dict[str, dict[str, Any]] = {}
        if needed_entity_ids:
            entity_placeholders = ",".join("?" for _ in needed_entity_ids)
            async with self._search_read_connection(_search_db) as db:
                async with db.execute(
                    f"SELECT * FROM v4_entities WHERE tenant_id = ? "
                    f"AND status = 'ACTIVE' AND entity_id IN ({entity_placeholders})",
                    (tenant_id, *needed_entity_ids),
                ) as cursor:
                    entities = {
                        str(row["entity_id"]): dict(row)
                        for row in await cursor.fetchall()
                    }

        # Materialize assertions only for the ordered candidates
        extra_assertion_ids: set[str] = set()
        for cand in ordered_candidates:
            for p_aid in cand["supporting_evidence_ids"]:
                if p_aid != cand["assertion_id"]:
                    extra_assertion_ids.add(p_aid)

        extra_assertions = {
            aid: in_scope_assertions[aid]
            for aid in extra_assertion_ids
            if aid in in_scope_assertions
        }

        all_materialized_assertions: list[dict[str, Any]] = []
        for cand in ordered_candidates:
            if cand["assertion"] is not None:
                provenance_ids = [
                    cand["assertion_id"],
                    *cand["supporting_evidence_ids"],
                ]
                provenance_by_id = {
                    **extra_assertions,
                    cand["assertion_id"]: dict(cand["assertion"]),
                }
                graph_evidence = cand.get("graph_evidence", {})
                best_directions = dict(
                    zip(
                        graph_evidence.get("graph_path_assertion_ids", []),
                        graph_evidence.get("graph_edge_directions", []),
                    )
                )
                for provenance_id in dict.fromkeys(provenance_ids):
                    source_assertion = provenance_by_id.get(provenance_id)
                    if source_assertion is None:
                        continue
                    matched_assertion = dict(source_assertion)
                    if not self._legal_rules_enabled:
                        # Hide stored overlays in the returned copy; canonical
                        # identities survive switch changes and upload resumes.
                        for field in (
                            "law_id", "statute_code", "article_id", "paragraph_id",
                            "clause_id", "legal_version_id", "legal_identity_source",
                        ):
                            matched_assertion[field] = ""
                    subject_id = str(matched_assertion.get("subject_id") or "")
                    object_id = str(matched_assertion.get("object_entity_id") or "")
                    if subject_id in cand_entity_names:
                        matched_assertion["subject_name"] = cand_entity_names[
                            subject_id
                        ]
                    if object_id in cand_entity_names:
                        matched_assertion["object_name"] = cand_entity_names[object_id]
                    # An assertion retains its true subject/object. Traversal
                    # directions belong to individual paths, not their union.
                    matched_assertion["direction"] = best_directions.get(
                        provenance_id, "asserted"
                    )
                    cand["materialized_provenance"].append(matched_assertion)
            all_materialized_assertions.extend(cand["materialized_provenance"])

        mutation_ids = sorted(
            {
                str(a["mutation_id"])
                for a in all_materialized_assertions
                if a.get("mutation_id")
            }
        )
        metadata_by_mutation: dict[str, dict[str, Any]] = {}
        legal_items_by_mutation: dict[str, dict[str, Any]] = {}
        rerank_texts: dict[str, str] = {}
        async with self._search_read_connection(_search_db) as db:
            if mutation_ids:
                mutation_placeholders = ",".join("?" for _ in mutation_ids)
                async with db.execute(
                    "SELECT mutation_id, metadata_json"
                    + (", content_payload" if self._reranker is not None else "")
                    + " FROM memory_mutations "
                    f"WHERE mutation_id IN ({mutation_placeholders})",
                    mutation_ids,
                ) as cursor:
                    for row in await cursor.fetchall():
                        if self._reranker is not None:
                            rerank_texts[str(row["mutation_id"])] = str(row["content_payload"])
                        try:
                            metadata = json.loads(str(row["metadata_json"] or "{}"))
                        except (TypeError, json.JSONDecodeError):
                            metadata = {}
                        if isinstance(metadata, dict):
                            item_set = metadata.get("_mesa_legal_items")
                            if self._legal_rules_enabled and isinstance(item_set, dict):
                                legal_items_by_mutation[str(row["mutation_id"])] = item_set
                            metadata_by_mutation[str(row["mutation_id"])] = {
                                key: value
                                for key, value in metadata.items()
                                if not str(key).startswith("_mesa_")
                            }
            catalog_fields = (
                ("dataset_id", "dataset"),
                ("document_id", "document"),
                ("revision_id", "revision"),
                ("chunk_id", "chunk"),
            )
            physical_ids = sorted(
                {
                    str(assertion[field])
                    for assertion in all_materialized_assertions
                    for field, _ in catalog_fields
                    if assertion.get(field)
                }
            )
            external_ids: dict[tuple[str, str], str] = {}
            # Resolve the selected proof set in bounded batches, not four
            # additional SQL round trips for each occurrence of an assertion.
            for offset in range(0, len(physical_ids), 500):
                batch = physical_ids[offset : offset + 500]
                batch_placeholders = ",".join("?" for _ in batch)
                async with db.execute(
                    "SELECT kind, physical_id, external_id FROM v4_catalog_identities "
                    f"WHERE tenant_id = ? AND physical_id IN ({batch_placeholders})",
                    (tenant_id, *batch),
                ) as cursor:
                    for row in await cursor.fetchall():
                        external_ids[(str(row[0]), str(row[1]))] = str(row[2])
            for assertion in all_materialized_assertions:
                metadata = metadata_by_mutation.get(str(assertion.get("mutation_id")))
                if metadata:
                    assertion["metadata"] = metadata
                for field, kind in catalog_fields:
                    physical_id = assertion.get(field)
                    if physical_id:
                        assertion[field] = external_ids.get((kind, str(physical_id)))

        for cand in ordered_candidates:
            if cand["materialized_provenance"]:
                first_prov = cand["materialized_provenance"][0]
                cand["source_chunk_id"] = first_prov.get("chunk_id")
                # Materialized provenance has already crossed the catalog
                # boundary above.  Never retain the earlier physical ID that
                # was copied from the canonical assertion row.
                cand["document_id"] = first_prov.get("document_id")
                if not cand["evidence_span"]:
                    cand["evidence_span"] = first_prov.get("evidence_span")

        results: list[dict[str, Any]] = []
        for cand in ordered_candidates:
            eid = cand["entity_id"]
            retrieval_provenance: dict[str, Any] = {
                "origins": sorted(cand["origins"]),
                "lane_ranks": cand["lane_ranks"],
                "raw_scores": cand["raw_scores"],
            }
            if cand["candidate_id"] in exact_ids:
                retrieval_provenance["exact_match"] = {
                    "kind": exact_kinds[cand["candidate_id"]],
                    "order": exact_ids.index(cand["candidate_id"]),
                }
            if cand["assertion_id"]:
                retrieval_provenance["assertion_id"] = cand["assertion_id"]
            if cand["source_chunk_id"]:
                retrieval_provenance["source_chunk_id"] = cand["source_chunk_id"]
            vector_artifact = cand.get("vector_artifact")
            if vector_artifact is not None:
                retrieval_provenance["vector"] = {
                    **vector_artifact,
                    "distance": cand["raw_scores"]["vector"],
                    "rank": cand["lane_ranks"]["vector"],
                    "scope_identity": {
                        "tenant_id": tenant_id,
                        "dataset_id": (
                            str(
                                cand["materialized_provenance"][0].get("dataset_id")
                                or ""
                            )
                            if cand["materialized_provenance"]
                            else ""
                        ),
                        "agent_id": agent_id,
                    },
                }
            if cand.get("graph_evidence"):
                retrieval_provenance.update(cand["graph_evidence"])
            legal_identity = (
                _assertion_legal_identity(cand["assertion"] or {})
                if self._legal_rules_enabled else None
            )
            if legal_identity is not None:
                retrieval_provenance["legal_identity"] = legal_identity

            cand_prov = cand["materialized_provenance"]
            if not cand_prov:
                continue
            item_set = legal_items_by_mutation.get(str(cand_prov[0]["mutation_id"]))
            if item_set is not None:
                retrieval_provenance["legal_items"] = item_set
            result = {
                "entity": entities.get(eid, {}),
                "candidate_id": cand["candidate_id"],
                "evidence_id": cand["assertion_id"],
                "assertion_id": cand["assertion_id"],
                "source_chunk_id": cand["source_chunk_id"],
                "document_id": cand["document_id"],
                "evidence_span": cand["evidence_span"],
                "raw_score": (
                    cand["raw_scores"].get("vector")
                    if "vector" in cand["raw_scores"]
                    else cand["raw_scores"].get("assertion")
                ),
                "rrf_score": cand["rrf_score"],
                "legal_factor": cand["legal_factor"],
                "final_score": cand["rrf_score"] * cand["legal_factor"],
                "provenance": cand_prov,
                "matched_assertions": [
                    p for p in cand_prov if p["assertion_id"] == cand["assertion_id"]
                ],
                "supporting_assertions": [
                    p for p in cand_prov if p["assertion_id"] != cand["assertion_id"]
                ],
                "retrieval_provenance": retrieval_provenance,
            }
            if self._reranker is not None:
                result["_rerank_text"] = rerank_texts[str(cand_prov[0]["mutation_id"])]
            if certification_metadata is not None:
                first_provenance = cand_prov[0]
                result["scope_identity"] = {
                    "tenant_id": tenant_id,
                    "dataset_id": str(first_provenance.get("dataset_id") or ""),
                    "agent_id": agent_id,
                    "jurisdiction": str(first_provenance.get("jurisdiction") or ""),
                    "status": str(first_provenance.get("status") or ""),
                }
            results.append(result)
        return results


    @staticmethod
    async def _advance_mutation_projection_state(db: Any, mutation_id: str) -> None:
        async with db.execute(
            "SELECT m.state as mutation_state, pr.state as pipeline_state "
            "FROM memory_mutations m "
            "LEFT JOIN pipeline_runs pr ON pr.pipeline_run_id = m.pipeline_run_id "
            "WHERE m.mutation_id = ?",
            (mutation_id,),
        ) as cursor:
            m_row = await cursor.fetchone()
        if (
            not m_row
            or m_row["mutation_state"]
            in (
                "ROLLING_BACK",
                "ROLLED_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
                "REJECTED",
            )
            or m_row["pipeline_state"]
            in (
                "ROLLING_BACK",
                "ROLLED_BACK",
                "PURGING",
                "PURGED",
                "CANCELLED",
                "REJECTED",
            )
        ):
            return
        async with db.execute(
            "SELECT projection_name, state FROM projection_outbox WHERE mutation_id = ?",
            (mutation_id,),
        ) as cursor:
            states = {row[0]: row[1] for row in await cursor.fetchall()}
        milestones: list[str] = []
        if states.get("SQL") == "COMPLETED":
            milestones.append("SQL_APPLIED")
        if states.get("VECTOR") in {"COMPLETED", "SKIPPED"}:
            milestones.append("VECTOR_APPLIED")
        if states.get("GRAPH") in {"COMPLETED", "SKIPPED"}:
            milestones.append("GRAPH_APPLIED")
        if len(milestones) == 3:
            milestones.append("COMMITTED")
        if not milestones:
            return
        current_state = str(m_row["mutation_state"])
        if current_state == "RETRY_PENDING":
            if not await MemoryDAO._transition_memory_mutation_in_tx(
                db, mutation_id, to_state="PENDING"
            ):
                return
            current_state = "PENDING"
        lifecycle_order = {
            "PENDING": -1,
            "VALIDATED": -1,
            "SQL_APPLIED": 0,
            "VECTOR_APPLIED": 1,
            "GRAPH_APPLIED": 2,
            "COMMITTED": 3,
        }
        current_index = lifecycle_order.get(current_state, -1)
        for state in milestones:
            state_index = lifecycle_order[state]
            if state_index <= current_index:
                continue
            if not await MemoryDAO._transition_memory_mutation_in_tx(
                db, mutation_id, to_state=state
            ):
                return
            current_state = state
            current_index = state_index
        async with db.execute(
            "SELECT pipeline_run_id FROM memory_mutations WHERE mutation_id = ?",
            (mutation_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row and row[0]:
            await MemoryDAO._recompute_pipeline_state_in_tx(
                db,
                str(row[0]),
                event_type=f"PROJECTION_{current_state}",
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def _sqlite_engine(self) -> AsyncEngine:
        """Internal SQLite engine — private to prevent DAO bypass."""
        return self._sql

    @property
    def vector_engine(self) -> VectorEngine:
        """Return the underlying vector engine (read-only access)."""
        return self._vec

    @property
    def graph_provider(self) -> KuzuGraphProvider | None:
        """Return the underlying graph provider (read-only access)."""
        return self._graph

    # ------------------------------------------------------------------
    # E1 FIX: Startup orphan reconciliation
    # ------------------------------------------------------------------


    # ==================================================================
    # Migration State Polling
    # ==================================================================


    # ==================================================================
    # INSERT — agent-scoped memory ingestion
    # ==================================================================


    # ==================================================================
    # SEARCH — agent-scoped memory retrieval
    # ==================================================================


    # ==================================================================

    # PURGE — agent-scoped soft-delete
    # ==================================================================


    # ==================================================================
    # UPDATE ENTITY DESCRIPTION
    # ==================================================================


    # ==================================================================
    # MARK CONSOLIDATED — agent-scoped
    # ==================================================================


    # ==================================================================
    # EDGE OPERATIONS — KùzuDB (Single Source of Truth)
    # ==================================================================

    def _require_graph(self) -> KuzuGraphProvider:
        """Return the graph provider or raise if not wired."""
        if self._graph is None:
            raise RuntimeError(
                "KuzuGraphProvider is not configured. "
                "Pass graph_provider to MemoryDAO constructor."
            )
        return self._graph


    # ==================================================================
    # INVALIDATION — agent-scoped soft-invalidation
    # ==================================================================


    # ==================================================================
    # FIND NODES — agent-scoped entity name lookup
    # ==================================================================


    # ==================================================================
    # GET MEMORY BY ID — agent-scoped single-node lookup
    # ==================================================================


    # ==================================================================
    # RAW LOG INSERT — hot-path ingestion (< 50ms, pure I/O)
    # ==================================================================

    async def _queue_usage(
        self, db: aiosqlite.Connection, tenant_id: str | None = None
    ) -> dict[str, int]:
        placeholders = ",".join("?" for _ in _ADMISSION_ACTIVE_STATES)
        predicate = f"state IN ({placeholders})"
        params: list[Any] = list(_ADMISSION_ACTIVE_STATES)
        if tenant_id is not None:
            predicate += " AND tenant_id = ?"
            params.append(tenant_id)
        async with db.execute(
            f"SELECT COUNT(*) AS records, COALESCE(SUM(payload_bytes), 0) AS bytes, "
            f"COALESCE(SUM(CASE WHEN state = 'IN_FLIGHT' THEN 1 ELSE 0 END), 0) AS in_flight, "
            f"COALESCE(SUM(CASE WHEN state = 'RETRY_PENDING' THEN 1 ELSE 0 END), 0) AS retry_pending "
            f"FROM dispatch_queue WHERE {predicate}",
            params,
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None
        return {
            key: int(row[key])
            for key in ("records", "bytes", "in_flight", "retry_pending")
        }

    @staticmethod
    def _enforce_queue_capacity(
        global_usage: dict[str, int],
        tenant_usage: dict[str, int],
        payload_bytes: int,
        policy: QueueAdmissionPolicy,
    ) -> None:
        if payload_bytes > policy.queue_max_single_record_bytes:
            raise QueueRecordTooLargeError("queue record exceeds configured size limit")
        checks = (
            (global_usage["records"] + 1 > policy.queue_max_pending_records, "global"),
            (
                global_usage["bytes"] + payload_bytes > policy.queue_max_pending_bytes,
                "global",
            ),
            (global_usage["in_flight"] >= policy.queue_max_in_flight_records, "global"),
            (
                global_usage["retry_pending"] >= policy.queue_max_retry_pending_records,
                "global",
            ),
            (
                tenant_usage["records"] + 1
                > policy.queue_max_pending_records_per_tenant,
                "tenant",
            ),
            (
                tenant_usage["bytes"] + payload_bytes
                > policy.queue_max_pending_bytes_per_tenant,
                "tenant",
            ),
            (
                tenant_usage["in_flight"]
                >= policy.queue_max_in_flight_records_per_tenant,
                "tenant",
            ),
            (
                tenant_usage["retry_pending"]
                >= policy.queue_max_retry_pending_records_per_tenant,
                "tenant",
            ),
        )
        for exceeded, scope in checks:
            if exceeded:
                raise QueueOverCapacityError(scope)

    async def admit_raw_log(
        self,
        agent_id: str,
        payload: dict[str, Any],
        *,
        policy: QueueAdmissionPolicy,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically admit a raw log and durable dispatch receipt, or persist nothing.

        Capacity is measured from canonical UTF-8 serialized envelopes under the
        SQLite writer transaction.  This prevents concurrent callers from
        bypassing count, byte, tenant, retry, or in-flight budgets.
        """
        _assert_valid_agent_id(agent_id)
        durable_tenant_id = tenant_id or str(payload.get("tenant_id") or agent_id)
        if not durable_tenant_id or payload.get("tenant_id") not in (
            None,
            durable_tenant_id,
        ):
            raise ValueError("payload tenant_id must match the durable tenant scope")
        if payload.get("agent_id") not in (None, agent_id):
            raise ValueError("payload agent_id must match the durable admission tenant")
        content_str = str(
            payload.get("content_payload")
            or payload.get("text")
            or payload.get("content")
            or ""
        )
        raw_metadata = payload.get("metadata")
        meta_dict = (
            cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
        )
        _validate_write_payload(content_str, meta_dict)
        durable_payload = dict(payload)
        durable_payload.setdefault("tenant_id", durable_tenant_id)
        durable_payload.setdefault("agent_id", agent_id)
        serialized, payload_bytes = _canonical_payload_bytes(durable_payload)
        metadata = payload.get("metadata", {})
        idempotency_key = (
            metadata.get("idempotency_key") if isinstance(metadata, dict) else None
        )
        content_hash = (
            metadata.get("content_sha256") if isinstance(metadata, dict) else None
        )
        memory_type = (
            metadata.get("memory_type") if isinstance(metadata, dict) else None
        )
        try:
            async with self._sql.transaction() as db:
                if (isinstance(idempotency_key, str) and idempotency_key) or (
                    isinstance(content_hash, str) and content_hash
                ):
                    async with db.execute(
                        "SELECT id FROM raw_logs WHERE agent_id = ? "
                        "AND json_extract(payload, '$.tenant_id') = ? "
                        "AND json_extract(payload, '$.session_id') = ? "
                        "AND ((? IS NULL AND json_extract(payload, '$.metadata.memory_type') IS NULL) "
                        "OR json_extract(payload, '$.metadata.memory_type') = ?) "
                        "AND ((? IS NOT NULL AND json_extract(payload, '$.metadata.idempotency_key') = ?) "
                        "OR (? IS NOT NULL AND json_extract(payload, '$.metadata.content_sha256') = ?)) "
                        "ORDER BY id ASC LIMIT 1",
                        (
                            agent_id,
                            durable_tenant_id,
                            payload.get("session_id"),
                            memory_type,
                            memory_type,
                            idempotency_key,
                            idempotency_key,
                            content_hash,
                            content_hash,
                        ),
                    ) as cursor:
                        existing = await cursor.fetchone()
                    if existing is not None:
                        await db.commit()
                        return {
                            "admission": "DEDUPLICATED",
                            "log_id": int(existing[0]),
                            "deduplicated": True,
                        }
                global_usage = await self._queue_usage(db)
                tenant_usage = await self._queue_usage(db, durable_tenant_id)
                self._enforce_queue_capacity(
                    global_usage, tenant_usage, payload_bytes, policy
                )
                cursor = await db.execute(
                    "INSERT INTO raw_logs (agent_id, payload, status) VALUES (?, ?, 'DEFERRED')",
                    (agent_id, serialized),
                )
                assert cursor.lastrowid is not None
                log_id = int(cursor.lastrowid)
                dispatch_id = str(uuid.uuid4())
                queue_id = str(uuid.uuid4())
                idempotency_key = f"raw-log:{agent_id}:{log_id}"
                await db.execute(
                    "INSERT INTO dispatch_journal (dispatch_id, source_record_id, tenant_id, agent_id, "
                    "job_type, idempotency_key, state, attempt_count, queue_record_id, dispatched_at, finalized_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'cold_path', ?, 'RECEIPT_RECORDED', 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (
                        dispatch_id,
                        log_id,
                        durable_tenant_id,
                        agent_id,
                        idempotency_key,
                        queue_id,
                    ),
                )
                await db.execute(
                    "INSERT INTO dispatch_queue (queue_record_id, dispatch_id, tenant_id, agent_id, job_type, "
                    "payload_reference, payload_bytes, idempotency_key, state) VALUES (?, ?, ?, ?, 'cold_path', ?, ?, ?, 'ENQUEUED')",
                    (
                        queue_id,
                        dispatch_id,
                        durable_tenant_id,
                        agent_id,
                        log_id,
                        payload_bytes,
                        idempotency_key,
                    ),
                )
                await db.execute(
                    "INSERT INTO dispatch_receipts (receipt_id, dispatch_id, queue_record_id, tenant_id, agent_id, outcome, idempotency_key) "
                    "VALUES (?, ?, ?, ?, ?, 'ENQUEUED', ?)",
                    (
                        str(uuid.uuid4()),
                        dispatch_id,
                        queue_id,
                        durable_tenant_id,
                        agent_id,
                        idempotency_key,
                    ),
                )
                await db.commit()
        except (aiosqlite.Error, OSError) as exc:
            raise QueueUnavailableError("durable admission is unavailable") from exc
        return {
            "admission": "DEFERRED",  # type: ignore[index]
            "log_id": log_id,
            "dispatch_id": dispatch_id,
            "queue_record_id": queue_id,
            "payload_bytes": payload_bytes,
            "deduplicated": False,
        }

    async def get_queue_admission_metrics(
        self, agent_id: str, *, tenant_id: str | None = None
    ) -> dict[str, Any]:
        """Return bounded queue pressure counts for readiness/metrics consumers."""
        _assert_valid_agent_id(agent_id)
        try:
            async with self._sql.connection() as db:
                global_usage = await self._queue_usage(db)
                tenant_usage = await self._queue_usage(db, tenant_id or agent_id)
                async with db.execute(
                    "SELECT COUNT(*) FROM dispatch_queue WHERE state = 'BLOCKED'"
                ) as cursor:
                    _row = await cursor.fetchone()
                    assert _row is not None
                    blocked = int(_row[0])
        except (aiosqlite.Error, OSError) as exc:
            raise QueueUnavailableError("durable admission is unavailable") from exc
        return {
            "global": global_usage,
            "tenant": tenant_usage,
            "blocked_records": blocked,
        }

    async def insert_raw_log(self, agent_id: str, payload: dict) -> int:
        """Insert a raw payload into the ``raw_logs`` staging table.

        This is the **hot-path write** for the v0.7.1 decoupled ingestion
        architecture.  It performs a single async SQLite INSERT and returns
        the auto-generated ``log_id`` immediately.

        **No validation, ECOD, REBEL extraction, or LLM calls occur here.**
        All heavy processing is deferred to the cold-path worker
        (``process_cold_path``).

        Args:
            agent_id: **Mandatory** tenant isolation key.
            payload: Raw ingestion payload (serialised as JSON).

        Returns:
            The ``id`` (INTEGER PRIMARY KEY) of the newly inserted row.
        """
        _assert_valid_agent_id(agent_id)
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "INSERT INTO raw_logs (agent_id, payload) VALUES (?, ?)",
                (agent_id, json.dumps(payload)),
            )
            log_id = cursor.lastrowid
            await db.commit()

        logger.info("INSERT_RAW_LOG | agent_id=%s log_id=%s", agent_id, log_id)
        return log_id or 0

    async def get_raw_log(self, agent_id: str, log_id: int) -> dict[str, Any] | None:
        """Retrieve a single raw_logs row by primary key.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            log_id: INTEGER primary key of the raw_logs row.

        Returns:
            A dict with keys ``id``, ``payload``, ``status``, ``created_at``,
            or ``None`` if no row with that ID exists.
        """
        _assert_valid_agent_id(agent_id)
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT id, agent_id, payload, status, created_at "
                "FROM raw_logs WHERE id = ? AND agent_id = ?",
                (log_id, agent_id),
            ) as cursor:
                row = await cursor.fetchone()  # type: ignore[index]
                if row is None:
                    return None
                row_dict = dict(row)
                # Deserialise the JSON payload back to a dict
                if isinstance(row_dict.get("payload"), str):
                    row_dict["payload"] = json.loads(row_dict["payload"])
                return row_dict

    async def get_recent_logs(
        self, agent_id: str, session_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Retrieve recent raw_logs for a given session.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            session_id: The session ID to filter by.
            limit: Maximum number of recent logs to return.

        Returns:
            A list of raw_log payload dicts.
        """
        _assert_valid_agent_id(agent_id)
        recent_logs = []
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT l.payload FROM raw_logs l WHERE l.agent_id = ? "
                "AND json_extract(l.payload, '$.session_id') = ? "
                "AND NOT EXISTS (SELECT 1 FROM memory_mutations m "
                "LEFT JOIN document_revisions r ON r.revision_id = m.revision_id "
                "WHERE m.raw_log_id = l.id AND m.agent_id = l.agent_id "
                "AND (m.state IN ('PURGED', 'ROLLED_BACK', 'REJECTED', 'CANCELLED') "
                "OR r.status IN ('PURGED', 'ROLLED_BACK', 'SUPERSEDED'))) "
                "ORDER BY l.created_at DESC LIMIT ?",
                (agent_id, session_id, limit),
            ) as cur:
                rows = await cur.fetchall()
                for row in rows:
                    payload_raw = row[0]
                    if isinstance(payload_raw, str):
                        payload = json.loads(payload_raw)
                    else:
                        payload = payload_raw
                    recent_logs.append(payload)
        return recent_logs

    async def request_session_finalization(
        self, agent_id: str, session_id: str
    ) -> dict[str, Any]:
        """Create one idempotent durable finalization intent for an exact session."""
        _assert_valid_agent_id(agent_id)
        if not session_id or session_id == "*":
            raise ValueError("an exact session_id is required")
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT * FROM session_finalization_journal WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is None:
                async with db.execute(
                    "SELECT COUNT(*) FROM raw_logs WHERE agent_id = ? AND json_extract(payload, '$.session_id') = ? "
                    "AND status NOT LIKE 'processed%' AND status NOT LIKE 'rejected%'",
                    (agent_id, session_id),
                ) as cursor:
                    _row = await cursor.fetchone()
                    assert _row is not None
                    pending_count = int(_row[0])
                finalization_id = str(uuid.uuid4())
                state = "COMPLETED" if pending_count == 0 else "PENDING"
                await db.execute(
                    "INSERT INTO session_finalization_journal "
                    "(finalization_id, agent_id, session_id, idempotency_key, state, completed_at) "
                    "VALUES (?, ?, ?, ?, ?, CASE WHEN ? = 'COMPLETED' THEN CURRENT_TIMESTAMP ELSE NULL END)",
                    (
                        finalization_id,
                        agent_id,
                        session_id,
                        f"session-finalize:{agent_id}:{session_id}",
                        state,
                        state,
                    ),
                )
            async with db.execute(
                "SELECT * FROM session_finalization_journal WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            ) as cursor:
                row = await cursor.fetchone()
            await db.commit()
        assert row is not None
        return dict(row)

    async def claim_session_finalization(
        self,
        agent_id: str,
        session_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None:
        """Claim pending finalization using a durable fencing token."""
        _assert_valid_agent_id(agent_id)
        if not worker_id or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid finalization claim bounds")
        token = str(uuid.uuid4())
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE session_finalization_journal SET state = 'CLAIMED', claim_token = ?, claimed_by = ?, "
                "lease_expires_at = datetime('now', ?), attempt_count = attempt_count + 1, "
                "last_error_class = NULL, updated_at = CURRENT_TIMESTAMP WHERE agent_id = ? AND session_id = ? "
                "AND (state IN ('PENDING','RETRY_PENDING') OR (state = 'CLAIMED' AND lease_expires_at <= CURRENT_TIMESTAMP))",
                (token, worker_id, f"+{lease_seconds} seconds", agent_id, session_id),
            )
            if cursor.rowcount != 1:
                await db.commit()
                return None
            async with db.execute(
                "SELECT * FROM session_finalization_journal WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            ) as rows:
                row = await rows.fetchone()
            await db.commit()
        return dict(row) if row else None

    async def get_session_finalization(
        self, agent_id: str, session_id: str
    ) -> dict[str, Any] | None:
        _assert_valid_agent_id(agent_id)
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM session_finalization_journal WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_pending_session_finalizations(
        self, *, limit: int = 1
    ) -> list[dict[str, Any]]:
        """Return bounded durable finalization intents for a worker consumer."""
        if not 1 <= limit <= 100:
            raise ValueError("session finalization limit must be between 1 and 100")
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT agent_id, session_id FROM session_finalization_journal "
                "WHERE state IN ('PENDING', 'RETRY_PENDING') "
                "OR (state = 'CLAIMED' AND lease_expires_at <= CURRENT_TIMESTAMP) "
                "ORDER BY updated_at LIMIT ?",
                (limit,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def get_pending_session_raw_logs(
        self, agent_id: str, session_id: str
    ) -> list[int]:
        _assert_valid_agent_id(agent_id)
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT id FROM raw_logs WHERE agent_id = ? AND json_extract(payload, '$.session_id') = ? "
                "AND status = 'DEFERRED' ORDER BY id",
                (agent_id, session_id),
            ) as cursor:
                return [int(row[0]) for row in await cursor.fetchall()]

    async def complete_session_finalization(
        self, agent_id: str, session_id: str, *, worker_id: str, claim_token: str
    ) -> bool:
        """Complete only when every exact-scope raw log is terminal and the fence matches."""
        _assert_valid_agent_id(agent_id)
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM raw_logs WHERE agent_id = ? AND json_extract(payload, '$.session_id') = ? "
                "AND status NOT LIKE 'processed%' AND status NOT LIKE 'rejected%'",
                (agent_id, session_id),
            ) as cursor:
                _row = await cursor.fetchone()
                assert _row is not None
                incomplete = int(_row[0])
            if incomplete:
                await db.commit()
                return False
            cursor = await db.execute(
                "UPDATE session_finalization_journal SET state = 'COMPLETED', completed_at = CURRENT_TIMESTAMP, "
                "claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE agent_id = ? AND session_id = ? AND state = 'CLAIMED' AND claimed_by = ? AND claim_token = ?",
                (agent_id, session_id, worker_id, claim_token),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def fail_session_finalization(
        self,
        agent_id: str,
        session_id: str,
        *,
        worker_id: str,
        claim_token: str,
        error_class: str,
    ) -> bool:
        """Persist a sanitized bounded failure; stale workers cannot mutate state."""
        _assert_valid_agent_id(agent_id)
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE session_finalization_journal SET state = CASE WHEN attempt_count >= retry_limit THEN 'BLOCKED' "
                "ELSE 'RETRY_PENDING' END, last_error_class = ?, claim_token = NULL, claimed_by = NULL, "
                "lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE agent_id = ? AND session_id = ? "
                "AND state = 'CLAIMED' AND claimed_by = ? AND claim_token = ?",
                (error_class[:120], agent_id, session_id, worker_id, claim_token),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def update_raw_log_status(
        self,
        agent_id: str,
        log_id: int,
        status: str,
        *,
        error_reason: str | None = None,
    ) -> None:
        """Transition the status of a raw_logs row.

        Valid transitions: ``queued → processing → processed | failed | rejected``.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            log_id: INTEGER primary key of the raw_logs row.
            status: New status string (``processing``, ``processed``,
                    ``failed``, ``rejected``).
            error_reason: Optional error message (stored in the ``status``
                          field as ``failed:<reason>`` for traceability).
        """
        _assert_valid_agent_id(agent_id)
        final_status = f"{status}:{error_reason}" if error_reason else status

        async with self._sql.transaction() as db:
            await db.execute(
                "UPDATE raw_logs SET status = ? WHERE id = ? AND agent_id = ?",
                (final_status, log_id, agent_id),
            )
            await db.commit()

        logger.debug(
            "UPDATE_RAW_LOG_STATUS | agent_id=%s log_id=%d status=%s",
            agent_id,
            log_id,
            final_status,
        )

    async def claim_raw_log(
        self, agent_id: str, log_id: int, *, worker_id: str, lease_seconds: int = 1800
    ) -> dict[str, Any] | None:
        """Atomically claim a deferred or expired cold-path job."""
        _assert_valid_agent_id(agent_id)
        if not worker_id or not 1 <= lease_seconds <= 3600:
            raise ValueError("worker_id and a 1..3600 second lease are required.")
        token = str(uuid.uuid4())
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE raw_logs SET status = 'processing', claim_token = ?, "
                "claimed_by = ?, lease_expires_at = datetime('now', ?), "
                "attempt_count = attempt_count + 1, last_error = NULL "
                "WHERE id = ? AND agent_id = ? AND (status = 'DEFERRED' OR "
                "(status = 'processing' AND lease_expires_at <= CURRENT_TIMESTAMP))",
                (token, worker_id, f"+{lease_seconds} seconds", log_id, agent_id),
            )
            if cursor.rowcount != 1:
                await db.commit()
                return None
            async with db.execute(
                "SELECT id, agent_id, payload, status, claim_token, claimed_by, "
                "lease_expires_at, attempt_count FROM raw_logs WHERE id = ? AND agent_id = ?",
                (log_id, agent_id),
            ) as rows:
                row = await rows.fetchone()
            await db.commit()
        assert row is not None
        claimed = dict(row)
        if isinstance(claimed.get("payload"), str):
            claimed["payload"] = json.loads(claimed["payload"])
        return claimed

    async def transition_claimed_raw_log(
        self,
        agent_id: str,
        log_id: int,
        *,
        worker_id: str,
        claim_token: str,
        status: str,
        error_reason: str | None = None,
    ) -> bool:
        """Finalize a job only when its owner and fencing token still match."""
        _assert_valid_agent_id(agent_id)
        if status not in {"processed", "failed", "rejected", "DEFERRED"}:
            raise ValueError("claimed job status must be terminal or DEFERRED.")
        final_status = f"{status}:{error_reason}" if error_reason else status
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE raw_logs SET status = ?, claim_token = NULL, claimed_by = NULL, "
                "lease_expires_at = NULL, last_error = ?, processed_at = "
                "CASE WHEN ? = 'processed' THEN CURRENT_TIMESTAMP ELSE processed_at END "
                "WHERE id = ? AND agent_id = ? AND status = 'processing' "
                "AND claimed_by = ? AND claim_token = ?",
                (
                    final_status,
                    error_reason,
                    status,
                    log_id,
                    agent_id,
                    worker_id,
                    claim_token,
                ),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def recover_expired_raw_log_claims(self) -> int:
        """Return expired claims to DEFERRED without changing terminal rows."""
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE raw_logs SET status = 'DEFERRED', claim_token = NULL, "
                "claimed_by = NULL, lease_expires_at = NULL "
                "WHERE status = 'processing' AND lease_expires_at <= CURRENT_TIMESTAMP"
            )
            await db.commit()
        return cursor.rowcount

    async def recover_processing_raw_logs(self) -> int:
        """Release abandoned raw-log claims after the runtime takes the writer lock."""
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE raw_logs SET status = 'DEFERRED', claim_token = NULL, "
                "claimed_by = NULL, lease_expires_at = NULL WHERE status = 'processing'"
            )
            await db.commit()
        return cursor.rowcount

    async def recover_in_flight_projection_and_dispatch(self) -> dict[str, int]:
        """Release abandoned in-flight projection and dispatch claims after writer lock."""
        async with self._sql.transaction() as db:
            p_cursor = await db.execute(
                "UPDATE projection_outbox SET state = 'RETRY_PENDING', claim_token = NULL, "
                "claimed_by = NULL, lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE state = 'IN_FLIGHT'"
            )
            d_cursor = await db.execute(
                "UPDATE dispatch_queue SET state = 'RETRY_PENDING', claim_token = NULL, "
                "claimed_by = NULL, lease_expires_at = NULL "
                "WHERE state = 'IN_FLIGHT'"
            )
            await db.commit()
        return {
            "projection_outbox": p_cursor.rowcount,
            "dispatch_queue": d_cursor.rowcount,
        }


    async def dispatch_raw_log(
        self,
        agent_id: str,
        log_id: int,
        *,
        worker_id: str,
        policy: QueueAdmissionPolicy | None = None,
    ) -> dict[str, Any]:
        """Atomically materialize a bounded raw-log dispatch intent, queue row and receipt."""
        _assert_valid_agent_id(agent_id)
        if not worker_id:
            raise ValueError("worker_id is required")
        if policy is None:
            policy = _DEFAULT_QUEUE_ADMISSION_POLICY
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT id, payload FROM raw_logs WHERE id = ? AND agent_id = ?",
                (log_id, agent_id),
            ) as cursor:
                raw_log = await cursor.fetchone()
                if raw_log is None:
                    raise ValueError("raw log is not in the requested tenant scope")
            raw_payload = raw_log["payload"]
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
            _, payload_bytes = _canonical_payload_bytes(raw_payload)
            async with db.execute(
                "SELECT * FROM dispatch_journal WHERE source_record_id = ? AND agent_id = ?",
                (log_id, agent_id),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is None:
                dispatch_id = str(uuid.uuid4())
                idempotency_key = f"raw-log:{agent_id}:{log_id}"
                await db.execute(
                    "INSERT INTO dispatch_journal (dispatch_id, source_record_id, tenant_id, agent_id, "
                    "job_type, idempotency_key, state, attempt_count, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'cold_path', ?, 'PENDING', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (dispatch_id, log_id, agent_id, agent_id, idempotency_key),
                )
            else:
                dispatch_id = existing["dispatch_id"]
                idempotency_key = existing["idempotency_key"]
            async with db.execute(
                "SELECT * FROM dispatch_journal WHERE dispatch_id = ?", (dispatch_id,)
            ) as cursor:
                journal = await cursor.fetchone()
                assert journal is not None
            if journal["state"] != "RECEIPT_RECORDED":
                global_usage = await self._queue_usage(db)
                tenant_usage = await self._queue_usage(db, agent_id)
                self._enforce_queue_capacity(
                    global_usage, tenant_usage, payload_bytes, policy
                )
                token = str(uuid.uuid4())
                await db.execute(
                    "UPDATE dispatch_journal SET state = 'CLAIMED', claimed_by = ?, claim_token = ?, "
                    "attempt_count = attempt_count + 1, lease_expires_at = datetime('now', '+300 seconds'), "
                    "updated_at = CURRENT_TIMESTAMP WHERE dispatch_id = ?",
                    (worker_id, token, dispatch_id),
                )
                queue_id = journal["queue_record_id"] or str(uuid.uuid4())
                await db.execute(
                    "INSERT OR IGNORE INTO dispatch_queue (queue_record_id, dispatch_id, tenant_id, agent_id, "
                    "job_type, payload_reference, payload_bytes, idempotency_key, state) VALUES (?, ?, ?, ?, 'cold_path', ?, ?, ?, 'ENQUEUED')",
                    (
                        queue_id,
                        dispatch_id,
                        agent_id,
                        agent_id,
                        log_id,
                        payload_bytes,
                        idempotency_key,
                    ),
                )
                await db.execute(
                    "INSERT OR IGNORE INTO dispatch_receipts (receipt_id, dispatch_id, queue_record_id, tenant_id, "
                    "agent_id, outcome, idempotency_key) VALUES (?, ?, ?, ?, ?, 'ENQUEUED', ?)",
                    (
                        str(uuid.uuid4()),
                        dispatch_id,
                        queue_id,
                        agent_id,
                        agent_id,
                        idempotency_key,
                    ),
                )
                await db.execute(
                    "UPDATE dispatch_journal SET state = 'RECEIPT_RECORDED', queue_record_id = ?, "
                    "dispatched_at = CURRENT_TIMESTAMP, finalized_at = CURRENT_TIMESTAMP, claim_token = NULL, "
                    "claimed_by = NULL, lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE dispatch_id = ?",
                    (queue_id, dispatch_id),
                )
            async with db.execute(
                "SELECT * FROM dispatch_journal WHERE dispatch_id = ?", (dispatch_id,)
            ) as cursor:
                _r = await cursor.fetchone()
                assert _r is not None
                result = dict(_r)
            await db.commit()
        return result

    async def recover_raw_log_dispatches(
        self, *, worker_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Find tenant-scoped deferred raw logs that lack a durable dispatch receipt."""
        if not worker_id or not 1 <= limit <= 1000:
            raise ValueError("invalid dispatch recovery bounds")
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT r.id, r.agent_id FROM raw_logs r LEFT JOIN dispatch_journal j "
                "ON j.source_record_id = r.id WHERE r.status = 'DEFERRED' AND "
                "(j.dispatch_id IS NULL OR j.state != 'RECEIPT_RECORDED') ORDER BY r.id LIMIT ?",
                (limit,),
            ) as cursor:
                pending = [
                    (row["agent_id"], row["id"]) for row in await cursor.fetchall()
                ]
        return [
            await self.dispatch_raw_log(aid, record_id, worker_id=worker_id)
            for aid, record_id in pending
        ]

    async def claim_dispatch_queue(
        self, *, worker_id: str, limit: int = 100, lease_seconds: int = 300
    ) -> list[dict[str, Any]]:
        """Claim bounded durable queue records with a worker fencing token."""
        if not worker_id or not 1 <= limit <= 1000 or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid dispatch queue claim bounds")
        token = str(uuid.uuid4())
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT queue_record_id FROM dispatch_queue WHERE state = 'ENQUEUED' "
                "OR (state = 'RETRY_PENDING' AND (lease_expires_at IS NULL "
                "OR lease_expires_at <= CURRENT_TIMESTAMP)) "
                "OR (state = 'IN_FLIGHT' AND lease_expires_at <= CURRENT_TIMESTAMP) ORDER BY created_at LIMIT ?",
                (limit,),
            ) as cursor:
                ids = [row[0] for row in await cursor.fetchall()]
            claimed: list[str] = []
            for queue_id in ids:
                cursor = await db.execute(
                    "UPDATE dispatch_queue SET state = 'IN_FLIGHT', claimed_by = ?, claim_token = ?, "
                    "lease_expires_at = datetime('now', ?), attempt_count = attempt_count + 1 "
                    "WHERE queue_record_id = ? AND (state = 'ENQUEUED' "
                    "OR (state = 'RETRY_PENDING' AND (lease_expires_at IS NULL "
                    "OR lease_expires_at <= CURRENT_TIMESTAMP)) "
                    "OR (state = 'IN_FLIGHT' AND lease_expires_at <= CURRENT_TIMESTAMP))",
                    (worker_id, token, f"+{lease_seconds} seconds", queue_id),
                )
                if cursor.rowcount == 1:
                    claimed.append(queue_id)
            if not claimed:
                await db.commit()
                return []
            placeholders = ",".join("?" for _ in claimed)
            async with db.execute(
                f"SELECT * FROM dispatch_queue WHERE queue_record_id IN ({placeholders})",
                claimed,
            ) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            await db.commit()
        return rows

    async def complete_dispatch_queue(
        self,
        queue_record_id: str,
        *,
        worker_id: str,
        claim_token: str,
        outcome: str,
        side_effect_verified: bool,
    ) -> bool:
        """Write a completion receipt before ACK/finalization; stale fences never finalize."""
        if not worker_id or not claim_token:
            raise ValueError("worker_id and claim_token are required")
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT * FROM dispatch_queue WHERE queue_record_id = ?",
                (queue_record_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if (
                row is None
                or row["state"] != "IN_FLIGHT"
                or row["claimed_by"] != worker_id
                or row["claim_token"] != claim_token
            ):
                await db.commit()
                return False
            if not side_effect_verified:
                if int(row["attempt_count"]) >= 5:
                    await db.execute(
                        "UPDATE dispatch_queue SET state = 'DEAD_LETTER', claim_token = NULL, claimed_by = NULL, "
                        "lease_expires_at = NULL, last_error_class = ? WHERE queue_record_id = ? AND claim_token = ?",
                        (outcome[:120], queue_record_id, claim_token),
                    )
                    async with db.execute(
                        "SELECT mutation_id, state FROM memory_mutations "
                        "WHERE raw_log_id = ? AND agent_id = ? AND tenant_id = ?",
                        (row["payload_reference"], row["agent_id"], row["tenant_id"]),
                    ) as mutations:
                        linked = await mutations.fetchall()
                    for mutation in linked:
                        # Validated mutations may be projecting; leave them alone.
                        if mutation["state"] in {
                            "RECEIVED",
                            "EXTRACTED",
                            "RETRY_PENDING",
                        }:
                            await self._transition_memory_mutation_in_tx(
                                db,
                                str(mutation["mutation_id"]),
                                to_state="DEAD_LETTER",
                                failure_class=outcome[:120],
                            )
                    await db.execute(
                        "UPDATE raw_logs SET status = 'failed', claim_token = NULL, claimed_by = NULL, "
                        "lease_expires_at = NULL, last_error = ? WHERE id = ? AND agent_id = ? "
                        "AND status IN ('queued', 'DEFERRED', 'processing', 'dispatched')",
                        (outcome[:120], row["payload_reference"], row["agent_id"]),
                    )
                    await db.commit()
                    return False
                await db.execute(
                    "UPDATE dispatch_queue SET state = 'RETRY_PENDING', claim_token = NULL, claimed_by = NULL, "
                    "lease_expires_at = datetime('now', ?), last_error_class = ? WHERE queue_record_id = ? AND claim_token = ?",
                    (
                        f"+{_retry_delay_seconds(int(row['attempt_count']))} seconds",
                        outcome[:120],
                        queue_record_id,
                        claim_token,
                    ),
                )
                await db.commit()
                return False
            await db.execute(
                "INSERT OR IGNORE INTO dispatch_completion_receipts (receipt_id, queue_record_id, dispatch_id, tenant_id, agent_id, "
                "worker_id, claim_token, outcome, side_effect_verified, attempt_count, idempotency_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    str(uuid.uuid4()),
                    queue_record_id,
                    row["dispatch_id"],
                    row["tenant_id"],
                    row["agent_id"],
                    worker_id,
                    claim_token,
                    outcome,
                    row["attempt_count"],
                    f"completion:{row['idempotency_key']}",
                ),
            )
            cursor = await db.execute(
                "UPDATE dispatch_queue SET state = 'FINALIZED', claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL "
                "WHERE queue_record_id = ? AND state = 'IN_FLIGHT' AND claim_token = ?",
                (queue_record_id, claim_token),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def get_dispatch_completion_receipt(
        self, queue_record_id: str
    ) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM dispatch_completion_receipts WHERE queue_record_id = ?",
                (queue_record_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_dispatch_receipt(self, dispatch_id: str) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM dispatch_receipts WHERE dispatch_id = ?", (dispatch_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_dispatch_receipt_by_source(
        self, agent_id: str, log_id: int
    ) -> dict[str, Any] | None:
        _assert_valid_agent_id(agent_id)
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT r.* FROM dispatch_receipts r JOIN dispatch_journal j ON j.dispatch_id = r.dispatch_id "
                "WHERE j.agent_id = ? AND j.source_record_id = ?",
                (agent_id, log_id),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    # ==================================================================
    # HEALTH — engine passthrough
    # ==================================================================

    async def vector_consumer_status(self) -> dict[str, Any]:
        """Report whether active registry vectors can be consumed right now."""
        embedding_identity = getattr(self._vec, "embedding_identity", None)
        identity_metadata = V4_VECTOR_CONSUMER_CONTRACT._identity_metadata(
            embedding_identity
        )
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT DISTINCT r.registry_id, r.metadata_json "
                "FROM artifact_registry r "
                "WHERE r.store_name = 'VECTOR' "
                "AND r.artifact_kind = 'ASSERTION_VECTOR' "
                "AND r.state = 'ACTIVE' "
                "AND EXISTS (SELECT 1 FROM artifact_sources s "
                "WHERE s.registry_id = r.registry_id AND s.state = 'ACTIVE')"
            ) as cursor:
                rows = list(await cursor.fetchall())

        rejection_reasons: Counter[str] = Counter()
        compatible = 0
        for row in rows:
            metadata = _decode_registry_metadata(row[1])
            errors = V4_VECTOR_CONSUMER_CONTRACT.compatibility_errors(
                metadata,
                embedding_identity=embedding_identity,
            )
            if errors:
                rejection_reasons.update(errors)
            else:
                compatible += 1
        active = len(rows)
        return {
            "status": (
                "empty" if active == 0 else "healthy" if compatible > 0 else "degraded"
            ),
            "contract_version": V4_VECTOR_LANE_DIAGNOSTIC_CONTRACT_VERSION,
            "representation_version": V4_VECTOR_CONSUMER_CONTRACT.representation_version,
            "embedding_space_id": (
                identity_metadata["embedding_space_id"]
                if identity_metadata is not None
                else None
            ),
            "active_registry_artifacts": active,
            "compatible_registry_artifacts": compatible,
            "rejected_registry_artifacts": active - compatible,
            "rejection_reasons": dict(sorted(rejection_reasons.items())),
        }

    async def health_check(self, *, detailed: bool = True) -> dict[str, Any]:
        """Aggregate health status from all storage backends."""
        sql_health = (
            await self._sql.health_check()
            if detailed
            else await self._sql.health_check(check_integrity=False)
        )
        vec_health = await self._vec.health_check()
        result: dict[str, Any] = {
            "sqlite": sql_health,
            "vector": vec_health,
        }
        if self._graph is not None:
            graph_status = "not_initialized"
            if self._graph.is_initialized:
                try:
                    await self._graph.execute_query(
                        "MATCH (n:Entity) RETURN COUNT(n) AS c"
                    )
                    graph_status = "healthy"
                except Exception:
                    graph_status = "unhealthy"
            result["graph"] = {
                "status": graph_status,
                "db_path": self._graph.db_path,
                "timeout_seconds": getattr(self._graph, "search_timeout_seconds", None),
            }
        if not detailed:
            # /health consumes only backend status and rebuild maintenance.
            # Keep corpus-sized integrity/ownership audits in full diagnostics.
            result["v4_rebuild"] = await self._rebuild_health.snapshot()
            return result
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT state, COUNT(*) FROM projection_outbox GROUP BY state"
            ) as cursor:
                outbox_states = {row[0]: row[1] for row in await cursor.fetchall()}
            async with db.execute(
                "SELECT COUNT(*) FROM projection_outbox WHERE state = 'IN_FLIGHT' "
                "AND lease_expires_at <= CURRENT_TIMESTAMP"
            ) as cursor:
                stuck_claim_row = await cursor.fetchone()
            if stuck_claim_row is None:
                raise RuntimeError("stuck projection query returned no row")
            stuck_claims = int(stuck_claim_row[0])
            async with db.execute(
                "SELECT state, COUNT(*) FROM artifact_cleanup_outbox GROUP BY state"
            ) as cursor:
                cleanup_states = {row[0]: row[1] for row in await cursor.fetchall()}
            async with db.execute(
                "SELECT state, COUNT(*) FROM pipeline_runs GROUP BY state"
            ) as cursor:
                pipeline_states = {row[0]: row[1] for row in await cursor.fetchall()}
            async with db.execute(
                "SELECT COUNT(*) FROM artifact_registry r WHERE r.state = 'ACTIVE' "
                "AND NOT EXISTS (SELECT 1 FROM artifact_sources s "
                "WHERE s.registry_id = r.registry_id AND s.state = 'ACTIVE')"
            ) as cursor:
                orphan_row = await cursor.fetchone()
            if orphan_row is None:
                raise RuntimeError("orphan registry query returned no row")
            orphan_registry = int(orphan_row[0])
            async with db.execute(
                "SELECT COUNT(*) FROM (SELECT registry_id FROM artifact_sources "
                "WHERE state = 'ACTIVE' GROUP BY registry_id HAVING COUNT(*) > 1)"
            ) as cursor:
                shared_row = await cursor.fetchone()
            if shared_row is None:
                raise RuntimeError("shared artifact query returned no row")
            shared_artifacts = int(shared_row[0])
        result["v4_projection"] = {
            "backlog": sum(
                outbox_states.get(state, 0)
                for state in ("PENDING", "RETRY_PENDING", "IN_FLIGHT")
            ),
            "dead_letter": outbox_states.get("DEAD_LETTER", 0),
            "stuck_claims": stuck_claims,
            "states": outbox_states,
        }
        result["v4_ownership"] = {
            "cleanup_backlog": sum(
                cleanup_states.get(state, 0)
                for state in ("PENDING", "RETRY_PENDING", "IN_FLIGHT")
            ),
            "cleanup_blocked": cleanup_states.get("BLOCKED", 0),
            "orphan_registry": orphan_registry,
            "shared_artifacts": shared_artifacts,
            "cleanup_states": cleanup_states,
            "pipeline_states": pipeline_states,
        }
        result["v4_rebuild"] = await self._rebuild_health.snapshot()
        result["v4_vector_consumer"] = await self.vector_consumer_status()
        return result


    # ==================================================================
    # COST CONTROL (Daily Limits)
    # ==================================================================

    async def increment_and_check_daily_limit(
        self, subject_id: str, limit: int = 1000
    ) -> bool:
        """Increment the daily request counter for a verified principal subject.

        ``subject_id`` is a server-side principal ID, never a credential,
        tenant field, or agent ID supplied by a request.
        Returns True if allowed, False if exceeded.
        """
        from datetime import date

        today = date.today().isoformat()

        async with self._sql.transaction() as db:
            await db.execute(
                "INSERT INTO daily_limits (subject_id, date, request_count) "
                "VALUES (?, ?, 1) "
                "ON CONFLICT(subject_id, date) DO UPDATE SET request_count = request_count + 1",
                (subject_id, today),
            )

            async with db.execute(
                "SELECT request_count FROM daily_limits WHERE subject_id = ? AND date = ?",
                (subject_id, today),
            ) as cur:
                row = await cur.fetchone()

            await db.commit()

        if row and row[0] > limit:
            return False
        return True

