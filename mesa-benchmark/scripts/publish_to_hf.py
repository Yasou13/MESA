#!/usr/bin/env python3
"""
Publish MESA benchmark dataset and results to HuggingFace Hub.

Creates a versioned dataset with proper dataset card, license, and citation info.

Usage:
    python publish_to_hf.py --dataset-path ../mesa_benchmark/datasets/comprehensive_200_dataset.json
    python publish_to_hf.py --dataset-path ../datasets/locomo/dataset.json --repo-id mesa-project/mesa-locomo-benchmark
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


def create_dataset_card(
    repo_id: str,
    dataset_name: str,
    num_scenarios: int,
    num_questions: int,
    version: str,
) -> str:
    """Generates a HuggingFace dataset card (README.md) with YAML frontmatter."""
    return f"""---
language:
  - en
  - tr
license: apache-2.0
size_categories:
  - 100<n<1K
task_categories:
  - question-answering
  - text-retrieval
tags:
  - memory-systems
  - rag-benchmark
  - multi-hop-reasoning
  - contradiction-detection
  - graph-augmented-retrieval
pretty_name: "{dataset_name}"
dataset_info:
  features:
    - name: id
      dtype: string
    - name: name
      dtype: string
    - name: description
      dtype: string
    - name: contexts
      list:
        - name: id
          dtype: string
        - name: text
          dtype: string
    - name: questions
      list:
        - name: id
          dtype: string
        - name: query
          dtype: string
        - name: ground_truth
          dtype: string
        - name: expected_context_ids
          sequence: string
        - name: evaluation_strategy
          dtype: string
  config_name: default
  splits:
    - name: test
      num_examples: {num_scenarios}
---

# {dataset_name}

## Dataset Description

A comprehensive benchmark dataset for evaluating memory-augmented RAG systems,
specifically designed to test multi-hop graph traversal, contradiction resolution,
and distractor quarantine capabilities.

### Dataset Summary

- **{num_scenarios} scenarios** with **{num_questions} questions** across 4 difficulty tiers:
  - **Single-Hop Retrieval** (40%): Direct fact lookup from a single memory node
  - **Multi-Hop Graph Traversal** (30%): Questions requiring connection of 2+ memory facts
  - **Hard-Negative Contradiction** (15%): Outdated vs. authoritative fact resolution
  - **Out-of-Domain Distractor** (15%): Irrelevant context quarantine testing

### Evaluation Methodology

- **Dual scoring**: Both keyword/exact-match and LLM-as-a-Judge evaluation
- **Multi-model judging**: GPT-4o-mini + Claude Sonnet for independent verification
- **Agreement reporting**: Cohen's Kappa between evaluators
- **Statistical rigor**: 5-seed runs with Mean ± Std and Welch's t-test p-values

### Supported Systems

| System | Adapter | Status |
|--------|---------|--------|
| MESA | `MesaClientAdapter` | ✅ Primary |
| Mem0 | `Mem0ClientAdapter` | ✅ Baseline |
| Zep | `ZepClientAdapter` | ✅ Competitor |
| Letta/MemGPT | `LettaClientAdapter` | ✅ Competitor |
| BareRAG | `DummyClientAdapter` | ✅ Control |

### Version

- **Dataset Version**: {version}
- **MESA Version**: 0.6.1+

### Citation

```bibtex
@misc{{mesa_benchmark_2026,
  title={{MESA Memory Benchmark: A Multi-Tier Evaluation Suite for Memory-Augmented RAG Systems}},
  author={{MESA Contributors}},
  year={{2026}},
  url={{https://huggingface.co/datasets/{repo_id}}},
}}
```

### License

Apache License 2.0
"""


def publish(
    dataset_path: str,
    repo_id: str,
    version: str,
    token: str | None = None,
) -> None:
    """Uploads the dataset and card to HuggingFace Hub."""
    try:
        from huggingface_hub import HfApi
    except ImportError:
        print(
            "[ERROR] huggingface_hub required. Install with: pip install huggingface_hub",
            file=sys.stderr,
        )
        sys.exit(1)

    path = Path(dataset_path)
    if not path.exists():
        print(f"[ERROR] Dataset not found: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    num_scenarios = len(data) if isinstance(data, list) else len(data.get("data", []))
    num_questions = sum(
        len(s.get("questions", []))
        for s in (data if isinstance(data, list) else data.get("data", []))
    )

    dataset_name = f"MESA Memory Benchmark v{version}"

    # Create staging directory
    staging = REPO_ROOT / ".hf_staging"
    staging.mkdir(parents=True, exist_ok=True)

    # Write dataset card
    card = create_dataset_card(
        repo_id, dataset_name, num_scenarios, num_questions, version
    )
    (staging / "README.md").write_text(card, encoding="utf-8")

    # Copy dataset
    shutil.copy2(path, staging / "test.json")

    # Upload
    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)
    api.upload_folder(
        folder_path=str(staging),
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=f"Upload MESA benchmark dataset v{version}",
    )

    print(f"✅ Published to https://huggingface.co/datasets/{repo_id}")

    # Cleanup
    shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish MESA benchmark dataset to HuggingFace Hub."
    )
    parser.add_argument(
        "--dataset-path",
        type=str,
        default=str(
            REPO_ROOT / "mesa_benchmark" / "datasets" / "comprehensive_200_dataset.json"
        ),
        help="Path to the dataset JSON file.",
    )
    parser.add_argument(
        "--repo-id",
        type=str,
        default="mesa-project/mesa-benchmark",
        help="HuggingFace repository ID (org/name).",
    )
    parser.add_argument(
        "--version",
        type=str,
        default="2.0",
        help="Dataset version.",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="HuggingFace API token (or set HF_TOKEN env var).",
    )
    args = parser.parse_args()

    token = args.token or __import__("os").environ.get("HF_TOKEN")
    publish(args.dataset_path, args.repo_id, args.version, token)


if __name__ == "__main__":
    main()
