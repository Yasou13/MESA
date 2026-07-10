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
    def __init__(self, dataset_path: str | Path):
        self.dataset_path = Path(dataset_path)
        self.scenarios: List[BenchmarkScenario] = []

    def load(self) -> None:
        """Loads and validates the dataset from JSON."""
        if not self.dataset_path.exists():
            # Fallback 1: check relative to this module's directory (mesa_benchmark/datasets/)
            fallback = Path(__file__).resolve().parent / self.dataset_path.name
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

                # Check for duplicate scenario IDs
                if scenario.id in scenario_ids:
                    raise DatasetLoaderError(
                        f"Duplicate scenario ID found: {scenario.id}"
                    )
                scenario_ids.add(scenario.id)

                # Validate that expected_context_ids exist in the contexts
                context_ids = {ctx.id for ctx in scenario.contexts}
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
