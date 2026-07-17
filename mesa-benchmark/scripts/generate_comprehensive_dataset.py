#!/usr/bin/env python3
"""
Comprehensive 4-Tier Benchmark Dataset Generator for MESA v0.6.0+.
Generates 200+ scenarios across four difficulty tiers:
  1. Single-Hop Retrieval (40%)
  2. Multi-Hop Graph Traversal (30%)
  3. Hard-Negative / Contradiction Resolution (15%)
  4. Out-of-Domain / Distractor Quarantine (15%)
"""

import argparse
import json
import os
import random
from typing import Any, Dict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT_PATH = os.path.normpath(
    os.path.join(
        SCRIPT_DIR,
        "..",
        "mesa_benchmark",
        "datasets",
        "comprehensive_200_dataset.json",
    )
)

ENTITIES_A = [
    "Dr. Elena Vance",
    "Professor Marcus Thorne",
    "Aria Montgomery",
    "Kenji Sato",
    "Clara Oswald",
    "Dr. Aris Thorne",
    "Selin Yilmaz",
    "Kaan Aksoy",
    "Amara Okafor",
    "Liam O'Connor",
]

COMPANIES = [
    "Aegis Quantum Systems",
    "Ephesus BioLabs",
    "MESA Memory Corp",
    "NovaStellar Tech",
    "Hyperion Cybernetics",
    "Veritas Legal AI",
    "Apex Robotics",
    "Zenith Cloud",
]

CITIES = [
    "Zurich",
    "Istanbul",
    "Tokyo",
    "San Francisco",
    "London",
    "Berlin",
    "Singapore",
]


def generate_single_hop(idx: int) -> Dict[str, Any]:
    person = ENTITIES_A[idx % len(ENTITIES_A)]
    company = COMPANIES[idx % len(COMPANIES)]
    role = "Chief AI Architect" if idx % 2 == 0 else "Director of Research"

    ctx_id = f"single_{idx}_ctx"
    context_text = f"{person} joined {company} as {role} in January 2025."

    q_id = f"single_{idx}_q"
    query = f"What role did {person} assume at {company}?"

    return {
        "id": f"single_hop_scenario_{idx}",
        "name": f"Single-Hop Entity Role #{idx}",
        "description": "Tests direct fact retrieval from single memory node.",
        "contexts": [
            {"id": ctx_id, "text": context_text, "metadata": {"tier": "single_hop"}}
        ],
        "questions": [
            {
                "id": q_id,
                "query": query,
                "ground_truth": role,
                "expected_context_ids": [ctx_id],
                "evaluation_strategy": "exact_match",
            }
        ],
    }


def generate_multi_hop(idx: int) -> Dict[str, Any]:
    person = ENTITIES_A[idx % len(ENTITIES_A)]
    company = COMPANIES[idx % len(COMPANIES)]
    city = CITIES[idx % len(CITIES)]
    project = f"Project Omega-{idx}"

    ctx1_id = f"multi_{idx}_ctx1"
    ctx2_id = f"multi_{idx}_ctx2"

    text1 = f"{person} is the lead investigator for {project} at {company}."
    text2 = f"{project} has relocated its primary R&D headquarters to {city}."

    query = f"In which city is the primary R&D headquarters of the project led by {person} located?"

    return {
        "id": f"multi_hop_scenario_{idx}",
        "name": f"Multi-Hop Graph Traversal #{idx}",
        "description": "Tests multi-hop connection across two entity nodes.",
        "contexts": [
            {
                "id": ctx1_id,
                "text": text1,
                "metadata": {
                    "tier": "multi_hop",
                    "relations": [{"target": project, "type": "LEADS"}],
                },
            },
            {
                "id": ctx2_id,
                "text": text2,
                "metadata": {
                    "tier": "multi_hop",
                    "relations": [{"target": city, "type": "LOCATED_IN"}],
                },
            },
        ],
        "questions": [
            {
                "id": f"multi_{idx}_q",
                "query": query,
                "ground_truth": city,
                "expected_context_ids": [ctx1_id, ctx2_id],
                "evaluation_strategy": "llm_judge",
            }
        ],
    }


def generate_hard_negative(idx: int) -> Dict[str, Any]:
    company = COMPANIES[idx % len(COMPANIES)]
    old_rev = 120 + idx
    new_rev = 240 + idx

    ctx_old = f"hn_{idx}_old"
    ctx_new = f"hn_{idx}_new"

    text_old = f"In preliminary 2024 guidance, {company} projected annual net revenue of ${old_rev}M."
    text_new = f"In the audited FY2024 annual filing, {company} confirmed actual annual net revenue reached ${new_rev}M."

    query = (
        f"What was the actual audited FY2024 annual net revenue reported by {company}?"
    )

    return {
        "id": f"hard_negative_scenario_{idx}",
        "name": f"Hard-Negative Contradiction #{idx}",
        "description": "Tests resolution of conflicting old guidance vs audited facts.",
        "contexts": [
            {
                "id": ctx_old,
                "text": text_old,
                "metadata": {"tier": "hard_negative", "outdated": True},
            },
            {
                "id": ctx_new,
                "text": text_new,
                "metadata": {"tier": "hard_negative", "authoritative": True},
            },
        ],
        "questions": [
            {
                "id": f"hn_{idx}_q",
                "query": query,
                "ground_truth": f"${new_rev}M",
                "expected_context_ids": [ctx_new],
                "evaluation_strategy": "exact_match",
            }
        ],
    }


def generate_out_of_domain(idx: int) -> Dict[str, Any]:
    person = ENTITIES_A[idx % len(ENTITIES_A)]
    secret_code = f"ALPHA-{idx + 100}"
    distractor = f"Unrelated gossip column mentioned {person} visited a coffee shop."

    ctx_real = f"ood_{idx}_real"
    ctx_dist = f"ood_{idx}_dist"

    return {
        "id": f"ood_scenario_{idx}",
        "name": f"Out-of-Domain Distractor Quarantine #{idx}",
        "description": "Tests whether system avoids irrelevant distractors.",
        "contexts": [
            {
                "id": ctx_real,
                "text": f"Security briefing: {person} was assigned security clearance code {secret_code}.",
                "metadata": {"tier": "ood"},
            },
            {
                "id": ctx_dist,
                "text": distractor,
                "metadata": {"tier": "ood", "distractor": True},
            },
        ],
        "questions": [
            {
                "id": f"ood_{idx}_q",
                "query": f"What is the assigned security clearance code for {person}?",
                "ground_truth": secret_code,
                "expected_context_ids": [ctx_real],
                "evaluation_strategy": "exact_match",
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate 200+ comprehensive MESA benchmark scenarios."
    )
    parser.add_argument(
        "--output", type=str, default=DEFAULT_OUT_PATH, help="Output JSON path"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed for reproducibility"
    )
    args = parser.parse_args()

    random.seed(args.seed)

    scenarios = []

    # 40% Single-Hop (80 scenarios)
    for i in range(80):
        scenarios.append(generate_single_hop(i))

    # 30% Multi-Hop (60 scenarios)
    for i in range(60):
        scenarios.append(generate_multi_hop(i))

    # 15% Hard-Negative (30 scenarios)
    for i in range(30):
        scenarios.append(generate_hard_negative(i))

    # 15% Out-of-Domain (30 scenarios)
    for i in range(30):
        scenarios.append(generate_out_of_domain(i))

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(scenarios)} comprehensive scenarios -> {args.output}")


if __name__ == "__main__":
    main()
