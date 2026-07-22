#!/usr/bin/env python3
"""Sync selected pinned MemoryAgentBench capability tracks into schema v2."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from ..core.paths import cache_root, data_root

REVISION = "7ea066982b140a19337e17e60d45d4076e042faf"
SELECTED_RAW_SHA256 = "55f72b0f8aa86e8854776b86752c7497494afaea4d51c4b18d1d4448c64d8fb6"
RECSYS_RAW_SHA256 = "04ed684e905e345111200d76afdb9009525357ad6c9a7cf4f2b1612a03cf1ed0"
DATASET_ID = "ai-hyz/MemoryAgentBench"
DEFAULT_OUT = data_root() / "external" / "memoryagentbench" / "dataset.json"


def selected_source(source: str, track: str = "core") -> bool:
    if track == "recsys":
        return source == "recsys_redial_full"
    return (
        source == "eventqa_131072"
        or source
        in {
            "factconsolidation_mh_64k",
            "factconsolidation_sh_64k",
            "detective_qa",
        }
        or source.startswith("icl_")
    )


def canonical_selected_rows(dataset: Any, track: str = "core") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in sorted(dataset):
        for index, row in enumerate(dataset[split]):
            value = dict(row)
            source = str(value["metadata"]["source"])
            if selected_source(source, track):
                rows.append({"split": split, "index": index, **value})
    payload = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    actual = hashlib.sha256(payload).hexdigest()
    expected = RECSYS_RAW_SHA256 if track == "recsys" else SELECTED_RAW_SHA256
    if actual != expected:
        raise RuntimeError(
            "MemoryAgentBench selected-row checksum mismatch: "
            f"expected={expected} actual={actual}"
        )
    return rows


def _sentences(text: str) -> Iterable[str]:
    for item in re.split(r"(?<=[.!?])\s+|\n{2,}", text):
        if item.strip():
            yield item.strip()


def chunk_text(text: str, chunk_size: int) -> list[str]:
    """Mirror upstream sentence-bounded token chunking without runtime downloads."""
    import tiktoken

    encoding = tiktoken.encoding_for_model("gpt-4o-mini")
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in _sentences(text):
        tokens = encoding.encode(sentence, allowed_special={"<|endoftext|>"})
        if len(tokens) > chunk_size:
            if current:
                chunks.append(" ".join(current))
                current = []
                current_tokens = 0
            chunks.extend(
                encoding.decode(tokens[index : index + chunk_size])
                for index in range(0, len(tokens), chunk_size)
            )
        elif current_tokens + len(tokens) > chunk_size:
            chunks.append(" ".join(current))
            current = [sentence]
            current_tokens = len(tokens)
        else:
            current.append(sentence)
            current_tokens += len(tokens)
    if current:
        chunks.append(" ".join(current))
    return chunks


def _flatten_references(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    flattened: list[str] = []
    for item in value or []:
        if isinstance(item, list):
            flattened.extend(str(part) for part in item)
        else:
            flattened.append(str(item))
    return list(dict.fromkeys(item for item in flattened if item.strip()))


def _category(source: str) -> str:
    if source.startswith("eventqa_"):
        return "accurate_retrieval"
    if source.startswith("factconsolidation_"):
        return "conflict_resolution"
    if source == "detective_qa":
        return "long_range_understanding"
    if source == "recsys_redial_full":
        return "recommendation"
    return "test_time_learning"


def convert(rows: list[dict[str, Any]], chunk_size: int) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    question_ids: set[str] = set()
    for row in rows:
        metadata = dict(row["metadata"])
        source = str(metadata["source"])
        source_counts[source] = source_counts.get(source, 0) + 1
        scenario_id = f"mab-{source}-{source_counts[source]:03d}"
        chunks = chunk_text(str(row["context"]), chunk_size)
        contexts = [
            {
                "id": f"{scenario_id}-chunk-{index:05d}",
                "text": chunk,
                "metadata": {
                    "source": DATASET_ID,
                    "track": source,
                    "chunk_index": index,
                },
            }
            for index, chunk in enumerate(chunks)
        ]
        qa_ids = metadata.get("qa_pair_ids") or []
        questions = []
        for index, (query, answers) in enumerate(zip(row["questions"], row["answers"])):
            official_question_id = str(
                qa_ids[index] if index < len(qa_ids) else f"q{index}"
            )
            question_id = f"{scenario_id}-{official_question_id}"
            if question_id in question_ids:
                raise ValueError(
                    f"duplicate MemoryAgentBench question ID: {question_id}"
                )
            question_ids.add(question_id)
            questions.append(
                {
                    "id": question_id,
                    "query": str(query),
                    "reference_answers": _flatten_references(answers),
                    "category": _category(source),
                    "difficulty": source,
                    "metadata": {
                        "official_source": source,
                        "official_question_id": official_question_id,
                        "official_metric": (
                            "recall_at_5"
                            if source == "recsys_redial_full"
                            else (
                                "substring_exact_match"
                                if source.startswith(("eventqa_", "factconsolidation_"))
                                else "exact_match"
                            )
                        ),
                    },
                    "evaluation_strategy": (
                        "recall_at_5"
                        if source == "recsys_redial_full"
                        else (
                            "substring_match"
                            if source.startswith(("eventqa_", "factconsolidation_"))
                            else "normalized_exact_match"
                        )
                    ),
                }
            )
        scenarios.append(
            {
                "id": scenario_id,
                "name": f"MemoryAgentBench {source}",
                "description": f"Official {source} track with {chunk_size}-token chunks",
                "contexts": contexts,
                "questions": questions,
            }
        )
    return scenarios


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--cache-dir", default=str(cache_root() / "memoryagentbench"))
    parser.add_argument("--chunk-size", type=int, default=512)
    parser.add_argument("--track", choices=("core", "recsys"), default="core")
    args = parser.parse_args()
    from datasets import load_dataset  # type: ignore[attr-defined]

    dataset = load_dataset(DATASET_ID, revision=REVISION, cache_dir=args.cache_dir)
    rows = canonical_selected_rows(dataset, args.track)
    scenarios = convert(rows, args.chunk_size)
    output = (
        Path(args.output)
        if args.output
        else (
            DEFAULT_OUT
            if args.track == "core"
            else data_root() / "external" / "memoryagentbench" / "recsys.json"
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(scenarios, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "revision": REVISION,
                "raw_sha256": (
                    RECSYS_RAW_SHA256 if args.track == "recsys" else SELECTED_RAW_SHA256
                ),
                "converted_sha256": sha256(output),
                "scenarios": len(scenarios),
                "contexts": sum(len(item["contexts"]) for item in scenarios),
                "questions": sum(len(item["questions"]) for item in scenarios),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
