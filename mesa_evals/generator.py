# MESA v0.3.0 — Phase 0 Synthetic Dataset Generator
# Deterministically bootstraps the 30% synthetic portion (30 entries) of the
# 100-question Golden Dataset.  Covers Legal, Financial, and Code domains.
#
# DESIGN CONSTRAINTS:
#   - Strict deterministic seeding (random.seed + uuid.UUID for reproducibility)
#   - Edge-case generation: temporal logic conflicts, cross-domain multi-hop,
#     contradictory forward-looking statements
#   - Every entry passes the Pydantic schema defined in mesa_evals.dataset
#
# Usage:
#   python -m mesa_evals.generator                      # stdout JSON
#   python -m mesa_evals.generator --out golden.json    # write to file
"""
Deterministic synthetic generator for the MESA Golden Dataset (Phase 0).

Generates exactly 30 entries (10 Legal, 10 Financial, 10 Code) with
deliberate chronological contradictions and multi-hop reasoning requirements.
The remaining 70 entries are expected to be sourced from real-world documents
via the ingestion pipeline.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from typing import Any

from mesa_evals.dataset import DatasetEntry, Domain, EntryMetadata

# ---------------------------------------------------------------------------
# Deterministic seeding — guarantees identical output across CI/CD runs
# ---------------------------------------------------------------------------
SEED = 20260521  # MESA Phase-0 epoch — NEVER change after baseline lock
random.seed(SEED)

# We derive UUIDs from a seeded namespace so they are reproducible
_UUID_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d")
_uuid_counter = 0


def _deterministic_uuid(label: str) -> str:
    """Generate a reproducible UUIDv4-compatible string from a label.

    Uses UUID5 (SHA-1 based) internally for determinism, then normalises the
    version nibble to 4 so downstream validators accept it as UUIDv4.
    """
    raw = uuid.uuid5(_UUID_NAMESPACE, label)
    # Patch version nibble → 4 (UUIDv4) while preserving rest of the hash
    hex_str = raw.hex
    # Version nibble is at position 12 (0-indexed)
    patched = hex_str[:12] + "4" + hex_str[13:]
    # Variant bits (position 16) must be 8, 9, a, or b
    variant_char = hex(8 | (int(patched[16], 16) & 0x3))[2:]
    patched = patched[:16] + variant_char + patched[17:]
    return str(uuid.UUID(patched))


# ---------------------------------------------------------------------------
# Template pools — domain-specific building blocks for synthetic generation
# ---------------------------------------------------------------------------

# ---- LEGAL DOMAIN --------------------------------------------------------
_LEGAL_CASES = [
    {
        "court": "Yargıtay 11. Hukuk Dairesi",
        "case_no": "2024/1847",
        "subject": "trademark infringement",
        "ruling_date": "2024-03-15",
        "amendment_date": "2025-01-22",
        "original_ruling": "upheld the injunction against defendant",
        "amended_ruling": "vacated the injunction due to procedural deficiency",
    },
    {
        "court": "Yargıtay 4. Ceza Dairesi",
        "case_no": "2023/9421",
        "subject": "data protection violation under KVKK Art. 12",
        "ruling_date": "2023-06-10",
        "amendment_date": "2024-11-05",
        "original_ruling": "imposed administrative fine of ₺2.5M",
        "amended_ruling": "reduced fine to ₺850K following constitutional review",
    },
    {
        "court": "Danıştay 10. Daire",
        "case_no": "2024/3312",
        "subject": "environmental permit revocation",
        "ruling_date": "2024-07-20",
        "amendment_date": "2025-02-14",
        "original_ruling": "annulled the operating permit",
        "amended_ruling": "reinstated permit with enhanced monitoring obligations",
    },
    {
        "court": "Yargıtay 9. Hukuk Dairesi",
        "case_no": "2023/15204",
        "subject": "wrongful termination and severance",
        "ruling_date": "2023-09-28",
        "amendment_date": "2024-08-30",
        "original_ruling": "awarded ₺180K severance to plaintiff",
        "amended_ruling": "increased award to ₺340K including moral damages",
    },
    {
        "court": "Anayasa Mahkemesi",
        "case_no": "2024/AYM-77",
        "subject": "constitutional challenge to internet censorship regulation",
        "ruling_date": "2024-01-18",
        "amendment_date": "2025-04-03",
        "original_ruling": "found the regulation constitutional",
        "amended_ruling": "struck down Article 7(3) as unconstitutional",
    },
]

# ---- FINANCIAL DOMAIN ----------------------------------------------------
_FINANCIAL_ENTITIES = [
    {
        "company": "Anatolian Technologies A.Ş.",
        "ticker": "ANTK",
        "q2_projection": "net revenue of $42M with 18% YoY growth",
        "q4_actual": "net loss of $3.7M due to restructuring charges",
        "liability_type": "contingent warranty reserve",
        "original_amount": "$12.4M",
        "adjusted_amount": "$8.1M",
        "fiscal_year": "FY2024",
    },
    {
        "company": "Bosphorus Dynamics Corp.",
        "ticker": "BSPH",
        "q2_projection": "EBITDA margin expansion to 24%",
        "q4_actual": "EBITDA margin contraction to 11.3% after impairment",
        "liability_type": "deferred tax obligation",
        "original_amount": "$28.9M",
        "adjusted_amount": "$41.2M",
        "fiscal_year": "FY2024",
    },
    {
        "company": "Cappadocia FinServ Ltd.",
        "ticker": "CPFS",
        "q2_projection": "loan loss provision decrease of 15%",
        "q4_actual": "loan loss provision increase of 32% following regulatory stress test",
        "liability_type": "expected credit loss reserve",
        "original_amount": "$55.0M",
        "adjusted_amount": "$72.6M",
        "fiscal_year": "FY2025",
    },
    {
        "company": "Derinkuyu Mining PLC",
        "ticker": "DKYU",
        "q2_projection": "operating cash flow of $95M",
        "q4_actual": "operating cash flow of $61M after commodity price collapse",
        "liability_type": "asset retirement obligation",
        "original_amount": "$18.5M",
        "adjusted_amount": "$26.3M",
        "fiscal_year": "FY2024",
    },
    {
        "company": "Ephesus Biotech Inc.",
        "ticker": "EPBT",
        "q2_projection": "FDA Phase III trial completion by Q3",
        "q4_actual": "FDA issued Complete Response Letter; trial extended to Q2 FY2026",
        "liability_type": "clinical trial accrued liability",
        "original_amount": "$7.8M",
        "adjusted_amount": "$14.2M",
        "fiscal_year": "FY2025",
    },
]

# ---- CODE DOMAIN ---------------------------------------------------------
_CODE_SCENARIOS = [
    {
        "library": "mesa-memory",
        "module": "ConsolidationLoop",
        "v1_api": "consolidate(batch_size=32)",
        "v2_api": "BatchOrchestrator.run(config=BatchConfig(size=32))",
        "v1_date": "2024-06-01",
        "v2_date": "2025-03-15",
        "breaking_change": "ConsolidationLoop.consolidate() removed; migrate to BatchOrchestrator",
        "deprecation_window": "v0.2.x → v0.3.0",
    },
    {
        "library": "fastapi",
        "module": "Depends",
        "v1_api": "Depends(get_db, use_cache=True)",
        "v2_api": "Depends(get_db)  # use_cache removed; caching moved to middleware",
        "v1_date": "2024-01-10",
        "v2_date": "2025-02-28",
        "breaking_change": "use_cache parameter removed from Depends; use CacheMiddleware instead",
        "deprecation_window": "v0.111.x → v0.115.0",
    },
    {
        "library": "numpy",
        "module": "np.matrix",
        "v1_api": "np.matrix([[1, 2], [3, 4]])",
        "v2_api": "np.array([[1, 2], [3, 4]])  # np.matrix fully removed",
        "v1_date": "2018-07-01",
        "v2_date": "2025-01-01",
        "breaking_change": "np.matrix class removed; use np.array or np.asarray",
        "deprecation_window": "v1.15 → v2.2.0",
    },
    {
        "library": "pydantic",
        "module": "BaseModel.dict()",
        "v1_api": "model.dict(exclude_unset=True)",
        "v2_api": "model.model_dump(exclude_unset=True)",
        "v1_date": "2022-06-01",
        "v2_date": "2024-07-01",
        "breaking_change": ".dict() removed in V2; use .model_dump()",
        "deprecation_window": "v1.x → v2.0",
    },
    {
        "library": "tensorflow",
        "module": "tf.contrib",
        "v1_api": "tf.contrib.layers.fully_connected(x, 128)",
        "v2_api": "tf.keras.layers.Dense(128)(x)",
        "v1_date": "2019-09-30",
        "v2_date": "2024-03-01",
        "breaking_change": "tf.contrib namespace removed; use tf.keras equivalents",
        "deprecation_window": "v1.15 → v2.16",
    },
]


# ---------------------------------------------------------------------------
# Generator functions — one per domain
# ---------------------------------------------------------------------------


def _generate_legal_entries() -> list[dict[str, Any]]:
    """Generate 10 synthetic legal entries with temporal contradictions.

    Strategy: each entry presents an original ruling date and a later
    amendment that reverses or modifies the outcome.  The query requires
    the model to identify which ruling is authoritative based on chronological
    ordering.
    """
    entries: list[dict[str, Any]] = []

    for i, case in enumerate(_LEGAL_CASES):
        # --- Entry A: Straightforward timeline contradiction (Tier 3) ------
        entry_a = DatasetEntry(
            id=_deterministic_uuid(f"legal-a-{i}"),
            query=(
                f"In {case['court']} case {case['case_no']} regarding "
                f"{case['subject']}, what is the current binding ruling as of "
                f"{case['amendment_date']}?"
            ),
            context_fragments=[
                # Fragment 1: original ruling
                f"On {case['ruling_date']}, {case['court']} in case "
                f"{case['case_no']} {case['original_ruling']}.",
                # Fragment 2: contradicting amendment (later date)
                f"On {case['amendment_date']}, {case['court']} issued a "
                f"corrective decision in case {case['case_no']} and "
                f"{case['amended_ruling']}.",
                # Fragment 3: misleading restatement of original
                f"Court records from {case['ruling_date']} confirm that "
                f"{case['court']} {case['original_ruling']} in the matter "
                f"of {case['subject']}.",
            ],
            ground_truth_answer=(
                f"The binding ruling as of {case['amendment_date']} is that "
                f"{case['court']} {case['amended_ruling']}. The original "
                f"decision from {case['ruling_date']} was superseded."
            ),
            required_reasoning_hops=3,
            domain=Domain.LEGAL,
            metadata=EntryMetadata(
                complexity_tier=3,
                requires_chronology=True,
                is_contradictory=True,
                is_synthetic=True,
            ),
        )
        entries.append(entry_a.model_dump())

        # --- Entry B: Cross-reference hop (Tier 2) -------------------------
        # Link to a financial entity for cross-domain reasoning
        fin = _FINANCIAL_ENTITIES[i % len(_FINANCIAL_ENTITIES)]
        entry_b = DatasetEntry(
            id=_deterministic_uuid(f"legal-b-{i}"),
            query=(
                f"Based on the {case['court']} amended ruling in case "
                f"{case['case_no']}, how would the {fin['liability_type']} "
                f"of {fin['company']} ({fin['ticker']}) be affected?"
            ),
            context_fragments=[
                f"On {case['amendment_date']}, {case['court']} "
                f"{case['amended_ruling']} in case {case['case_no']}.",
                f"{fin['company']} ({fin['ticker']}) disclosed a "
                f"{fin['liability_type']} of {fin['original_amount']} in "
                f"their {fin['fiscal_year']} 10-K filing.",
                f"Legal counsel noted that the outcome of case "
                f"{case['case_no']} directly affects the valuation of "
                f"{fin['company']}'s {fin['liability_type']}.",
            ],
            ground_truth_answer=(
                f"The amended ruling ({case['amended_ruling']}) implies a "
                f"reassessment of {fin['company']}'s {fin['liability_type']} "
                f"from {fin['original_amount']} to {fin['adjusted_amount']}."
            ),
            required_reasoning_hops=2,
            domain=Domain.LEGAL,
            metadata=EntryMetadata(
                complexity_tier=2,
                requires_chronology=True,
                is_contradictory=False,
                is_synthetic=True,
            ),
        )
        entries.append(entry_b.model_dump())

    return entries


def _generate_financial_entries() -> list[dict[str, Any]]:
    """Generate 10 synthetic financial entries with projection/actual conflicts.

    Strategy: Q2 forward-looking projections contradict Q4 actuals.  The model
    must weigh the authoritative finalised numbers against optimistic guidance.
    """
    entries: list[dict[str, Any]] = []

    for i, fin in enumerate(_FINANCIAL_ENTITIES):
        # --- Entry A: Projection vs. actual contradiction (Tier 3) ---------
        entry_a = DatasetEntry(
            id=_deterministic_uuid(f"financial-a-{i}"),
            query=(
                f"Despite the Q2 forward-looking statement from "
                f"{fin['company']} ({fin['ticker']}), what was the actual "
                f"financial outcome reported in the Q4 {fin['fiscal_year']} "
                f"earnings?"
            ),
            context_fragments=[
                # Fragment 1: optimistic Q2 projection
                f"In the Q2 {fin['fiscal_year']} earnings call, "
                f"{fin['company']} management projected {fin['q2_projection']}.",
                # Fragment 2: contradicting Q4 actual
                f"The audited Q4 {fin['fiscal_year']} 10-K filing for "
                f"{fin['company']} ({fin['ticker']}) reported "
                f"{fin['q4_actual']}.",
                # Fragment 3: analyst report echoing the Q2 projection
                f"Sell-side analysts maintained their model reflecting "
                f"{fin['q2_projection']} through Q3 {fin['fiscal_year']}, "
                f"citing management guidance.",
            ],
            ground_truth_answer=(
                f"The actual outcome was {fin['q4_actual']}, superseding "
                f"the Q2 projection of {fin['q2_projection']}. The audited "
                f"10-K is the authoritative source."
            ),
            required_reasoning_hops=3,
            domain=Domain.FINANCIAL,
            metadata=EntryMetadata(
                complexity_tier=3,
                requires_chronology=True,
                is_contradictory=True,
                is_synthetic=True,
            ),
        )
        entries.append(entry_a.model_dump())

        # --- Entry B: Liability adjustment tracing (Tier 2) ----------------
        entry_b = DatasetEntry(
            id=_deterministic_uuid(f"financial-b-{i}"),
            query=(
                f"What is the current {fin['liability_type']} for "
                f"{fin['company']} after the {fin['fiscal_year']} audit "
                f"adjustment?"
            ),
            context_fragments=[
                f"{fin['company']} ({fin['ticker']}) originally booked a "
                f"{fin['liability_type']} of {fin['original_amount']} in "
                f"the preliminary {fin['fiscal_year']} filing.",
                f"Following the year-end audit, the {fin['liability_type']} "
                f"was restated to {fin['adjusted_amount']} per the final "
                f"10-K.",
            ],
            ground_truth_answer=(
                f"The current {fin['liability_type']} is "
                f"{fin['adjusted_amount']}, as restated in the final "
                f"{fin['fiscal_year']} 10-K, replacing the preliminary "
                f"figure of {fin['original_amount']}."
            ),
            required_reasoning_hops=2,
            domain=Domain.FINANCIAL,
            metadata=EntryMetadata(
                complexity_tier=2,
                requires_chronology=False,
                is_contradictory=True,
                is_synthetic=True,
            ),
        )
        entries.append(entry_b.model_dump())

    return entries


def _generate_code_entries() -> list[dict[str, Any]]:
    """Generate 10 synthetic code-domain entries with API migration conflicts.

    Strategy: v1 API documentation contradicts v2 changelog.  The model must
    identify the correct current API surface by resolving version timelines.
    """
    entries: list[dict[str, Any]] = []

    for i, scenario in enumerate(_CODE_SCENARIOS):
        # --- Entry A: Breaking change resolution (Tier 3) ------------------
        entry_a = DatasetEntry(
            id=_deterministic_uuid(f"code-a-{i}"),
            query=(
                f"A codebase uses `{scenario['v1_api']}` from the "
                f"`{scenario['library']}` library. According to the latest "
                f"migration guide ({scenario['v2_date']}), what is the "
                f"correct replacement?"
            ),
            context_fragments=[
                # Fragment 1: old API docs (still indexed)
                f"Documentation ({scenario['v1_date']}): The "
                f"`{scenario['module']}` module provides "
                f"`{scenario['v1_api']}` for standard usage.",
                # Fragment 2: changelog with breaking change
                f"Changelog ({scenario['v2_date']}): BREAKING — "
                f"{scenario['breaking_change']}. Deprecated in "
                f"{scenario['deprecation_window']}.",
                # Fragment 3: community blog still referencing old API
                f"Tutorial (accessed 2024-12-01): 'To use "
                f"{scenario['library']}, simply call "
                f"`{scenario['v1_api']}`.' — Note: this tutorial predates "
                f"the {scenario['deprecation_window']} migration.",
            ],
            ground_truth_answer=(
                f"The correct replacement as of {scenario['v2_date']} is "
                f"`{scenario['v2_api']}`. The old `{scenario['v1_api']}` was "
                f"removed in {scenario['deprecation_window']}."
            ),
            required_reasoning_hops=3,
            domain=Domain.CODE,
            metadata=EntryMetadata(
                complexity_tier=3,
                requires_chronology=True,
                is_contradictory=True,
                is_synthetic=True,
            ),
        )
        entries.append(entry_a.model_dump())

        # --- Entry B: Version timeline ordering (Tier 2) -------------------
        entry_b = DatasetEntry(
            id=_deterministic_uuid(f"code-b-{i}"),
            query=(
                f"In what version range of `{scenario['library']}` was "
                f"`{scenario['v1_api']}` deprecated before removal?"
            ),
            context_fragments=[
                f"Deprecation notice ({scenario['v1_date']}): "
                f"`{scenario['module']}` will be deprecated in a future "
                f"release.",
                f"Release notes ({scenario['v2_date']}): "
                f"`{scenario['module']}` fully removed. Deprecation window: "
                f"{scenario['deprecation_window']}.",
            ],
            ground_truth_answer=(
                f"`{scenario['v1_api']}` was deprecated across the "
                f"{scenario['deprecation_window']} version range and fully "
                f"removed as of {scenario['v2_date']}."
            ),
            required_reasoning_hops=2,
            domain=Domain.CODE,
            metadata=EntryMetadata(
                complexity_tier=2,
                requires_chronology=True,
                is_contradictory=False,
                is_synthetic=True,
            ),
        )
        entries.append(entry_b.model_dump())

    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_synthetic_entries() -> list[dict[str, Any]]:
    """Generate the full 30-entry synthetic block.

    Returns a list of 30 dict-serialised DatasetEntry objects:
        - 10 Legal  (5 Tier-3 + 5 Tier-2)
        - 10 Financial (5 Tier-3 + 5 Tier-2)
        - 10 Code   (5 Tier-3 + 5 Tier-2)

    All entries are deterministically seeded.  Calling this function multiple
    times with the same SEED constant produces identical output.
    """
    # Reset seed at generation time to guarantee idempotency even if the
    # module-level seed was consumed by prior random calls
    random.seed(SEED)

    legal = _generate_legal_entries()
    financial = _generate_financial_entries()
    code = _generate_code_entries()

    all_entries = legal + financial + code

    # Shuffle deterministically for interleaved domain evaluation
    random.shuffle(all_entries)

    assert len(all_entries) == 30, (
        f"Synthetic block must contain exactly 30 entries, got {len(all_entries)}"
    )

    return all_entries


def generate_to_json(indent: int = 2) -> str:
    """Serialize the synthetic block to a pretty-printed JSON string."""
    entries = generate_synthetic_entries()
    return json.dumps(entries, indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for the synthetic generator.

    Usage:
        python -m mesa_evals.generator                    # stdout
        python -m mesa_evals.generator --out output.json  # file
        python -m mesa_evals.generator --validate         # generate + validate
    """
    parser = argparse.ArgumentParser(
        description="MESA Phase 0 Golden Dataset — Synthetic Generator",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output file path. Omit to print to stdout.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate each generated entry against the Pydantic schema.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=SEED,
        help=f"Random seed for deterministic generation (default: {SEED})",
    )
    args = parser.parse_args()

    # Apply custom seed if provided
    random.seed(args.seed)

    entries = generate_synthetic_entries()

    if args.validate:
        # Re-parse each entry through Pydantic to surface validation errors
        for idx, raw in enumerate(entries):
            try:
                DatasetEntry.model_validate(raw)
            except Exception as exc:
                print(
                    f"VALIDATION FAILURE at synthetic entry {idx}: {exc}",
                    file=sys.stderr,
                )
                sys.exit(1)
        print(
            f"✓ All {len(entries)} synthetic entries pass schema validation.",
            file=sys.stderr,
        )

    output = json.dumps(entries, indent=2, ensure_ascii=False)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"✓ Wrote {len(entries)} entries to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
