# Base Image
FROM python:3.10-slim-buster

# Working directory
WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y gcc build-essential && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (pinned versions for reproducibility)
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt

# Copy source code and datasets
COPY mesa_benchmark/ ./mesa_benchmark/
COPY datasets/ ./datasets/
COPY config.yaml .
COPY config_locomo.yaml .

# Copy reproducibility scripts
COPY scripts/ ./scripts/

# Create output directories
RUN mkdir -p /app/reports /app/logs /app/results

# Default entrypoint runs the benchmark
ENTRYPOINT ["python", "-m", "mesa_benchmark"]
CMD ["--config", "config.yaml"]
