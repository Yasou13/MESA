#!/usr/bin/env python3
"""
BEAM Benchmark Dataset Downloader and Converter.

Downloads the BEAM benchmark dataset (Mohammadta/BEAM) from HuggingFace
and converts it to MESA's BenchmarkScenario format.
This script focuses on the 100K split for reasonable evaluation times.

Usage:
    python download_beam.py
    python download_beam.py --split 100K --output ../datasets/beam/dataset.json
"""

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

from ..core.paths import cache_root, data_root

DEFAULT_OUT = data_root() / "external" / "beam" / "v2" / "dataset.json"
LEGACY_DATASET = data_root() / "legacy" / "beam" / "v1" / "dataset.json"
BEAM_REVISION = "3205395e897e7318c7b094ef4e6047b9b82dbb03"
BEAM_RAW_SHA256 = {
    "100K": "170672bf631b59fd512acfc7ddf25504c103edd8a86655c714287a2c6519f2c1",
    "500K": "98dcff78da4b63bdcce8ba60e491554b4a4dab6083323778aaf9afaa2f8c8365",
    "1M": "1d64321ec640559056703bce44709d5b486d7eb9a0a091b6ad38e016eac7ff91",
}
BEAM_CONVERTED_SHA256 = {
    "100K": "e0e7286fe306d850e29010aa96c10bac0b032784ce35b6ce1cf8f7629d09edd7",
    "500K": "94a4abbdafc950ae7c4f5b152306500affcbeb9e32de00b13289fc9bc0ebc76e",
    "1M": "3f5e5cdb51df0762bceb119cb77173c761040439e4642577b23623967c129355",
}


