#!/usr/bin/env python3
# MESA v0.5.1 — Phase 2 Ablation Entry Point
"""
Executes the full ablation matrix to measure the incremental contribution
of each MESA sub-system (Graph Topology, Consensus Loop, Adaptive Router)
to overall Contradiction Resolution Accuracy (CRA).

Usage::

    python benchmarks/phase2_ablation/run_ablation.py
    python benchmarks/phase2_ablation/run_ablation.py --dataset path/to/dataset.jsonl
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add workspace root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Load environment variables
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
if "LLM_API_KEY" in os.environ:
    os.environ["GROQ_API_KEY"] = os.environ["LLM_API_KEY"]

from benchmarks.phase2_ablation.core.ablation_runner import AblationRunner  # noqa: E402

logger = logging.getLogger("MESA_AblationEntry")

DEFAULT_DATASET = "benchmarks/phase2_ablation/data/synthetic_dataset.jsonl"


async def main(dataset_path: str) -> None:
    if not Path(dataset_path).exists():
        logger.critical("Dataset not found: %s", dataset_path)
        sys.exit(1)

    runner = AblationRunner(dataset_path=dataset_path)
    rois = await runner.run_matrix()

    # Dump ROI results
    output_path = "benchmarks/phase2_ablation/results/ablation_roi.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(rois, f, indent=2)

    logger.info("Ablation ROI results written to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="MESA v0.5.1 — Phase 2 Ablation Study",
    )
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    args = parser.parse_args()

    asyncio.run(main(args.dataset))
