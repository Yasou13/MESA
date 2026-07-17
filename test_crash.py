import sys

sys.path.insert(0, "./mesa-benchmark")
from mesa_benchmark.core.runner import BenchmarkRunner

try:
    runner = BenchmarkRunner(config_path="mesa-benchmark/config.yaml")
    runner.setup()
    print("Setup finished!")
except BaseException as e:
    print(f"Caught exception: {type(e)}: {e}")
