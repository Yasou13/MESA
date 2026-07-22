#!/usr/bin/env python3
"""Generate the common 512-token/64-overlap BEAM chunking ablation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..core.paths import data_root

SOURCE = data_root() / "external" / "beam" / "v2" / "dataset.json"
OUTPUT = data_root() / "generated" / "beam" / "ablations" / "512-64.json"
MANIFEST = data_root() / "generated" / "beam" / "ablations" / "512-64-manifest.json"
SOURCE_SHA256 = "e0e7286fe306d850e29010aa96c10bac0b032784ce35b6ce1cf8f7629d09edd7"
RAW_SHA256 = "170672bf631b59fd512acfc7ddf25504c103edd8a86655c714287a2c6519f2c1"
REVISION = "3205395e897e7318c7b094ef4e6047b9b82dbb03"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rechunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and 0 <= overlap < chunk_size")
    import tiktoken

    encoding = tiktoken.get_encoding("cl100k_base")
    tokens = encoding.encode(text)
    step = chunk_size - overlap
    return [
        encoding.decode(tokens[index : index + chunk_size])
        for index in range(0, len(tokens), step)
        if tokens[index : index + chunk_size]
    ]


def convert(
    source: list[dict[str, Any]], chunk_size: int, overlap: int
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for scenario in source:
        full_text = "\n\n".join(context["text"] for context in scenario["contexts"])
        chunks = rechunk_text(full_text, chunk_size, overlap)
        converted.append(
            {
                **{key: value for key, value in scenario.items() if key != "contexts"},
                "contexts": [
                    {
                        "id": f"{scenario['id']}-chunk-{index:06d}",
                        "text": chunk,
                        "metadata": {
                            "source": "Mohammadta/BEAM",
                            "chunk_index": index,
                            "chunk_size": chunk_size,
                            "overlap": overlap,
                        },
                    }
                    for index, chunk in enumerate(chunks)
                ],
            }
        )
    return converted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--chunk-size", type=int, default=512)
    parser.add_argument("--overlap", type=int, default=64)
    args = parser.parse_args()
    if sha256(args.source) != SOURCE_SHA256:
        raise RuntimeError("BEAM v2 source checksum mismatch")
    source = json.loads(args.source.read_text(encoding="utf-8"))
    converted = convert(source, args.chunk_size, args.overlap)
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
    manifest: dict[str, Any] = {
        "schema_version": "2.0",
        "dataset_name": "BEAM common-chunk ablation",
        "dataset_version": "128k-512-64-v1",
        "source": {
            "url": "https://huggingface.co/datasets/Mohammadta/BEAM",
            "revision": REVISION,
            "split": "100K",
        },
        "checksums": {
            "raw_sha256": RAW_SHA256,
            "converted_sha256": sha256(args.output),
        },
        "license": {"spdx_id": "CC-BY-SA-4.0", "redistribution": "allowed"},
        "designation": "external-research",
        "isolation": "scenario",
        "ingest_mode": "sequential",
        "chunking": {
            "strategy": "token_window",
            "parameters": {"chunk_size": args.chunk_size, "overlap": args.overlap},
        },
        "metrics": {
            "supported": ["rubric_criterion_score", "latency", "tokens"],
            "unsupported": ["hit_at_k", "mrr", "ndcg"],
        },
        "counts": {
            "scenarios": len(converted),
            "contexts": sum(len(item["contexts"]) for item in converted),
            "questions": sum(len(item["questions"]) for item in converted),
            "categories": dict(sorted(categories.items())),
        },
        "converter": {
            "version": "beam-common-chunk-v1",
            "parameters": {"chunk_size": args.chunk_size, "overlap": args.overlap},
        },
        "quality": {"normalized_duplicate_query_budget": 0.05},
        "known_annotation_exceptions": [],
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
                "contexts": manifest["counts"]["contexts"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
