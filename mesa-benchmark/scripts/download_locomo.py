#!/usr/bin/env python3
"""Download a pinned official LoCoMo release and convert it to MESA format."""

import argparse
import hashlib
import json
import re
import urllib.request
import warnings
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR.parent / "datasets" / "locomo" / "dataset.json"
LOCOMO_REVISION = "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376"
LOCOMO_SHA256 = "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
LOCOMO_URL = (
    "https://raw.githubusercontent.com/snap-research/locomo/"
    f"{LOCOMO_REVISION}/data/locomo10.json"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_locomo(cache_dir: Path) -> list[dict[str, Any]]:
    """Download only the pinned official JSON and enforce its checksum."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"locomo10-{LOCOMO_REVISION}.json"
    if not cache_file.exists():
        urllib.request.urlretrieve(LOCOMO_URL, cache_file)
    actual = sha256(cache_file)
    if actual != LOCOMO_SHA256:
        raise RuntimeError(
            f"LoCoMo checksum mismatch: expected={LOCOMO_SHA256} actual={actual}"
        )
    value = json.loads(cache_file.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise TypeError("official LoCoMo root must be a list")
    return value


def convert_locomo_to_mesa(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert official ``conversation`` sessions and ``qa`` evidence IDs."""
    scenarios: list[dict[str, Any]] = []
    for item_index, item in enumerate(raw_items):
        sample_id = str(item.get("sample_id", item_index))
        conversation = item.get("conversation", {})
        contexts: list[dict[str, Any]] = []
        known_ids: set[str] = set()
        if not isinstance(conversation, dict):
            raise TypeError(f"LoCoMo sample {sample_id} conversation must be an object")
        session_keys = sorted(
            (
                key
                for key in conversation
                if key.startswith("session_") and not key.endswith("_date_time")
            ),
            key=lambda value: int(value.split("_")[1]),
        )
        for session_key in session_keys:
            session_number = session_key.split("_")[1]
            session_date = conversation.get(f"session_{session_number}_date_time")
            for turn in conversation.get(session_key, []):
                context_id = str(turn["dia_id"])
                known_ids.add(context_id)
                contexts.append(
                    {
                        "id": context_id,
                        "text": f"[{turn['speaker']}]: {turn['text']}",
                        "metadata": {
                            "source": "snap-research/locomo",
                            "speaker": turn["speaker"],
                            "session": int(session_number),
                            "session_date_time": session_date,
                        },
                    }
                )

        questions: list[dict[str, Any]] = []
        for question_index, qa in enumerate(item.get("qa", [])):
            raw_evidence = " ".join(str(value) for value in qa.get("evidence", []))
            evidence = [
                f"D{int(session)}:{int(turn)}"
                for session, turn in re.findall(r"D:?(\d+):(\d+)", raw_evidence)
            ]
            missing = sorted(set(evidence).difference(known_ids))
            if missing:
                warnings.warn(
                    f"LoCoMo sample {sample_id} contains unresolved official evidence "
                    f"IDs {missing}; they are excluded from retrieval scoring",
                    stacklevel=2,
                )
                evidence = [value for value in evidence if value in known_ids]
            is_unanswerable_adversarial = qa.get("category") == 5 and "answer" not in qa
            ground_truth = (
                "Not mentioned" if is_unanswerable_adversarial else str(qa["answer"])
            )
            questions.append(
                {
                    "id": f"locomo_{sample_id}_q{question_index}",
                    "query": str(qa["question"]),
                    "ground_truth": ground_truth,
                    "expected_context_ids": (
                        [] if is_unanswerable_adversarial else evidence
                    ),
                    "evaluation_strategy": "llm_judge",
                }
            )
        if contexts and questions:
            scenarios.append(
                {
                    "id": f"locomo_{sample_id}",
                    "name": f"LoCoMo conversation {sample_id}",
                    "description": "Official LoCoMo long-term conversational memory sample",
                    "contexts": contexts,
                    "questions": questions,
                }
            )
    return scenarios


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--cache-dir", default=str(SCRIPT_DIR.parent / ".cache" / "locomo")
    )
    args = parser.parse_args()
    raw_items = download_locomo(Path(args.cache_dir))
    scenarios = convert_locomo_to_mesa(raw_items)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(scenarios, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "revision": LOCOMO_REVISION,
                "source_sha256": LOCOMO_SHA256,
                "scenarios": len(scenarios),
                "questions": sum(len(item["questions"]) for item in scenarios),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
