import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Tuple

logger = logging.getLogger("MESA_Telemetry")


class TelemetryTracker:
    """Handles latency logging and JSON telemetry dumps for evaluation blocks."""

    def __init__(self, output_dir: str = "benchmarks/phase2_ablation/results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    async def track_stream_ttft(
        stream: AsyncGenerator[Any, None],
    ) -> AsyncGenerator[Tuple[float | None, Any], None]:
        """
        Wraps an asynchronous stream generator to track Time-To-First-Token (TTFT).
        Captures time.perf_counter() strictly upon the first yielded chunk.
        """
        start_time = time.perf_counter()
        first_token = True

        async for chunk in stream:
            if first_token:
                ttft = time.perf_counter() - start_time
                first_token = False
                yield ttft, chunk
            else:
                yield None, chunk

    def dump_evaluation_block(
        self,
        block_name: str,
        finops_data: Dict[str, Any],
        latency_data: Dict[str, Any],
        results: Dict[str, Any],
    ) -> None:
        """
        Builds the JSON logging hooks to dump the compiled FinOps and TTFT latency parameters per evaluation block.
        Outputs data for rigorous post-mortem dashboard rendering.
        """
        output_file = self.output_dir / f"{block_name}_telemetry.json"

        payload = {
            "block_name": block_name,
            "timestamp": time.time(),
            "finops": finops_data,
            "latency": latency_data,
            "results": results,
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4)

        logger.info(f"Telemetry dumped successfully to {output_file}")
