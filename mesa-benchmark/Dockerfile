# Base Image
FROM python:3.10-slim-buster

# Working directory
WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y gcc build-essential && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and datasets
COPY mesa_benchmark/ ./mesa_benchmark/
COPY datasets/ ./datasets/
COPY config.yaml .

# Create output directories
RUN mkdir -p /app/reports /app/logs

# Entrypoint
ENTRYPOINT ["python", "-m", "mesa_benchmark"]
CMD ["--config", "config.yaml"]
