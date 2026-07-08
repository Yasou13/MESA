# MESA v0.3.0 — Phase 0 Golden Dataset Schema
# Pydantic V2 strict schema for the 100-question evaluation benchmark.
# Covers Legal, Financial, and Code domains with deliberate chronological
# contradictions embedded in context_fragments.
"""
Strict Pydantic V2 models for the MESA Golden Dataset.

Schema hierarchy:
    DatasetEntry  — single QA pair with contradiction-bearing context
    GoldenDataset — root container enforcing exactly 100 entries and
                    domain-balance invariants (Legal=35, Financial=35, Code=30).
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import List

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# Domain enumeration — constrained to the three regulated verticals
# ---------------------------------------------------------------------------
class Domain(str, Enum):
    """Regulated industry verticals targeted by the evaluation pipeline."""

    LEGAL = "legal"
    FINANCIAL = "financial"
    CODE = "code"


# ---------------------------------------------------------------------------
# Metadata sub-model — reasoning-type annotations per entry
# ---------------------------------------------------------------------------
class EntryMetadata(BaseModel):
    """Auxiliary metadata that classifies the reasoning challenge type.

    complexity_tier:
        1 = single-hop retrieval
        2 = two-hop cross-reference
        3 = multi-hop with contradiction resolution
    """

    complexity_tier: int = Field(
        ...,
        ge=1,
        le=3,
        description="Tier 1: single-hop, Tier 2: two-hop, Tier 3: multi-hop/contradiction",
    )
    requires_chronology: bool = Field(
        default=False,
        description="True when solving requires ordered timeline traversal",
    )
    is_contradictory: bool = Field(
        default=False,
        description="True when context_fragments contain conflicting temporal facts",
    )
    is_synthetic: bool = Field(
        default=False,
        description="True when the entry was programmatically generated",
    )


# ---------------------------------------------------------------------------
# Core dataset entry
# ---------------------------------------------------------------------------
class DatasetEntry(BaseModel):
    """A single Golden Dataset QA pair.

    context_fragments deliberately inject chronological contradictions so that
    naive single-hop retrieval systems fail; the model must reconcile timelines
    across fragments to arrive at ground_truth_answer.
    """

    id: str = Field(
        ...,
        description="Strict UUIDv4 identifier for this entry",
    )
    query: str = Field(
        ...,
        min_length=10,
        description="Natural-language question (multi-hop or standard)",
    )
    context_fragments: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "Ordered list of context passages. MUST contain deliberate "
            "chronological contradictions for Tier-3 entries."
        ),
    )
    ground_truth_answer: str = Field(
        ...,
        min_length=1,
        description="Definitive gold answer resolved from context_fragments",
    )
    required_reasoning_hops: int = Field(
        ...,
        ge=1,
        le=5,
        description="Number of logical hops needed to derive the answer",
    )
    domain: Domain = Field(
        ...,
        description="Regulated target industry vertical",
    )
    metadata: EntryMetadata = Field(  # type: ignore[arg-type]
        default_factory=EntryMetadata,  # type: ignore[arg-type]
        description="Reasoning-type annotations for this entry",
    )

    # -- Validators ----------------------------------------------------------

    @field_validator("id")
    @classmethod
    def validate_uuid4(cls, v: str) -> str:
        """Enforce strict UUIDv4 format."""
        try:
            parsed = uuid.UUID(v, version=4)
        except ValueError as exc:
            raise ValueError(f"id must be a valid UUIDv4 string, got: {v!r}") from exc
        # Canonical lowercase representation
        return str(parsed)

    @field_validator("context_fragments")
    @classmethod
    def validate_fragments_non_empty(cls, v: List[str]) -> List[str]:
        """Reject empty or whitespace-only fragments."""
        for idx, fragment in enumerate(v):
            if not fragment.strip():
                raise ValueError(
                    f"context_fragments[{idx}] must not be empty or whitespace-only"
                )
        return v


# ---------------------------------------------------------------------------
# Root container — enforces the 100-entry + domain-balance invariants
# ---------------------------------------------------------------------------

# Required distribution per domain across the 100-entry dataset
DOMAIN_DISTRIBUTION: dict[Domain, int] = {
    Domain.LEGAL: 35,
    Domain.FINANCIAL: 35,
    Domain.CODE: 30,
}

# Synthetic entries must comprise exactly 30% of the dataset
SYNTHETIC_RATIO = 0.30
DATASET_SIZE = 100
EXPECTED_SYNTHETIC_COUNT = int(DATASET_SIZE * SYNTHETIC_RATIO)  # 30


class GoldenDataset(BaseModel):
    """Root model for the 100-question Golden Dataset.

    Invariants enforced at validation time:
        1. Exactly 100 entries.
        2. Domain distribution: Legal=35, Financial=35, Code=30.
        3. Synthetic ratio: exactly 30 entries have metadata.is_synthetic=True.
        4. All entry IDs are unique.
    """

    entries: List[DatasetEntry] = Field(
        ...,
        min_length=DATASET_SIZE,
        max_length=DATASET_SIZE,
        description=f"Exactly {DATASET_SIZE} QA entries",
    )

    @model_validator(mode="after")
    def enforce_dataset_invariants(self) -> "GoldenDataset":
        """Post-init validation of global dataset constraints."""
        # 1. Unique IDs
        ids = [e.id for e in self.entries]
        if len(set(ids)) != len(ids):
            dupes = [x for x in ids if ids.count(x) > 1]
            raise ValueError(f"Duplicate entry IDs detected: {set(dupes)}")

        # 2. Domain distribution
        domain_counts: dict[Domain, int] = {d: 0 for d in Domain}
        for entry in self.entries:
            domain_counts[entry.domain] += 1

        for domain, expected in DOMAIN_DISTRIBUTION.items():
            actual = domain_counts[domain]
            if actual != expected:
                raise ValueError(
                    f"Domain '{domain.value}' must have exactly {expected} entries, "
                    f"found {actual}"
                )

        # 3. Synthetic ratio
        synthetic_count = sum(1 for e in self.entries if e.metadata.is_synthetic)
        if synthetic_count != EXPECTED_SYNTHETIC_COUNT:
            raise ValueError(
                f"Exactly {EXPECTED_SYNTHETIC_COUNT} synthetic entries required, "
                f"found {synthetic_count}"
            )

        return self
