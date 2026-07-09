"""MESA v0.4.0 — Phase 2 (Part 2): Automated Graph Poisoning Audit.

Detects "Confident Hallucinations" where the REBEL extractor hallucinates
fake laws and poisons the knowledge graph.

Audit logic:
  1. Connect to the MemoryDAO SQLite graph (edges + nodes tables).
  2. Query all edges where relation_type IN ('DAYANIR', 'ATIF_YAPAR').
  3. Resolve target_id → entity_name via the nodes table.
  4. Validate each target entity_name against the official Turkish law
     reference set (representing omersaidd/Kanunlar).
  5. Flag any target_node NOT in the reference set as GRAPH POISONING.

Fatal guardrail:
  If poisoning_rate > 0.05 (5%) → GraphPoisoningError + sys.exit(1).

Usage:
    python -m mesa_evals.legal_audit --db mesa.db --agent-id legal_agent
    python -m mesa_evals.legal_audit --db mesa.db --agent-id legal_agent --threshold 0.03
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field

from mesa_storage.kuzu_provider import KuzuGraphProvider

logger = logging.getLogger("MESA_LegalAudit")

# ---------------------------------------------------------------------------
# Fatal error type
# ---------------------------------------------------------------------------


class GraphPoisoningError(Exception):
    """Raised when the graph poisoning rate exceeds the fatal threshold."""


# ---------------------------------------------------------------------------
# Reference data — ground-truth valid Turkish law articles
# Represents the canonical set from omersaidd/Kanunlar.
# B-8 FIX: load_reference_set() attempts HuggingFace dynamic loading;
# falls back to VALID_LAW_ARTICLES on any failure.
# ---------------------------------------------------------------------------

VALID_LAW_ARTICLES: frozenset[str] = frozenset(
    {
        # Türk Borçlar Kanunu (TBK) — key articles
        "TBK Madde 49",
        "TBK Madde 50",
        "TBK Madde 51",
        "TBK Madde 52",
        "TBK Madde 53",
        "TBK Madde 54",
        "TBK Madde 55",
        "TBK Madde 56",
        "TBK Madde 58",
        "TBK Madde 66",
        "TBK Madde 67",
        "TBK Madde 68",
        "TBK Madde 69",
        "TBK Madde 71",
        "TBK Madde 112",
        "TBK Madde 114",
        "TBK Madde 117",
        "TBK Madde 118",
        "TBK Madde 123",
        "TBK Madde 124",
        "TBK Madde 125",
        "TBK Madde 136",
        "TBK Madde 138",
        "TBK Madde 146",
        "TBK Madde 147",
        "TBK Madde 344",
        "TBK Madde 347",
        "TBK Madde 350",
        "TBK Madde 352",
        "TBK Madde 354",
        # Türk Medeni Kanunu (TMK)
        "TMK Madde 1",
        "TMK Madde 2",
        "TMK Madde 3",
        "TMK Madde 4",
        "TMK Madde 5",
        "TMK Madde 23",
        "TMK Madde 24",
        "TMK Madde 25",
        "TMK Madde 166",
        "TMK Madde 174",
        "TMK Madde 175",
        "TMK Madde 176",
        "TMK Madde 185",
        "TMK Madde 186",
        "TMK Madde 197",
        "TMK Madde 218",
        "TMK Madde 219",
        "TMK Madde 236",
        "TMK Madde 240",
        # Türk Ceza Kanunu (TCK)
        "TCK Madde 29",
        "TCK Madde 43",
        "TCK Madde 53",
        "TCK Madde 58",
        "TCK Madde 61",
        "TCK Madde 62",
        "TCK Madde 81",
        "TCK Madde 82",
        "TCK Madde 86",
        "TCK Madde 87",
        "TCK Madde 106",
        "TCK Madde 125",
        "TCK Madde 141",
        "TCK Madde 142",
        "TCK Madde 148",
        "TCK Madde 149",
        "TCK Madde 151",
        "TCK Madde 155",
        "TCK Madde 157",
        "TCK Madde 158",
        "TCK Madde 188",
        "TCK Madde 191",
        "TCK Madde 204",
        "TCK Madde 207",
        "TCK Madde 241",
        "TCK Madde 245",
        # Hukuk Muhakemeleri Kanunu (HMK)
        "HMK Madde 1",
        "HMK Madde 2",
        "HMK Madde 4",
        "HMK Madde 6",
        "HMK Madde 107",
        "HMK Madde 114",
        "HMK Madde 115",
        "HMK Madde 116",
        "HMK Madde 119",
        "HMK Madde 176",
        "HMK Madde 177",
        "HMK Madde 200",
        "HMK Madde 202",
        "HMK Madde 353",
        "HMK Madde 355",
        "HMK Madde 356",
        "HMK Madde 371",
        "HMK Madde 373",
        # Türk Ticaret Kanunu (TTK)
        "TTK Madde 4",
        "TTK Madde 5",
        "TTK Madde 11",
        "TTK Madde 12",
        "TTK Madde 14",
        "TTK Madde 18",
        "TTK Madde 19",
        "TTK Madde 20",
        "TTK Madde 124",
        "TTK Madde 329",
        "TTK Madde 330",
        "TTK Madde 340",
        "TTK Madde 343",
        "TTK Madde 349",
        "TTK Madde 553",
        "TTK Madde 556",
        # İş Kanunu (4857)
        "İş Kanunu Madde 4",
        "İş Kanunu Madde 17",
        "İş Kanunu Madde 18",
        "İş Kanunu Madde 19",
        "İş Kanunu Madde 20",
        "İş Kanunu Madde 21",
        "İş Kanunu Madde 22",
        "İş Kanunu Madde 24",
        "İş Kanunu Madde 25",
        "İş Kanunu Madde 32",
        "İş Kanunu Madde 34",
        "İş Kanunu Madde 41",
        "İş Kanunu Madde 46",
        "İş Kanunu Madde 57",
        "İş Kanunu Madde 63",
        # KVKK (6698)
        "KVKK Madde 3",
        "KVKK Madde 4",
        "KVKK Madde 5",
        "KVKK Madde 6",
        "KVKK Madde 7",
        "KVKK Madde 8",
        "KVKK Madde 9",
        "KVKK Madde 10",
        "KVKK Madde 11",
        "KVKK Madde 12",
        "KVKK Madde 15",
        "KVKK Madde 18",
        # Anayasa (Constitution)
        "Anayasa Madde 2",
        "Anayasa Madde 10",
        "Anayasa Madde 13",
        "Anayasa Madde 17",
        "Anayasa Madde 19",
        "Anayasa Madde 26",
        "Anayasa Madde 28",
        "Anayasa Madde 35",
        "Anayasa Madde 36",
        "Anayasa Madde 38",
        "Anayasa Madde 40",
        "Anayasa Madde 90",
        "Anayasa Madde 125",
        "Anayasa Madde 138",
        "Anayasa Madde 148",
        "Anayasa Madde 152",
        "Anayasa Madde 153",
    }
)

# ---------------------------------------------------------------------------
# B-8 FIX: Dynamic reference set loader with HuggingFace fallback
# ---------------------------------------------------------------------------

_HF_LAW_NAME_MAP: dict[str, str] = {
    "Türk Borçlar Kanunu": "TBK",
    "Türk Medeni Kanunu": "TMK",
    "Türk Ceza Kanunu": "TCK",
    "Hukuk Muhakemeleri Kanunu": "HMK",
    "Türk Ticaret Kanunu": "TTK",
    "İş Kanunu": "İş Kanunu",
    "Kişisel Verilerin Korunması Kanunu": "KVKK",
    "Anayasa": "Anayasa",
}


def load_reference_set(
    *,
    hf_repo: str = "omersaidd/Kanunlar",
    timeout_seconds: float = 15.0,
) -> frozenset[str]:
    """Load the valid law article reference set, preferring HuggingFace.

    Attempts to load the canonical Turkish law dataset from the HuggingFace
    Hub.  On success, rows are normalised into ``"{CODE} Madde {number}"``
    format and merged with the hardcoded ``VALID_LAW_ARTICLES``.

    On ANY failure — missing ``datasets`` library, network timeout, HTTP
    error, malformed schema — silently falls back to ``VALID_LAW_ARTICLES``.

    Returns:
        A ``frozenset[str]`` of valid Turkish law article references.
    """
    try:
        from datasets import load_dataset  # type: ignore

        logger.info(
            "REFERENCE_SET | Attempting dynamic load from HuggingFace: %s",
            hf_repo,
        )
        ds = load_dataset(hf_repo, split="train")

        dynamic_articles: set[str] = set()
        for row in ds:
            law_name = None
            article_no = None
            for col in ("kanun_adi", "law_name", "kanun"):
                if col in row and row[col]:
                    law_name = str(row[col]).strip()
                    break
            for col in ("madde_no", "article_number", "madde", "article"):
                if col in row and row[col] is not None:
                    article_no = str(row[col]).strip()
                    break
            if law_name and article_no:
                code = _HF_LAW_NAME_MAP.get(law_name, law_name)
                dynamic_articles.add(f"{code} Madde {article_no}")

        if dynamic_articles:
            merged = VALID_LAW_ARTICLES | frozenset(dynamic_articles)
            logger.info(
                "REFERENCE_SET | HF loaded: hardcoded=%d HF=%d merged=%d",
                len(VALID_LAW_ARTICLES),
                len(dynamic_articles),
                len(merged),
            )
            return merged

        logger.warning(
            "REFERENCE_SET | HF dataset loaded but 0 articles extracted. "
            "Falling back to hardcoded set."
        )
        return VALID_LAW_ARTICLES

    except ImportError:
        logger.info(
            "REFERENCE_SET | 'datasets' not installed. Using hardcoded set (%d).",
            len(VALID_LAW_ARTICLES),
        )
        return VALID_LAW_ARTICLES
    except Exception as exc:
        logger.warning(
            "REFERENCE_SET | HF load failed: %s. Falling back to hardcoded set.",
            exc,
        )
        return VALID_LAW_ARTICLES


# Module-level initialization — load reference set once at import time
_ACTIVE_REFERENCE_SET: frozenset[str] = load_reference_set()

# Normalised lookup set for fuzzy matching (lowercase, stripped)
_NORMALISED_VALID: frozenset[str] = frozenset(
    v.lower().strip() for v in _ACTIVE_REFERENCE_SET
)

# Legal edge relation types to audit
LEGAL_RELATIONS: frozenset[str] = frozenset({"DAYANIR", "ATIF_YAPAR"})

# Default poisoning threshold (5%)
DEFAULT_THRESHOLD = 0.05


# ---------------------------------------------------------------------------
# Audit result structure
# ---------------------------------------------------------------------------


@dataclass
class PoisonedEdge:
    """A single poisoned edge detected during audit."""

    edge_id: str
    source_entity: str
    target_entity: str
    relation_type: str
    agent_id: str


@dataclass
class AuditResult:
    """Complete audit result for a single agent's legal graph."""

    agent_id: str
    total_legal_edges: int = 0
    valid_edges: int = 0
    poisoned_edges: list[PoisonedEdge] = field(default_factory=list)

    @property
    def invalid_count(self) -> int:
        return len(self.poisoned_edges)

    @property
    def poisoning_rate(self) -> float:
        if self.total_legal_edges == 0:
            return 0.0
        return self.invalid_count / self.total_legal_edges

    @property
    def passed(self) -> bool:
        return self.poisoning_rate <= DEFAULT_THRESHOLD

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"AUDIT_{status} | agent_id={self.agent_id} "
            f"total_legal_edges={self.total_legal_edges} "
            f"valid={self.valid_edges} "
            f"poisoned={self.invalid_count} "
            f"poisoning_rate={self.poisoning_rate:.4f} "
            f"threshold={DEFAULT_THRESHOLD}"
        )


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def normalise_entity(name: str) -> str:
    """Normalise an entity name for comparison against the reference set.

    Handles common REBEL output formats:
        - "TBK m.49"      → "tbk madde 49"
        - "TBK Madde 49"  → "tbk madde 49"
        - "TBK m. 49"     → "tbk madde 49"
        - "TMK md.2"      → "tmk madde 2"
    """
    s = name.strip().lower()
    # Expand common abbreviations
    for abbr in ("m.", "md.", "mad."):
        s = s.replace(abbr, "madde ")
    # Collapse multiple spaces
    s = " ".join(s.split())
    return s


