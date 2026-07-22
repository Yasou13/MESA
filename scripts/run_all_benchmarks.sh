#!/bin/bash
export PYTHONPATH="$(pwd)/mesa-benchmark"
: "${BENCHMARK_OLLAMA_URL:?Set BENCHMARK_OLLAMA_URL to the remote Ollama root URL}"
: "${BENCHMARK_GENERATOR_MODEL:?Set BENCHMARK_GENERATOR_MODEL to an exact Ollama tag}"
: "${BENCHMARK_JUDGE_MODEL:?Set BENCHMARK_JUDGE_MODEL to an exact Ollama tag}"
export OLLAMA_HOST="$BENCHMARK_OLLAMA_URL"
export OPENAI_API_KEY="${OPENAI_API_KEY:-ollama}"
export OPENAI_BASE_URL="${BENCHMARK_OLLAMA_URL%/}/v1"
export HF_HUB_OFFLINE=1

configs=("config.yaml" "config_beam.yaml" "config_contradiction.yaml" "config_multi_hop.yaml" "config_mem0.yaml")

venv/bin/mesa-benchmark ollama-preflight --config mesa-benchmark/config_mini_mesa.yaml

for config in "${configs[@]}"; do
    if [ -f "mesa-benchmark/$config" ]; then
        echo "Running full benchmark for $config..."
        venv/bin/python scripts/reproduce_benchmark.py --config "mesa-benchmark/$config" --seeds 42
    else
        echo "Warning: mesa-benchmark/$config not found."
    fi
done

echo "Full benchmarks completed."
