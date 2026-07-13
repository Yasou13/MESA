# Rules

## Safe Cleanup and Data Preservation
- **NEVER** delete `results/`, `logs/`, or output data directories when cleaning up test environments or running benchmarks.
- Always preserve user-generated benchmark data, metrics, and output artifacts. 
- Only delete safe, temporary cache files (e.g., `state.json`, `.mesa_state`, `__pycache__`) when re-initializing a test.
- If you must clear previous results, move them to an archive/backup folder instead of using `rm -rf`.
