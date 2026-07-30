#!/usr/bin/env python3
"""Generate deterministic v3 regression, fairness, and frozen holdout datasets."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, cast

from ..core.paths import data_root, resolve_benchmark_path


def contradiction_v3(count: int = 200, seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    scenarios = []
    for index in range(count):
        scenario_id = f"contradiction-v3-{index:04d}"
        old_value = 50 + index
        current_value = old_value + rng.randint(7, 80)
        company = f"Northstar-{index:04d}"
        outdated = f"{scenario_id}-outdated"
        authoritative = f"{scenario_id}-authoritative"
        contexts = [
            {
                "id": outdated,
                "text": f"A preliminary forecast estimated {company} revenue at ${old_value}M.",
                "metadata": {
                    "evidence_role": "outdated",
                    "effective_date": "2024-03-01",
                },
            },
            {
                "id": authoritative,
                "text": f"The audited annual filing reports {company} revenue at ${current_value}M.",
                "metadata": {
                    "evidence_role": "authoritative",
                    "effective_date": "2025-02-15",
                },
            },
        ]
        for noise_index in range(5):
            contexts.append(
                {
                    "id": f"{scenario_id}-noise-{noise_index}",
                    "text": f"Unrelated company Meridian-{index}-{noise_index} reported ${rng.randint(10, 900)}M.",
                    "metadata": {"evidence_role": "distractor"},
                }
            )
        rng.shuffle(contexts)
        scenarios.append(
            {
                "id": scenario_id,
                "name": f"Authoritative update {index}",
                "description": "Stable contradiction v3 with explicit evidence roles",
                "contexts": contexts,
                "questions": [
                    {
                        "id": f"{scenario_id}-q",
                        "query": f"What audited revenue did {company} ultimately report?",
                        "reference_answers": [
                            f"${current_value}M",
                            f"{current_value} million dollars",
                        ],
                        "category": "update_contradiction",
                        "difficulty": "hard",
                        "supporting_context_ids": [authoritative],
                        "required_context_groups": [[authoritative]],
                        "forbidden_context_ids": [outdated],
                        "evaluation_strategy": "exact_match",
                    }
                ],
            }
        )
    return scenarios


def raw_text_multihop(source: Path) -> list[dict[str, Any]]:
    rows = cast(list[dict[str, Any]], json.loads(source.read_text(encoding="utf-8")))
    for scenario in rows:
        for context in scenario["contexts"]:
            metadata = context.get("metadata", {})
            context["metadata"] = {
                "source": "comprehensive-multihop-raw",
                **{
                    key: value
                    for key, value in metadata.items()
                    if key not in {"relations", "entity_name", "node_id", "edges"}
                },
            }
        for question in scenario["questions"]:
            question["category"] = "multi_hop"
            question.setdefault("difficulty", "internal")
    return rows


def frozen_holdout(seed: int = 20260722) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    categories = [
        "fact_recall",
        "multi_hop",
        "update_contradiction",
        "abstention",
        "preference_instruction",
        "near_negative_noise",
    ]
    scenarios: list[dict[str, Any]] = []
    for category_index, category in enumerate(categories):
        for index in range(100):
            turkish = index < 20
            code = f"H{category_index}{index:03d}"
            value = 1000 + category_index * 100 + index
            target = f"Atlas-{code}"
            c1, c2, c3, c4 = (f"{code}-c{i}" for i in range(1, 5))
            contexts: list[dict[str, Any]]
            supporting: list[str]
            groups: list[list[str]]
            forbidden: list[str] = []
            if category == "multi_hop":
                contexts = [
                    {
                        "id": c1,
                        "text": f"{target} uses project codename Cedar-{code}.",
                        "metadata": {"evidence_role": "required"},
                    },
                    {
                        "id": c2,
                        "text": f"Project Cedar-{code} has clearance value {value}.",
                        "metadata": {"evidence_role": "required"},
                    },
                    {
                        "id": c3,
                        "text": f"Project Cedar-{code}X has clearance value {value + 1}.",
                        "metadata": {"evidence_role": "near_negative"},
                    },
                    {
                        "id": c4,
                        "text": f"Archive note {code} is unrelated.",
                        "metadata": {"evidence_role": "distractor"},
                    },
                ]
                query = (
                    f"{target} kuruluşunun proje izin değeri nedir?"
                    if turkish
                    else f"What is the project clearance value used by {target}?"
                )
                answers = [str(value)]
                supporting, groups = [c1, c2], [[c1], [c2]]
            elif category == "update_contradiction":
                contexts = [
                    {
                        "id": c1,
                        "text": f"An obsolete profile listed {target} value as {value - 1}.",
                        "metadata": {"evidence_role": "outdated"},
                    },
                    {
                        "id": c2,
                        "text": f"The authoritative profile now lists {target} value as {value}.",
                        "metadata": {"evidence_role": "authoritative"},
                    },
                    {
                        "id": c3,
                        "text": f"{target}X still has value {value - 1}.",
                        "metadata": {"evidence_role": "near_negative"},
                    },
                    {
                        "id": c4,
                        "text": f"Noise record {code}.",
                        "metadata": {"evidence_role": "distractor"},
                    },
                ]
                query = (
                    f"{target} için güncel yetkili değer nedir?"
                    if turkish
                    else f"What is the current authoritative value for {target}?"
                )
                answers = [str(value)]
                supporting, groups, forbidden = [c2], [[c2]], [c1]
            elif category == "abstention":
                contexts = [
                    {
                        "id": c1,
                        "text": f"{target} has color blue.",
                        "metadata": {"evidence_role": "near_negative"},
                    },
                    {
                        "id": c2,
                        "text": f"{target} has serial {value}.",
                        "metadata": {"evidence_role": "distractor"},
                    },
                    {
                        "id": c3,
                        "text": f"{target}X has owner Mira.",
                        "metadata": {"evidence_role": "near_negative"},
                    },
                    {
                        "id": c4,
                        "text": f"No owner record is supplied for {target}.",
                        "metadata": {"evidence_role": "abstention_cue"},
                    },
                ]
                query = (
                    f"{target} sahibinin adı nedir?"
                    if turkish
                    else f"What is the owner's name for {target}?"
                )
                answers = ["Bilinmiyor" if turkish else "Unknown"]
                supporting, groups = [], []
            elif category == "preference_instruction":
                contexts = [
                    {
                        "id": c1,
                        "text": f"The user asks that {target} summaries always use exactly two bullets.",
                        "metadata": {"evidence_role": "required"},
                    },
                    {
                        "id": c2,
                        "text": f"An older request for {target}X used three bullets.",
                        "metadata": {"evidence_role": "near_negative"},
                    },
                    {
                        "id": c3,
                        "text": f"The user prefers concise wording for {target}.",
                        "metadata": {"evidence_role": "required"},
                    },
                    {
                        "id": c4,
                        "text": f"Unrelated formatting note {code}.",
                        "metadata": {"evidence_role": "distractor"},
                    },
                ]
                query = (
                    f"{target} özetinde kaç madde kullanılmalı?"
                    if turkish
                    else f"How many bullets should a {target} summary use?"
                )
                answers = ["iki" if turkish else "two", "2"]
                supporting, groups = [c1], [[c1]]
            else:
                contexts = [
                    {
                        "id": c1,
                        "text": f"The verified value for {target} is {value}.",
                        "metadata": {"evidence_role": "required"},
                    },
                    {
                        "id": c2,
                        "text": f"The value for {target}X is {value + 1}.",
                        "metadata": {"evidence_role": "near_negative"},
                    },
                    {
                        "id": c3,
                        "text": f"A paraphrased record confirms {target} equals {value}.",
                        "metadata": {"evidence_role": "paraphrase"},
                    },
                    {
                        "id": c4,
                        "text": f"Noise token {code}-{rng.randint(10000, 99999)}.",
                        "metadata": {"evidence_role": "distractor"},
                    },
                ]
                query = (
                    f"{target} için doğrulanmış değer nedir?"
                    if turkish
                    else f"What verified value belongs to {target}?"
                )
                answers = [str(value)]
                supporting, groups = [c1, c3], [[c1, c3]]
            scenarios.append(
                {
                    "id": f"holdout-{code}",
                    "name": f"Frozen holdout {code}",
                    "description": "Deterministic bilingual internal holdout",
                    "contexts": contexts,
                    "questions": [
                        {
                            "id": f"holdout-{code}-q",
                            "query": query,
                            "reference_answers": answers,
                            "category": category,
                            "difficulty": "hard",
                            "metadata": {"language": "tr" if turkish else "en"},
                            "supporting_context_ids": supporting,
                            "required_context_groups": groups,
                            "forbidden_context_ids": forbidden,
                            "evaluation_strategy": "exact_match",
                        }
                    ],
                }
            )
    return scenarios


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    output_root = data_root() / "generated" / "internal"
    write_json(output_root / "contradiction_v3.json", contradiction_v3(seed=args.seed))
    write_json(
        output_root / "comprehensive_multihop_raw_v2.json",
        raw_text_multihop(
            resolve_benchmark_path(
                "resource://fixtures/internal/comprehensive_multihop_only.json"
            )
        ),
    )
    write_json(output_root / "internal_holdout_600.json", frozen_holdout())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
