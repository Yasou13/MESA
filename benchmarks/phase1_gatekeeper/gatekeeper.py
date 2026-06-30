#!/usr/bin/env python3
# MESA v0.5.1 — Phase 1 Deployment Gatekeeper
# Enforces strict execution order and automated gatekeeping for the MESA evaluation pipeline.

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Add workspace root to python path to allow imports from mesa_evals
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from contradiction_runner import (
    create_client,
    run_benchmark,
)

logger = logging.getLogger("MESA_Gatekeeper")


async def run_gatekeeper() -> None:
    dataset_path = "benchmarks/phase2_ablation/data/synthetic_dataset.jsonl"

    # =========================================================================
    # PHASE A: INITIALIZATION
    # =========================================================================
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("PHASE A: INITIALIZATION")
    logger.info(f"Targeting dataset: {dataset_path}")

    if not os.path.exists(dataset_path):
        logger.critical(f"Dataset not found at {dataset_path}. Halting pipeline.")
        sys.exit(1)

    # =========================================================================
    # PHASE B: MESA SOLO RUN
    # =========================================================================
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("PHASE B: MESA SOLO RUN (BareRAG and Mem0 skipped)")

    try:
        mesa_client = create_client("mesa")
    except ValueError:
        logger.critical("🚨 PIPELINE HALTED 🚨")
        logger.critical(
            "MESA client is not yet implemented in `create_client` factory."
        )
        logger.critical(
            "Please implement the 'mesa' client in mesa_evals/contradiction_runner.py."
        )
        sys.exit(1)

    logger.info("Executing benchmark for MESA system...")
    mesa_report = await run_benchmark(
        mesa_client, client_type="mesa", dataset_path=dataset_path
    )

    # =========================================================================
    # PHASE C: HUMAN-IN-THE-LOOP VALIDATION
    # =========================================================================
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("PHASE C: OUTPUT DUMP & VALIDATION")

    mesa_dump_path = "benchmarks/mesa_solo_run_dump.json"
    os.makedirs(os.path.dirname(mesa_dump_path), exist_ok=True)
    with open(mesa_dump_path, "w", encoding="utf-8") as f:
        json.dump(mesa_report.to_json(), f, indent=2, ensure_ascii=False)

    logger.info(f"✅ MESA outputs dumped to {mesa_dump_path} for manual inspection.")

    # =========================================================================
    # CRA GATEKEEPER LOGIC
    # =========================================================================
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("GATEKEEPER: CRA THRESHOLD CHECK")

    cra_score = mesa_report.accuracy
    cra_percentage = cra_score * 100

    logger.info(f"MESA Context Resolution Accuracy (CRA): {cra_percentage:.2f}%")

    # The Threshold Rule
    if cra_score < 0.90:
        logger.critical("🚨 PIPELINE HALTED 🚨")
        logger.critical(
            f"MESA CRA score ({cra_percentage:.2f}%) is below the strict threshold."
        )
        sys.exit(1)

    logger.info("✅ GATEKEEPER PASSED: CRA >= 90%")
    logger.info("Unlocking full benchmark suite for comparative analysis...")

    # =========================================================================
    # FULL BENCHMARK EXECUTION
    # =========================================================================
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("PHASE D: EXECUTING FULL BENCHMARK SUITE")

    clients_to_run = ["mem0", "barerag"]
    reports = {"mesa": mesa_report}

    for c_type in clients_to_run:
        logger.info(f"Executing benchmark for {c_type}...")
        try:
            client = create_client(c_type)
            rep = await run_benchmark(
                client, client_type=c_type, dataset_path=dataset_path
            )
            reports[c_type] = rep
        except Exception as e:
            logger.error(f"Failed to run client '{c_type}': {e}. Skipping.")

    # =========================================================================
    # FINAL COMPARATIVE SCIENTIFIC LEADERBOARD MATRIX
    # =========================================================================
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("FINAL COMPARATIVE LEADERBOARD MATRIX")
    logger.info(
        f"{'Client':<15} | {'CRA (%)':<10} | {'T0 Acc (%)':<12} | {'T1 Acc (%)':<12} | {'P99 Latency':<15}"
    )
    logger.info("-" * 75)

    for c_type, rep in reports.items():
        cra = rep.accuracy * 100
        t0 = rep.t0_valid_accuracy * 100
        t1 = rep.t1_valid_accuracy * 100
        p99 = rep.latency.get("p99_ms", 0.0)
        logger.info(
            f"{c_type:<15} | {cra:<10.2f} | {t0:<12.2f} | {t1:<12.2f} | {p99:.2f}ms"
        )

    logger.info("═══════════════════════════════════════════════════════════════")

    # Dump full suite results
    full_dump_path = "benchmarks/contradiction_leaderboard_results.json"
    full_results = {name: rep.to_json() for name, rep in reports.items()}
    with open(full_dump_path, "w", encoding="utf-8") as f:
        json.dump(full_results, f, indent=2, ensure_ascii=False)

    logger.info(f"✅ Full benchmark results dumped to {full_dump_path}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(run_gatekeeper())
