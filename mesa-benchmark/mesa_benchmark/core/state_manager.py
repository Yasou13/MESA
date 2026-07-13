import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .exceptions import StateError


class ExecutionState(BaseModel):
    """Represents the current state of a benchmark run for resilience."""

    run_id: str = Field(..., description="Unique identifier for this benchmark run.")
    current_iteration: int = Field(
        1, description="Current iteration (out of total iterations)."
    )
    current_scenario_idx: int = Field(
        0, description="Index of the current scenario in the dataset."
    )
    status: str = Field(
        "running",
        description="Status of the run (e.g., 'running', 'completed', 'failed').",
    )
    results_file: str = Field(
        ..., description="Path to the .jsonl file where results are appended."
    )
    error_message: Optional[str] = Field(
        None, description="Last error message if failed."
    )


class StateManager:
    def __init__(self, state_file: str | Path = "state.json"):
        self.state_file = Path(state_file)
        self.state: Optional[ExecutionState] = None

    def initialize_state(self, run_id: str, results_file: str) -> ExecutionState:
        """Initializes a fresh state."""
        self.state = ExecutionState(run_id=run_id, results_file=str(results_file))
        self.save_state()
        return self.state

    def load_state(self) -> Optional[ExecutionState]:
        """Loads state from disk if it exists."""
        if not self.state_file.exists():
            return None

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.state = ExecutionState(**data)
            return self.state
        except Exception as e:
            raise StateError(f"Failed to load state from {self.state_file}: {e}")

    def save_state(self) -> None:
        """Saves current state to disk."""
        if not self.state:
            raise StateError("No state to save. Initialize state first.")

        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                state_dict = (
                    self.state.model_dump()
                    if hasattr(self.state, "model_dump")
                    else self.state.dict()
                )
                json.dump(state_dict, f, indent=4)
        except Exception as e:
            raise StateError(f"Failed to save state to {self.state_file}: {e}")

    def update_progress(self, iteration: int, scenario_idx: int) -> None:
        """Updates the progress counters and saves to disk."""
        if not self.state:
            raise StateError("State not initialized.")

        self.state.current_iteration = iteration
        self.state.current_scenario_idx = scenario_idx
        self.save_state()

    def mark_completed(self) -> None:
        if not self.state:
            return
        self.state.status = "completed"
        self.save_state()

    def mark_failed(self, error_message: str) -> None:
        if not self.state:
            return
        self.state.status = "failed"
        self.state.error_message = error_message
        self.save_state()
