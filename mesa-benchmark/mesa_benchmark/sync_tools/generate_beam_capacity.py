#!/usr/bin/env python3
"""Build an opt-in, non-publishable 10M-token capacity track from pinned BEAM 1M."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..core.paths import data_root

SOURCE = data_root() / "generated" / "beam" / "scale" / "1m.json"
OUTPUT = data_root() / "generated" / "beam" / "scale" / "10m-capacity.json"
MANIFEST = data_root() / "generated" / "beam" / "scale" / "10m-capacity-manifest.json"
SOURCE_SHA256 = "3f5e5cdb51df0762bceb119cb77173c761040439e4642577b23623967c129355"
REVISION = "3205395e897e7318c7b094ef4e6047b9b82dbb03"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_capacity(
    source: list[dict[str, Any]], target_tokens: int
) -> tuple[list[dict[str, Any]], int]:
    import tiktoken

    encoding = tiktoken.get_encoding("cl100k_base")
    contexts: list[dict[str, Any]] = []
    total_tokens = 0
    final_questions: list[dict[str, Any]] = []
    for scenario_index, scenario in enumerate(source):
        prefix = f"capacity-s{scenario_index:02d}"
        for context_index, raw_context in enumerate(scenario["contexts"]):
            context = dict(raw_context)
            context["id"] = f"{prefix}-c{context_index:06d}"
            context["metadata"] = {
                **dict(context.get("metadata") or {}),
                "capacity_source_context_id": raw_context["id"],
            }
            contexts.append(context)
            total_tokens += len(encoding.encode(str(context["text"])))
        final_questions = []
        for question_index, raw_question in enumerate(scenario["questions"]):
            question = dict(raw_question)
            question["id"] = f"{prefix}-q{question_index:03d}"
            question["supporting_context_ids"] = []
            question["required_context_groups"] = []
            question["forbidden_context_ids"] = []
            final_questions.append(question)
        if total_tokens >= target_tokens:
            break
    if total_tokens < target_tokens:
        raise ValueError(
            f"pinned BEAM 1M conversion contains only {total_tokens} tokens; "
            f"cannot build requested {target_tokens}-token track"
        )
    return [
        {
            "id": "beam-10m-capacity",
            "name": "BEAM-derived 10M-token capacity",
            "description": "Opt-in capacity-only track; never a publishable quality score",
            "contexts": contexts,
            "questions": final_questions,
        }
    ], total_tokens


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--target-tokens", type=int, default=10_000_000)
    args = parser.parse_args()
    if args.target_tokens < 1:
        raise ValueError("target tokens must be positive")
    if sha256(args.source) != SOURCE_SHA256:
        raise RuntimeError("BEAM 1M converted checksum mismatch")
    source = json.loads(args.source.read_text(encoding="utf-8"))
    converted, actual_tokens = build_capacity(source, args.target_tokens)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(converted, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    categories = Counter(
        question["category"]
        for scenario in converted
        for question in scenario["questions"]
    )
    manifest = {
        "schema_version": "2.0",
        "dataset_name": "BEAM-derived capacity",
        "dataset_version": "10m-v1",
        "source": {
            "url": "https://huggingface.co/datasets/Mohammadta/BEAM",
            "revision": REVISION,
            "split": "1M-derived-capacity",
        },
        "checksums": {
            "raw_sha256": SOURCE_SHA256,
            "converted_sha256": sha256(args.output),
        },
        "license": {"spdx_id": "CC-BY-SA-4.0", "redistribution": "allowed"},
        "designation": "internal-regression",
        "isolation": "scenario",
        "ingest_mode": "sequential",
        "chunking": {
            "strategy": "pair_chunk",
            "parameters": {"capacity_only": True, "actual_tokens": actual_tokens},
        },
        "metrics": {
            "supported": ["latency", "tokens", "storage_size_bytes"],
            "unsupported": ["publishable_quality_score", "retrieval_relevance"],
        },
        "counts": {
            "scenarios": 1,
            "contexts": len(converted[0]["contexts"]),
            "questions": len(converted[0]["questions"]),
            "categories": dict(sorted(categories.items())),
        },
        "converter": {
            "version": "beam-capacity-v1",
            "parameters": {"target_tokens": args.target_tokens},
        },
        "quality": {"normalized_duplicate_query_budget": 0.05},
        "known_annotation_exceptions": [
            "Capacity-only derived track; retrieval relevance is intentionally unavailable."
        ],
    }
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "manifest": str(args.manifest),
                "actual_tokens": actual_tokens,
                "designation": "internal-regression",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
