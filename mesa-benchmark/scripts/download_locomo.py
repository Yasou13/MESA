#!/usr/bin/env python3
"""
LoCoMo Dataset Downloader and Converter.

Downloads the LoCoMo benchmark dataset (used in Mem0's ECAI 2025 paper)
and converts it to MESA's BenchmarkScenario format.

Usage:
    python download_locomo.py
    python download_locomo.py --output ../datasets/locomo/dataset.json
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR.parent / "datasets" / "locomo" / "dataset.json"


def download_locomo_from_huggingface(cache_dir: Path) -> list:
    """Downloads LoCoMo dataset using the HuggingFace datasets library."""
    try:
        from datasets import load_dataset  # type: ignore

        ds = load_dataset("passing2961/LoCoMo", split="test", cache_dir=str(cache_dir))
        return list(ds)
    except ImportError:
        print(
            "[ERROR] 'datasets' library required. Install with: pip install datasets",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(
            f"[ERROR] Failed to download LoCoMo from HuggingFace: {e}", file=sys.stderr
        )
        print("[INFO] Attempting fallback JSON download...", file=sys.stderr)
        return _download_locomo_json_fallback(cache_dir)


def _download_locomo_json_fallback(cache_dir: Path) -> list:
    """Fallback: download raw JSON if HuggingFace datasets fails."""
    import urllib.request

    url = (
        "https://huggingface.co/datasets/passing2961/LoCoMo/resolve/main/data/test.json"
    )
    cache_file = cache_dir / "locomo_test.json"

    if not cache_file.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] Downloading LoCoMo from {url} ...")
        urllib.request.urlretrieve(url, str(cache_file))

    with open(cache_file, "r", encoding="utf-8") as f:
        result: list = json.load(f)
        return result


def convert_locomo_to_mesa(raw_items: list) -> list:
    """
    Converts LoCoMo items to MESA BenchmarkScenario JSON format.

    LoCoMo structure (typical):
    - Each item has conversation sessions and multi-hop QA pairs
    - Questions reference specific conversation turns as supporting facts
    """
    scenarios = []

    for idx, item in enumerate(raw_items):
        scenario_id = str(item.get("id", f"locomo_{idx}"))

        # Extract conversation turns as contexts
        contexts = []
        conversations = item.get("conversation", item.get("context", []))
        if isinstance(conversations, str):
            # Single string context
            contexts.append(
                {
                    "id": f"ctx_{idx}_0",
                    "text": conversations,
                    "metadata": {"source": "locomo"},
                }
            )
        elif isinstance(conversations, list):
            for c_idx, turn in enumerate(conversations):
                if isinstance(turn, dict):
                    text = turn.get(
                        "text", turn.get("content", turn.get("utterance", ""))
                    )
                    speaker = turn.get("speaker", turn.get("role", "unknown"))
                    turn_id = str(turn.get("id", f"ctx_{idx}_{c_idx}"))
                    if text and text.strip():
                        contexts.append(
                            {
                                "id": turn_id,
                                "text": (
                                    f"[{speaker}]: {text}"
                                    if speaker != "unknown"
                                    else text
                                ),
                                "metadata": {"source": "locomo", "speaker": speaker},
                            }
                        )
                elif isinstance(turn, str) and turn.strip():
                    contexts.append(
                        {
                            "id": f"ctx_{idx}_{c_idx}",
                            "text": turn,
                            "metadata": {"source": "locomo"},
                        }
                    )

        # Extract QA pairs
        questions = []
        qa_pairs = item.get("qa_pairs", item.get("questions", []))
        for q_idx, qa in enumerate(qa_pairs):
            if isinstance(qa, dict):
                query = qa.get("question", qa.get("query", ""))
                answer = qa.get("answer", qa.get("ground_truth", ""))
                supporting = qa.get("supporting_facts", qa.get("evidence", []))

                # Normalize supporting facts to context IDs
                expected_ids = []
                if isinstance(supporting, list):
                    for sf in supporting:
                        if isinstance(sf, str):
                            expected_ids.append(sf)
                        elif isinstance(sf, (int, float)):
                            expected_ids.append(f"ctx_{idx}_{int(sf)}")
                        elif isinstance(sf, dict):
                            expected_ids.append(str(sf.get("id", f"ctx_{idx}_{q_idx}")))

                if query and query.strip():
                    questions.append(
                        {
                            "id": f"locomo_{idx}_q{q_idx}",
                            "query": query,
                            "ground_truth": str(answer),
                            "expected_context_ids": expected_ids,
                            "evaluation_strategy": "llm_judge",
                        }
                    )

        if contexts and questions:
            scenarios.append(
                {
                    "id": scenario_id,
                    "name": f"LoCoMo Scenario {idx}",
                    "description": f"LoCoMo multi-hop dialogue memory test (item {idx})",
                    "contexts": contexts,
                    "questions": questions,
                }
            )

    return scenarios


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download LoCoMo benchmark and convert to MESA format."
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
        default=str(SCRIPT_DIR.parent / ".cache" / "locomo"),
        help="Cache directory for downloaded data.",
    )
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    print("=== LoCoMo Dataset Downloader ===")
    print(f"Cache dir: {cache_dir}")
    print(f"Output: {args.output}")

    # Download
    raw_items = download_locomo_from_huggingface(cache_dir)
    print(f"Downloaded {len(raw_items)} raw items from LoCoMo.")

    # Convert
    scenarios = convert_locomo_to_mesa(raw_items)
    print(f"Converted to {len(scenarios)} MESA BenchmarkScenarios.")

    # Save
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, indent=2, ensure_ascii=False)

    print(f"Saved to {out_path}")
    print(f"Total questions: {sum(len(s['questions']) for s in scenarios)}")
    print(f"Total contexts: {sum(len(s['contexts']) for s in scenarios)}")
    print("\nTo run MESA against LoCoMo:")
    print("  cd mesa-benchmark && python -m mesa_benchmark -c config_locomo.yaml")


if __name__ == "__main__":
    main()
