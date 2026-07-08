"""MESA v0.4.1 — Phase 3A: Legal Golden Dataset Generator with Adversarial Traps.

Generates multi-hop legal evaluation entries based on Turkish Supreme Court
(Yargıtay) decisions and Turkish Law (Kanunlar), with adversarial traps for
measuring Context Precision and Faithfulness.

Distribution:
  70% Standard queries   — entity, relational, semantic (valid expected_triplets)
  15% Hard Negatives     — valid law article + wrong legal context (expected_triplets=[])
  15% Out of Domain      — non-legal queries: recipes, sports, etc. (expected_triplets=[])

Data Relationship: (Yargıtay_Kararı) -[DAYANIR]-> (Kanun_Maddesi)

Question Types (standard):
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
# Hard Negative templates — valid article reference + WRONG legal context
# Each tuple: (law_short, article, misleading_subject)
# The misleading_subject is deliberately unrelated to the actual article.
# ---------------------------------------------------------------------------

HARD_NEGATIVE_TRAPS = [
    ("TBK", "49", "kira bedelinin hesaplanması"),  # m.49 = torts, NOT rent
    ("TBK", "49", "iş sözleşmesinin feshi"),  # m.49 = torts, NOT employment
    ("TMK", "2", "velayet hakkının değiştirilmesi"),  # m.2 = good faith, NOT custody
    ("TMK", "2", "tapu sicilinin düzeltilmesi"),  # m.2 = good faith, NOT land registry
    ("TCK", "157", "trafik güvenliğini tehlikeye sokma"),  # m.157 = fraud, NOT traffic
    ("TCK", "157", "hakaret suçunun unsurları"),  # m.157 = fraud, NOT defamation
    (
        "HMK",
        "107",
        "delil tespiti prosedürü",
    ),  # m.107 = indeterminate claims, NOT evidence
    (
        "HMK",
        "107",
        "ihtiyati tedbir şartları",
    ),  # m.107 = indeterminate claims, NOT injunctions
    (
        "TTK",
        "18",
        "anonim şirketin kuruluş işlemleri",
    ),  # m.18 = merchant duties, NOT incorporation
    (
        "TTK",
        "18",
        "kambiyo senetlerinde zamanaşımı",
    ),  # m.18 = merchant duties, NOT negotiable instruments
]

HARD_NEGATIVE_QUERY_TEMPLATES = [
    "{law_short} m.{article} kapsamında {wrong_subject} hakkında Yargıtay'ın görüşü nedir?",
    "{law_short} m.{article} hükmü {wrong_subject} davalarında nasıl uygulanmaktadır?",
    "{wrong_subject} konusunda {law_short} m.{article} maddesinin yeri nedir?",
]

# ---------------------------------------------------------------------------
# Out-of-Domain templates — non-legal queries (recipes, sports, trivia)
# ---------------------------------------------------------------------------

OUT_OF_DOMAIN_QUERIES = [
    "Ev yapımı mercimek çorbasının tarifi nedir?",
    "2026 Dünya Kupası hangi ülkede düzenleniyor?",
    "Bir maraton kaç kilometredir?",
    "Python'da dictionary comprehension nasıl yazılır?",
    "Güneş sistemimizdeki en büyük gezegen hangisidir?",
    "Lahmacun hamuru nasıl açılır?",
    "Beşiktaş'ın 2024-2025 sezon kadrosunda kaç yabancı oyuncu var?",
    "Türkiye'nin en yüksek dağı hangisidir ve rakımı kaçtır?",
    "Yapay zekâ ile görüntü sınıflandırma için hangi CNN mimarileri kullanılır?",
    "İstanbul'dan Ankara'ya hızlı trenle kaç saat sürer?",
    "Baklava kaç derecede ve kaç dakika pişirilmelidir?",
    "Futbolda ofsayt kuralı nasıl işler?",
    "Bir kilovat-saat kaç joule'dür?",
    "En iyi espresso makinesi markaları hangileridir?",
    "Mars'a ilk insanlı uçuş ne zaman planlanıyor?",
]

OUT_OF_DOMAIN_CONTEXTS = [
    "Mercimek çorbası, Türk mutfağının en temel yemeklerinden biridir.",
    "FIFA Dünya Kupası, dört yılda bir düzenlenen uluslararası futbol turnuvasıdır.",
    "Bir maraton yarışı resmi olarak 42.195 metre mesafeyi kapsar.",
    "Python, yüksek seviyeli, yorumlanabilir bir programlama dilidir.",
    "Jüpiter, Güneş Sistemi'ndeki en büyük gezegendir.",
    "Lahmacun, ince hamurun üzerine kıymalı harç sürülerek fırında pişirilir.",
    "Beşiktaş JK, İstanbul merkezli bir profesyonel futbol kulübüdür.",
    "Ağrı Dağı, 5.137 metre yüksekliği ile Türkiye'nin en yüksek dağıdır.",
    "CNN (Convolutional Neural Network), görüntü işleme için yaygın kullanılan bir derin öğrenme mimarisidir.",
    "YHT, Türkiye'deki yüksek hızlı tren hizmetidir.",
    "Baklava, ince yufka katları arasına ceviz veya fıstık konularak yapılır.",
    "Futbolda ofsayt kuralı, hücum oyuncusunun pozisyonunu düzenler.",
    "Bir kilovat-saat, 3.6 milyon joule'e eşittir.",
    "Espresso, yüksek basınç altında ince çekilmiş kahveden elde edilen bir içecektir.",
    "NASA ve SpaceX, Mars'a insanlı görevler için çalışmalar yürütmektedir.",
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
    """Generate `count` legal evaluation entries with adversarial traps.

    Distribution:
      - 70% standard queries (entity / relational / semantic)
      - 15% hard_negative (valid article + wrong context, expected_triplets=[])
      - 15% out_of_domain (non-legal queries, expected_triplets=[])

    Each entry models the relationship:
        (Yargıtay_Kararı) -[DAYANIR]-> (Kanun_Maddesi)
    except adversarial entries which carry empty expected_triplets.

    Returns a list of dicts compatible with mesa_evals.dataset.DatasetEntry.
    """
    random.seed(SEED)
    entries: list[dict] = []
    qtypes = ["entity", "relational", "semantic"]

    # --- Compute split sizes ---
    n_standard = int(count * 0.70)
    n_hard_neg = int(count * 0.15)
    n_ood = count - n_standard - n_hard_neg  # remainder goes to OOD

    # ===================================================================
    # Phase 1: Standard queries (70%)
    # ===================================================================
    for i in range(n_standard):
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
                "category": "standard",
                "query": query,
                "context_fragments": [ctx],
                "ground_truth_answer": answer,
                "required_reasoning_hops": [1, 2, 3][i % 3],
                "metadata": {
                    "complexity_tier": [1, 2, 3][i % 3],
                    "requires_chronology": i % 5 == 0,
                    "is_contradictory": i % 7 == 0,
                    "is_synthetic": True,
                    "category": "standard",
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

    # ===================================================================
    # Phase 2: Hard Negative traps (15%)
    # Valid law article + deliberately WRONG legal context.
    # Expected: system must NOT return related triplets.
    # ===================================================================
    for j in range(n_hard_neg):
        trap = HARD_NEGATIVE_TRAPS[j % len(HARD_NEGATIVE_TRAPS)]
        law_short, article, wrong_subject = trap
        template = HARD_NEGATIVE_QUERY_TEMPLATES[j % len(HARD_NEGATIVE_QUERY_TEMPLATES)]
        query = template.format(
            law_short=law_short,
            article=article,
            wrong_subject=wrong_subject,
        )

        # Context is intentionally empty — no supporting evidence should exist
        ctx = (
            f"{wrong_subject} konusu, {law_short} m.{article} kapsamında "
            f"değerlendirilmesi talep edilmiştir. Ancak bu madde doğrudan "
            f"bu konuyu düzenlememektedir."
        )

        entries.append(
            {
                "id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_DNS, f"hard-neg-{j}-{law_short}-{article}"
                    )
                ),
                "domain": "legal",
                "category": "hard_negative",
                "query": query,
                "context_fragments": [ctx],
                "ground_truth_answer": (
                    f"{law_short} m.{article} maddesi {wrong_subject} "
                    f"konusunu doğrudan düzenlememektedir."
                ),
                "required_reasoning_hops": 0,
                "metadata": {
                    "complexity_tier": 0,
                    "requires_chronology": False,
                    "is_contradictory": True,
                    "is_synthetic": True,
                    "category": "hard_negative",
                    "trap_type": "wrong_context_for_valid_article",
                    "actual_article_topic": {
                        law_item[0]: law_item[3] for law_item in LAWS
                    }.get(law_short, "unknown"),
                    "misleading_subject": wrong_subject,
                },
                "expected_triplets": [],  # Ground truth: system MUST return empty
            }
        )

    # ===================================================================
    # Phase 3: Out-of-Domain traps (15%)
    # Non-legal queries: recipes, sports, trivia.
    # Expected: system must NOT return any legal triplets.
    # ===================================================================
    for k in range(n_ood):
        query = OUT_OF_DOMAIN_QUERIES[k % len(OUT_OF_DOMAIN_QUERIES)]
        ctx = OUT_OF_DOMAIN_CONTEXTS[k % len(OUT_OF_DOMAIN_CONTEXTS)]

        entries.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ood-{k}-{query[:30]}")),
                "domain": "out_of_domain",
                "category": "out_of_domain",
                "query": query,
                "context_fragments": [ctx],
                "ground_truth_answer": "Bu soru hukuki bir konu değildir.",
                "required_reasoning_hops": 0,
                "metadata": {
                    "complexity_tier": 0,
                    "requires_chronology": False,
                    "is_contradictory": False,
                    "is_synthetic": True,
                    "category": "out_of_domain",
                    "trap_type": "non_legal_domain",
                },
                "expected_triplets": [],  # Ground truth: system MUST return empty
            }
        )

    # Shuffle to avoid clustering by category during ingestion
    random.shuffle(entries)

    assert len(entries) == count, f"Expected {count}, got {len(entries)}"
    return entries


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for the Legal Golden Dataset generator."""
    parser = argparse.ArgumentParser(
        description="MESA v0.4.1 Phase 3A — Legal Golden Dataset Generator with Adversarial Traps",
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
