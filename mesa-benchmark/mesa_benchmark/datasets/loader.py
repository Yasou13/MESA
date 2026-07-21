import json
from pathlib import Path
from typing import List, Set

from pydantic import ValidationError

from ..core.exceptions import BenchmarkError
from .schemas import BenchmarkScenario


class DatasetLoaderError(BenchmarkError):
    """Raised when there's an error loading or validating a dataset."""

    pass


class DatasetManager:
    def __init__(self, dataset_path: str | Path, noise_ratio: float = 0.0):
        self.dataset_path = Path(dataset_path)
        self.noise_ratio = noise_ratio
        self.scenarios: List[BenchmarkScenario] = []

    def load(self) -> None:
        """Loads and validates the dataset from JSON."""
        if not self.dataset_path.exists():
            # Fallback 1: check relative to the mesa-benchmark project root
            project_root = Path(__file__).resolve().parent.parent.parent
            fallback = project_root / self.dataset_path
            if fallback.exists():
                self.dataset_path = fallback
            else:
                raise DatasetLoaderError(f"Dataset file not found: {self.dataset_path}")

        try:
            with open(self.dataset_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise DatasetLoaderError(f"Failed to parse JSON dataset: {e}")

        if not isinstance(data, list):
            raise DatasetLoaderError("Dataset root must be a list of scenarios.")

        self.scenarios = []
        scenario_ids: Set[str] = set()

        for idx, item in enumerate(data):
            try:
                scenario = BenchmarkScenario(**item)

                # Apply text corruption if noise_ratio > 0
                if self.noise_ratio > 0.0:
                    import random
                    import string

                    num_to_corrupt = int(len(scenario.contexts) * self.noise_ratio)
                    if num_to_corrupt > 0:
                        contexts_to_corrupt = random.sample(
                            scenario.contexts, num_to_corrupt
                        )
                        for ctx in contexts_to_corrupt:
                            # Append random gibberish to simulate noisy data extraction
                            noise = " ".join(
                                "".join(random.choices(string.ascii_letters, k=8))
                                for _ in range(5)
                            )
                            ctx.text = f"{ctx.text} {noise}"

                # Check for duplicate scenario IDs
                if scenario.id in scenario_ids:
                    raise DatasetLoaderError(
                        f"Duplicate scenario ID found: {scenario.id}"
                    )
                scenario_ids.add(scenario.id)

                # Validate that expected_context_ids exist in the contexts
                context_ids = set()
                for ctx in scenario.contexts:
                    if ctx.id in context_ids:
                        raise DatasetLoaderError(
                            f"Duplicate context ID '{ctx.id}' in scenario '{scenario.id}'"
                        )
                    context_ids.add(ctx.id)

                for q in scenario.questions:
                    for ec_id in q.expected_context_ids:
                        if ec_id not in context_ids:
                            raise DatasetLoaderError(
                                f"Question '{q.id}' references non-existent context ID '{ec_id}'"
                            )

                self.scenarios.append(scenario)

            except ValidationError as e:
                raise DatasetLoaderError(
                    f"Schema validation failed for scenario at index {idx}: {e}"
                )

    def get_scenario(self, index: int) -> BenchmarkScenario:
        if index < 0 or index >= len(self.scenarios):
            raise IndexError("Scenario index out of range.")
        return self.scenarios[index]

    def __len__(self) -> int:
        return len(self.scenarios)