def is_valid_law(entity_name: str) -> bool:
    """Check if an entity name matches any known valid Turkish law article."""
    return normalise_entity(entity_name) in _NORMALISED_VALID


# ---------------------------------------------------------------------------
# Core audit logic — direct aiosqlite (no DAO dependency for standalone use)
# ---------------------------------------------------------------------------


async def audit_graph(
    db_path: str,
    agent_id: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> AuditResult:
    """Run the graph poisoning audit against a live KùzuDB database.

    Connects to the KùzuDB database directory (derived from db_path), queries
    all Observed edges, resolves target node entity names, and validates them
    against the canonical Turkish law reference set.

    Args:
        db_path: Path to the MESA SQLite database file (Kuzu path derived by replacing .db with _graph).
        agent_id: Agent ID to scope the audit (RLS / Zero-Trust).
        threshold: Maximum acceptable poisoning rate (default 0.05).

    Returns:
        AuditResult with full breakdown of valid vs poisoned edges.
    """
    result = AuditResult(agent_id=agent_id)

    # Derive KuzuDB path from the SQLite db path
    graph_path = db_path.replace(".db", "_graph")

    # Initialize KuzuGraphProvider
    graph_provider = KuzuGraphProvider(graph_path)
    await graph_provider.initialize()

    try:
        # Query all active legal edges using Cypher
        # Zero-Trust isolation enforced via agent_id properties
        cypher = (
            "MATCH (src:Entity)-[e:Observed]->(tgt:Entity) "
            "WHERE src.agent_id = $agent_id "
            "  AND tgt.agent_id = $agent_id "
            "  AND e.agent_id = $agent_id "
            "RETURN src.name, tgt.name"
        )

        rows = await graph_provider.execute_query(cypher, {"agent_id": agent_id})

        result.total_legal_edges = len(rows)

        for row in rows:
            source_name = row[0]
            target_name = row[1]
            if is_valid_law(target_name):
                result.valid_edges += 1
            else:
                result.poisoned_edges.append(
                    PoisonedEdge(
                        edge_id="kuzu-edge",  # Legacy ID field no longer explicitly stored in Kuzu
                        source_entity=source_name,
                        target_entity=target_name,
                        relation_type="Observed",  # Unified relation type in Kuzu
                        agent_id=agent_id,
                    )
                )
    finally:
        await graph_provider.close()

    return result


# ---------------------------------------------------------------------------
# Fatal guardrail enforcement
# ---------------------------------------------------------------------------


def enforce_guardrail(
    result: AuditResult, *, threshold: float = DEFAULT_THRESHOLD
) -> None:
    """Enforce the fatal poisoning rate guardrail.

    If poisoning_rate > threshold → raise GraphPoisoningError and sys.exit(1).
    """
    print(result.summary())

    if result.total_legal_edges == 0:
        print(
            "AUDIT_WARN | No legal edges found in graph. "
            "Skipping guardrail (nothing to audit).",
            file=sys.stderr,
        )
        return

    if result.poisoning_rate > threshold:
        # Log every poisoned edge for forensic analysis
        print(
            f"\nGRAPH_POISONING_DETECTED | {result.invalid_count} invalid edges:",
            file=sys.stderr,
        )
        for pe in result.poisoned_edges:
            print(
                f"  ✗ edge_id={pe.edge_id} "
                f"source={pe.source_entity!r} "
                f"-[{pe.relation_type}]-> "
                f"target={pe.target_entity!r} (HALLUCINATED)",
                file=sys.stderr,
            )

        msg = (
            f"FATAL: Graph poisoning rate {result.poisoning_rate:.4f} "
            f"exceeds threshold {threshold:.4f}. "
            f"{result.invalid_count}/{result.total_legal_edges} legal edges "
            f"reference non-existent law articles. "
            f"Deployment BLOCKED."
        )
        print(f"\n{msg}", file=sys.stderr)
        raise GraphPoisoningError(msg)
    else:
        print(
            f"AUDIT_PASS | Poisoning rate {result.poisoning_rate:.4f} "
            f"<= threshold {threshold:.4f}. Graph integrity verified.",
        )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for the Legal Graph Poisoning Audit."""
    parser = argparse.ArgumentParser(
        description="MESA v0.4.0 — Legal Graph Poisoning Audit",
    )
    parser.add_argument(
        "--db",
        type=str,
        required=True,
        help="Path to the MESA SQLite database file.",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        required=True,
        help="Agent ID to scope the audit (RLS).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Max acceptable poisoning rate (default: {DEFAULT_THRESHOLD})",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    result = asyncio.run(audit_graph(args.db, args.agent_id, threshold=args.threshold))

    try:
        enforce_guardrail(result, threshold=args.threshold)
    except GraphPoisoningError:
        sys.exit(1)


if __name__ == "__main__":
    main()
