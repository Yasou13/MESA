import asyncio
import json
import logging
import time
from dataclasses import dataclass

from benchmarks.phase1_gatekeeper.contradiction_runner import (
    create_client,
    make_agent_id,
)
from benchmarks.phase2_ablation.core.finops import FinOpsTracker
from benchmarks.phase2_ablation.core.telemetry import TelemetryTracker
from benchmarks.phase2_ablation.generators.judge import LLMJudgeEvaluator

logger = logging.getLogger("MESA_AblationRunner")
logging.basicConfig(level=logging.INFO)


@dataclass
class MESAStateConfig:
    name: str
    enable_graph_topology: bool = True
    enable_consensus_loop: bool = True
    use_adaptive_router: bool = True


@dataclass
class AblationResult:
    config_name: str
    accuracy_percent: float
    total_cost_usd: float
    avg_ttft_sec: float


class AblationRunner:
    def __init__(self, dataset_path: str):
        self.dataset_path = dataset_path
        self.telemetry = TelemetryTracker()
        self.judge = LLMJudgeEvaluator()

    def calculate_component_roi(
        self, baseline: AblationResult, variant: AblationResult
    ) -> float:
        delta_accuracy = variant.accuracy_percent - baseline.accuracy_percent
        delta_cost_usd = variant.total_cost_usd - baseline.total_cost_usd
        delta_cost_micro_cents = delta_cost_usd * 1_000_000

        if delta_cost_micro_cents == 0:
            return float("inf") if delta_accuracy > 0 else 0.0

        return delta_accuracy / delta_cost_micro_cents

    async def _execute_workload(self, config: MESAStateConfig) -> AblationResult:
        logger.info(f"Running Ablation Matrix Block: {config.name}")
        finops = FinOpsTracker()

        # Load dataset
        scenarios = []
        with open(self.dataset_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    scenarios.append(json.loads(line))

        total_scenarios = len(scenarios)
        if total_scenarios == 0:
            raise ValueError("Dataset is empty.")

        total_is_correct_true = 0
        total_ttft = 0.0

        # Inject the config into the client factory/environment for this run
        import os

        os.environ["MESA_ENABLE_GRAPH"] = str(config.enable_graph_topology)
        os.environ["MESA_ENABLE_CONSENSUS"] = str(config.enable_consensus_loop)
        os.environ["MESA_ADAPTIVE_ROUTER"] = str(config.use_adaptive_router)

        client = create_client("mesa")
        await client.initialize()

        try:
            for scenario in scenarios:
                scenario_id = scenario["id"]
                agent_id = make_agent_id(scenario_id)

                await client.clear_memory(agent_id=agent_id)
                await client.add_memory(scenario["context_t0"], agent_id=agent_id)
                await client.add_memory(scenario["context_t1"], agent_id=agent_id)

                t0 = time.monotonic()
                result = await client.query(scenario["question"], agent_id=agent_id)
                ttft = time.monotonic() - t0
                total_ttft += ttft

                # We use the raw context strings returned by the system for judging
                actual_response = result.context
                if not actual_response:
                    actual_response = "No relevant context found."

                # Pass to strict LLM judge
                judge_res = await self.judge.evaluate(
                    context_t0=scenario["context_t0"],
                    context_t1=scenario["context_t1"],
                    query=scenario["question"],
                    expected_ground_truth=scenario["ground_truth_answer"],
                    actual_system_response=actual_response,
                )

                if judge_res.is_correct:
                    total_is_correct_true += 1
                else:
                    logger.debug(f"Failure on {scenario_id}: {judge_res.reasoning}")

                # TODO(finops): Instrument actual token usage from litellm response.
                # The client.query() return type needs to be extended to include
                # usage metrics from the LLM API response (prompt_tokens, completion_tokens).
                # Until then, FinOps cost tracking is not available for this workload.
                # DO NOT use hardcoded token counts — they produce fabricated cost claims.
        finally:
            await client.shutdown()

        accuracy = (total_is_correct_true / total_scenarios) * 100.0
        avg_ttft = total_ttft / total_scenarios

        self.telemetry.dump_evaluation_block(
            block_name=config.name,
            finops_data=finops.get_finops_report(),
            latency_data={"avg_ttft_sec": avg_ttft},
            results={"accuracy_percent": accuracy},
        )

        return AblationResult(
            config_name=config.name,
            accuracy_percent=accuracy,
            total_cost_usd=finops.get_total_cost(),
            avg_ttft_sec=avg_ttft,
        )

    async def run_matrix(self) -> dict[str, float]:
        configs = [
            MESAStateConfig("Naive_Vector_RAG", False, False, False),
            MESAStateConfig("Vector_Plus_Graph", True, False, False),
            MESAStateConfig("Vector_Plus_Consensus", False, True, False),
            MESAStateConfig("Full_MESA_Pipeline", True, True, True),
        ]

        baseline = await self._execute_workload(configs[0])
        logger.info(
            f"Baseline | Acc: {baseline.accuracy_percent}% | Cost: ${baseline.total_cost_usd:.6f}"
        )

        rois = {}
        for config in configs[1:]:
            variant = await self._execute_workload(config)
            roi = self.calculate_component_roi(baseline, variant)
            rois[config.name] = roi
            logger.info(
                f"Variant: {config.name} | Acc: {variant.accuracy_percent}% | Cost: ${variant.total_cost_usd:.6f} | ROI: {roi:.6f}"
            )

        return rois


if __name__ == "__main__":
    runner = AblationRunner(
        dataset_path="benchmarks/phase2_ablation/data/synthetic_dataset.jsonl"
    )
    asyncio.run(runner.run_matrix())
