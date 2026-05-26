"""MESA v0.4.0 — Phase 2 (Part 1): Legal Golden Dataset Generator.

Generates 200 highly complex, multi-hop legal evaluation entries based on
Turkish Supreme Court (Yargıtay) decisions and Turkish Law (Kanunlar).

Data Relationship: (Yargıtay_Kararı) -[DAYANIR]-> (Kanun_Maddesi)

Question Types:
  1. Entity Resolution:   "Bu kararda geçen 'TBK m.49' atıfı hangi kanuna aittir?"
  2. Relational Accuracy:  "Karar 2023/154 hangi kanun maddesine dayanarak hüküm kurmuştur?"
  3. Semantic Search:       "Kusursuz sorumluluk hallerine atıf yapan ... kararlarının dayandığı yasa maddeleri nelerdir?"

Usage:
    python -m mesa_evals.legal_generator
    python -m mesa_evals.legal_generator --out custom_path.json
    python -m mesa_evals.legal_generator --count 300
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid

# ---------------------------------------------------------------------------
# Deterministic seeding — MESA v0.4.0 Phase 2 epoch
# ---------------------------------------------------------------------------
SEED = 20260526
random.seed(SEED)

# ---------------------------------------------------------------------------
# Template pools — Turkish legal domain building blocks
# ---------------------------------------------------------------------------

LAWS = [
    ("TBK", "49", "Türk Borçlar Kanunu", "Haksız fiil sorumluluğu"),
    (
        "TMK",
        "2",
        "Türk Medeni Kanunu",
        "Dürüstlük kuralı ve hakkın kötüye kullanılması",
    ),
    ("TCK", "157", "Türk Ceza Kanunu", "Dolandırıcılık suçu"),
    ("HMK", "107", "Hukuk Muhakemeleri Kanunu", "Belirsiz alacak davası"),
    ("TTK", "18", "Türk Ticaret Kanunu", "Tacir olmanın hükümleri"),
]

COURTS = [
    "Yargıtay Hukuk Genel Kurulu",
    "Yargıtay 4. Hukuk Dairesi",
    "Yargıtay 11. Hukuk Dairesi",
]

SUBJECTS = [
    "kusursuz sorumluluk halleri",
    "haksız fiilden doğan tazminat",
    "sözleşmenin feshi",
    "ticari işletme devri",
    "manevi tazminat talebi",
    "miras hukuku uyuşmazlığı",
    "iş kazası tazminatı",
    "kira sözleşmesi ihlali",
]

# ---------------------------------------------------------------------------
# Query templates — one per question type
# ---------------------------------------------------------------------------
Q_ENTITY = "Bu kararda geçen '{law_short} m.{article}' atıfı hangi kanuna aittir?"
Q_RELATIONAL = "Karar {case_no} hangi kanun maddesine dayanarak hüküm kurmuştur?"
Q_SEMANTIC = (
    "{subject} konusunda {court} kararlarının dayandığı yasa maddeleri nelerdir?"
)

# Context template — synthesises a realistic Yargıtay decision fragment
CTX_TEMPLATE = (
    "{court}, {date} tarihli {case_no} sayılı kararında, {law_full} "
    "({law_short}) m.{article} hükmüne dayanarak {subject} konusunda "
    "hüküm kurmuştur. Mahkeme, {law_short} m.{article} kapsamında "
    "{desc} ilkesini uygulamıştır."
)


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------


def generate_legal_dataset(count: int = 200) -> list[dict]:
    """Generate `count` legal evaluation entries with deterministic seeding.

    Each entry models the relationship:
        (Yargıtay_Kararı) -[DAYANIR]-> (Kanun_Maddesi)

    Returns a list of dicts compatible with mesa_evals.dataset.DatasetEntry.
    """
    random.seed(SEED)
    entries: list[dict] = []
    qtypes = ["entity", "relational", "semantic"]

    for i in range(count):
        law_short, article, law_full, desc = random.choice(LAWS)
        court = random.choice(COURTS)
        year = random.randint(2020, 2025)
        seq = random.randint(100, 9999)
        case_no = f"{year}/{seq}"
        date = f"{year}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        subject = random.choice(SUBJECTS)
        qtype = qtypes[i % 3]

        ctx = CTX_TEMPLATE.format(
            court=court,
            date=date,
            case_no=case_no,
            law_full=law_full,
            law_short=law_short,
            article=article,
            subject=subject,
            desc=desc,
        )

        if qtype == "entity":
            query = Q_ENTITY.format(law_short=law_short, article=article)
            answer = f"'{law_short} m.{article}' atıfı {law_full}'na aittir."
        elif qtype == "relational":
            query = Q_RELATIONAL.format(case_no=case_no)
            answer = (
                f"Karar {case_no}, {law_full} ({law_short}) m.{article} "
                f"hükmüne dayanmaktadır."
            )
        else:
            query = Q_SEMANTIC.format(subject=subject, court=court)
            answer = (
                f"{court}, {subject} konusunda {law_short} m.{article} "
                f"({desc}) maddesine dayanmaktadır."
            )

        entries.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"legal-{i}-{case_no}")),
                "domain": "legal",
                "query": query,
                "context_fragments": [ctx],
                "ground_truth_answer": answer,
                "required_reasoning_hops": [1, 2, 3][i % 3],
                "metadata": {
                    "complexity_tier": [1, 2, 3][i % 3],
                    "requires_chronology": i % 5 == 0,
                    "is_contradictory": i % 7 == 0,
                    "is_synthetic": True,
                },
                "expected_triplets": [
                    {
                        "source": f"Yargıtay Kararı {case_no}",
                        "relation": "DAYANIR",
                        "target": f"{law_short} m.{article}",
                    }
                ],
            }
        )

    assert len(entries) == count, f"Expected {count}, got {len(entries)}"
    return entries


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for the Legal Golden Dataset generator."""
    parser = argparse.ArgumentParser(
        description="MESA v0.4.0 Phase 2 — Legal Golden Dataset Generator",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="legal_golden.json",
        help="Output file path (default: legal_golden.json)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=200,
        help="Number of entries to generate (default: 200)",
    )
    args = parser.parse_args()

    data = generate_legal_dataset(args.count)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(
        f"✓ Generated {len(data)} legal entries → {args.out}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
