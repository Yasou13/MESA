#!/usr/bin/env python3
"""Download and losslessly convert the pinned LongMemEval_S cleaned release."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

from ..core.paths import cache_root, data_root

REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
RAW_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
CONVERTED_SHA256 = "7eae91c8ddda5db33494e8a0e5781ddef6fc6dba5135b79687dbdd85c35930a2"
SOURCE_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/"
    f"{REVISION}/longmemeval_s_cleaned.json"
)
DEFAULT_OUT = data_root() / "external" / "longmemeval" / "dataset.json"

CATEGORY_MAP = {
    "single-session-user": "information_extraction",
    "single-session-assistant": "information_extraction",
    "single-session-preference": "information_extraction",
    "multi-session": "multi_session_reasoning",
    "knowledge-update": "knowledge_update",
    "temporal-reasoning": "temporal_reasoning",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def acquire_raw(cache_dir: Path, raw_file: Path | None = None) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = raw_file or cache_dir / f"longmemeval_s_cleaned-{REVISION}.json"
    if not target.exists():
        urllib.request.urlretrieve(SOURCE_URL, target)
    actual = sha256(target)
    if actual != RAW_SHA256:
        raise RuntimeError(
            f"LongMemEval checksum mismatch: expected={RAW_SHA256} actual={actual}"
        )
    return target


def convert_longmemeval(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(raw) != 500:
        raise ValueError(
            f"LongMemEval_S cleaned must contain 500 questions, got {len(raw)}"
        )
    scenarios: list[dict[str, Any]] = []
    for row in raw:
        question_id = str(row["question_id"])
        session_ids = [str(item) for item in row["haystack_session_ids"]]
        sessions = row["haystack_sessions"]
        dates = row["haystack_dates"]
        if not (len(session_ids) == len(sessions) == len(dates)):
            raise ValueError(f"misaligned LongMemEval history: {question_id}")
        contexts = []
        seen_session_ids: dict[str, int] = {}
        for session_index, (session_id, session, date) in enumerate(
            zip(session_ids, sessions, dates)
        ):
            seen_session_ids[session_id] = seen_session_ids.get(session_id, 0) + 1
            context_id = (
                session_id
                if seen_session_ids[session_id] == 1
                else f"{session_id}~{seen_session_ids[session_id]}"
            )
            text = "\n".join(
                f"[{str(turn['role']).upper()}]: {turn['content']}" for turn in session
            )
            contexts.append(
                {
                    "id": context_id,
                    "text": text,
                    "metadata": {
                        "source": "xiaowu0162/longmemeval-cleaned",
                        "session_date": date,
                        "session_index": session_index,
                        "official_session_id": session_id,
                    },
                }
            )
        evidence = [str(item) for item in row.get("answer_session_ids", [])]
        missing = sorted(set(evidence).difference(session_ids))
        if missing:
            raise ValueError(f"LongMemEval {question_id} missing evidence: {missing}")
        question_type = str(row["question_type"])
        scenarios.append(
            {
                "id": f"longmemeval-{question_id}",
                "name": f"LongMemEval_S {question_id}",
                "description": "Pinned cleaned LongMemEval_S question history",
                "contexts": contexts,
                "questions": [
                    {
                        "id": question_id,
                        "query": str(row["question"]),
                        "reference_answers": [str(row["answer"])],
                        "supporting_context_ids": evidence,
                        "required_context_groups": [[item] for item in evidence],
                        "category": (
                            "abstention"
                            if question_id.endswith("_abs")
                            else CATEGORY_MAP[question_type]
                        ),
                        "difficulty": "small-128k",
                        "metadata": {
                            "official_question_type": question_type,
                            "question_date": row["question_date"],
                        },
                        "evaluation_strategy": "llm_judge",
                    }
                ],
            }
        )
    return scenarios


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUT))
    parser.add_argument("--cache-dir", default=str(cache_root() / "longmemeval"))
    parser.add_argument("--raw-file", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists() and not args.force and sha256(output) == CONVERTED_SHA256:
        print(
            json.dumps(
                {
                    "output": str(output),
                    "revision": REVISION,
                    "raw_sha256": RAW_SHA256,
                    "converted_sha256": CONVERTED_SHA256,
                    "status": "verified-existing",
                },
                indent=2,
            )
        )
        return 0
    raw_path = acquire_raw(Path(args.cache_dir), args.raw_file)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    converted = convert_longmemeval(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(converted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "revision": REVISION,
                "raw_sha256": RAW_SHA256,
                "converted_sha256": sha256(output),
                "scenarios": len(converted),
                "questions": 500,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
