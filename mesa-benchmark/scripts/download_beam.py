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
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR.parent / "datasets" / "beam" / "dataset.json"
BEAM_REVISION = "3205395e897e7318c7b094ef4e6047b9b82dbb03"


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
        return list(ds)
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

        for c_idx, turn in enumerate(flat_chat):
            if isinstance(turn, dict):
                text = turn.get("content", "")
                speaker = turn.get("role", "unknown")
                turn_id = str(turn.get("id", f"ctx_{idx}_{c_idx}"))
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
                    rubric = qa.get("rubric", "")

                    if query and query.strip():
                        questions.append(
                            {
                                "id": f"{scenario_id}_{category}_q{q_idx}",
                                "query": query,
                                "ground_truth": str(ideal_response),
                                "evaluation_strategy": "llm_judge",
                                "metadata": {
                                    "category": category,
                                    "difficulty": qa.get("difficulty"),
                                    "rubric": rubric,
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
        "--cache-dir",
        type=str,
        default=str(SCRIPT_DIR.parent / ".cache" / "beam"),
        help="Cache directory for downloaded data.",
    )
    parser.add_argument(
        "--split",
        type=str,
        default="100K",
        choices=["100K", "500K", "1M"],
        help="Which split to download (default: 100K).",
    )
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== BEAM Benchmark Downloader ({args.split}) ===")
    print(f"Cache dir: {cache_dir}")
    print(f"Output: {args.output}")

    # Download
    raw_items = download_beam_from_huggingface(cache_dir, args.split)
    print(f"Downloaded {len(raw_items)} raw items from BEAM {args.split}.")

    # Convert
    scenarios = convert_beam_to_mesa(raw_items, args.split)
    print(f"Converted to {len(scenarios)} MESA BenchmarkScenarios.")

    # Save
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, indent=2, ensure_ascii=False)

    print(f"Saved to {out_path}")
    print(f"Total questions: {sum(len(s['questions']) for s in scenarios)}")
    print(f"Total contexts (turns): {sum(len(s['contexts']) for s in scenarios)}")
    print("\nTo run MESA against BEAM:")
    print(
        "  python -m mesa_evals.run_beam_eval --adapter mesa --dataset " + str(out_path)
    )


if __name__ == "__main__":
    main()
