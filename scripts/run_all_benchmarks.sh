#!/bin/bash
export PYTHONPATH=$(pwd)/mesa-benchmark
export OLLAMA_HOST=http://192.168.1.103:11434
export OPENAI_API_KEY="sk-dummy"
export OPENAI_BASE_URL="http://192.168.1.103:11434/v1"
export HF_HUB_OFFLINE=1

configs=("config.yaml" "config_beam.yaml" "config_contradiction.yaml" "config_multi_hop.yaml" "config_mem0.yaml")

for config in "${configs[@]}"; do
    if [ -f "mesa-benchmark/$config" ]; then
        echo "Running full benchmark for $config..."
        venv/bin/python scripts/reproduce_benchmark.py --config "mesa-benchmark/$config" --seeds 42
    else
        echo "Warning: mesa-benchmark/$config not found."
    fi
done

echo "Full benchmarks completed."
