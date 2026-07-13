import tempfile
from pathlib import Path

from mesa_benchmark.core.state_manager import StateManager


def test_state_manager_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_file = Path(tmp) / "state.json"
        sm = StateManager(state_file=state_file)

        # Init
        state = sm.initialize_state("run_123", "/tmp/results.jsonl")
        assert state.run_id == "run_123"
        assert state.status == "running"
        assert state_file.exists()

        # Update progress
        sm.update_progress(iteration=2, scenario_idx=10)
        assert sm.state is not None
        assert sm.state.current_iteration == 2

        # Load
        sm2 = StateManager(state_file=state_file)
        loaded = sm2.load_state()
        assert loaded is not None
        assert loaded.current_iteration == 2

        # Mark completed
        sm2.mark_completed()
        assert sm2.state is not None
        assert sm2.state.status == "completed"