def download_beam_from_huggingface(cache_dir: Path, split: str) -> list:
    """Downloads BEAM dataset using the HuggingFace datasets library."""
    try:
        from datasets import load_dataset  # type: ignore

        print(f"[INFO] Downloading BEAM split: {split}")
        ds = load_dataset(
            "Mohammadta/BEAM",
            revision=BEAM_REVISION,
            split=split,
            cache_dir=str(cache_dir),
        )
        rows = list(ds)
        expected = BEAM_RAW_SHA256.get(split)
        if expected:
            payload = json.dumps(
                rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            actual = hashlib.sha256(payload).hexdigest()
            if actual != expected:
                raise RuntimeError(
                    f"BEAM raw checksum mismatch: expected={expected} actual={actual}"
                )
        return rows
    except ImportError:
        print(
            "[ERROR] 'datasets' library required. Install with: pip install datasets",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Failed to download BEAM from HuggingFace: {e}", file=sys.stderr)
        sys.exit(1)


def convert_beam_to_mesa(raw_items: list, split: str) -> list:
    """
    Converts BEAM items to MESA BenchmarkScenario JSON format.

    BEAM structure:
    - conversation_id
    - chat (list of dicts)
    - probing_questions (stringified dict with categories)
    """
    scenarios = []

    for idx, item in enumerate(raw_items):
        scenario_id = str(item.get("conversation_id", f"beam_{split}_{idx}"))

        # Extract conversation turns as contexts
        contexts = []
        chat = item.get("chat", [])
        if isinstance(chat, str):
            try:
                chat = ast.literal_eval(chat)
            except Exception:
                pass

        # Flatten chat if it's a list of lists
        flat_chat = []
        for c in chat:
            if isinstance(c, list):
                flat_chat.extend(c)
            else:
                flat_chat.append(c)

        context_id_counts: dict[str, int] = {}
        for c_idx, turn in enumerate(flat_chat):
            if isinstance(turn, dict):
                text = turn.get("content", "")
                speaker = turn.get("role", "unknown")
                official_turn_id = str(turn.get("id", f"ctx_{idx}_{c_idx}"))
                context_id_counts[official_turn_id] = (
                    context_id_counts.get(official_turn_id, 0) + 1
                )
                turn_id = (
                    official_turn_id
                    if context_id_counts[official_turn_id] == 1
                    else f"{official_turn_id}~{context_id_counts[official_turn_id]}"
                )
                if text and text.strip():
                    contexts.append(
                        {
                            "id": turn_id,
                            "text": (
                                f"[{speaker.upper()}]: {text}"
                                if speaker != "unknown"
                                else text
                            ),
                            "metadata": {
                                "source": "beam",
                                "speaker": speaker,
                                "time_anchor": turn.get("time_anchor"),
                                "official_context_id": official_turn_id,
                            },
                        }
                    )

        # Extract QA pairs from probing_questions
        questions = []
        pq_str = item.get("probing_questions", "{}")
        try:
            pq_dict = ast.literal_eval(pq_str) if isinstance(pq_str, str) else pq_str

            for category, q_list in pq_dict.items():
                if not isinstance(q_list, list):
                    continue

                for q_idx, qa in enumerate(q_list):
                    query = qa.get("question", "")
                    ideal_response = qa.get("ideal_response", "")
                    rubric = str(qa.get("rubric", "")).strip()

                    if query and query.strip():
                        questions.append(
                            {
                                "id": f"{scenario_id}_{category}_q{q_idx}",
                                "query": query,
                                "reference_answers": (
                                    [str(ideal_response)]
                                    if str(ideal_response).strip()
                                    else []
                                ),
                                "rubric": [rubric] if rubric else [],
                                "category": category,
                                "difficulty": qa.get("difficulty"),
                                "evaluation_strategy": "rubric_judge",
                                "metadata": {
                                    "source": "Mohammadta/BEAM",
                                },
                            }
                        )
        except Exception as e:
            print(f"[WARNING] Failed to parse probing_questions for {scenario_id}: {e}")

        if contexts and questions:
            scenarios.append(
                {
                    "id": scenario_id,
                    "name": f"BEAM Scenario {scenario_id}",
                    "description": f"BEAM long-term memory benchmark ({split})",
                    "contexts": contexts,
                    "questions": questions,
                }
            )

    return scenarios


def upgrade_legacy_beam(legacy_path: Path) -> list:
    """Losslessly lift labels discarded by the v1 runtime schema."""
    scenarios = cast(
        list[dict[str, Any]], json.loads(legacy_path.read_text(encoding="utf-8"))
    )
    for scenario in scenarios:
        for question in scenario.get("questions", []):
            metadata = question.get("metadata", {})
            reference = str(question.pop("ground_truth", "")).strip()
            rubric = str(metadata.pop("rubric", "")).strip()
            question["reference_answers"] = [reference] if reference else []
            question["rubric"] = [rubric] if rubric else []
            question["category"] = metadata.pop("category", None)
            question["difficulty"] = metadata.pop("difficulty", None)
            question["evaluation_strategy"] = "rubric_judge"
            question["metadata"] = {"source": "Mohammadta/BEAM", **metadata}
    return scenarios


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download BEAM benchmark and convert to MESA format."
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_OUT),
        help="Output JSON path for MESA-format dataset.",
    )
    parser.add_argument(
        "--upgrade-legacy",
        type=str,
        help="Upgrade a pinned v1 MESA conversion without downloading it again.",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=str(cache_root() / "beam"),
        help="Cache directory for downloaded data.",
    )
    parser.add_argument(
        "--split",
        type=str,
        default="100K",
        choices=["100K", "500K", "1M"],
        help="Which split to download (default: 100K).",
    )
    parser.add_argument(
        "--force", action="store_true", help="Regenerate even if output is verified."
    )
    args = parser.parse_args()

    out_path = Path(args.output)
    expected_converted = BEAM_CONVERTED_SHA256[args.split]
    if (
        not args.force
        and not args.upgrade_legacy
        and out_path.exists()
        and hashlib.sha256(out_path.read_bytes()).hexdigest() == expected_converted
    ):
        print(
            json.dumps(
                {
                    "output": str(out_path),
                    "split": args.split,
                    "converted_sha256": expected_converted,
                    "status": "verified-existing",
                },
                indent=2,
            )
        )
        return

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== BEAM Benchmark Downloader ({args.split}) ===")
    print(f"Cache dir: {cache_dir}")
    print(f"Output: {args.output}")

    if args.upgrade_legacy:
        scenarios = upgrade_legacy_beam(Path(args.upgrade_legacy))
    else:
        raw_items = download_beam_from_huggingface(cache_dir, args.split)
        print(f"Downloaded {len(raw_items)} raw items from BEAM {args.split}.")
        scenarios = convert_beam_to_mesa(raw_items, args.split)
    print(f"Converted to {len(scenarios)} MESA BenchmarkScenarios.")

    # Save
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, indent=2, ensure_ascii=False)
    actual_converted = hashlib.sha256(out_path.read_bytes()).hexdigest()
    if not args.upgrade_legacy and actual_converted != expected_converted:
        raise RuntimeError(
            "BEAM converted checksum mismatch: "
            f"expected={expected_converted} actual={actual_converted}"
        )

    print(f"Saved to {out_path}")
    print(f"Total questions: {sum(len(s['questions']) for s in scenarios)}")
    print(f"Total contexts (turns): {sum(len(s['contexts']) for s in scenarios)}")
    print("\nTo run the packaged benchmark against BEAM:")
    print("  mesa-benchmark run --config resource://configs/release/beam_128k.yaml")


if __name__ == "__main__":
    main()
