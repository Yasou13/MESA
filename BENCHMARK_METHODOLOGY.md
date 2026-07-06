# Benchmark Methodology

To ensure scientific validity and evaluation fairness across all systems, the MESA contradiction benchmarks adhere strictly to the following constraints:

## 1. Top-K Enforcement
An identical retrieval limit of **Top-K = 5** (`limit=5`) is strictly enforced on all benchmark clients (MESA, BareRAG, Mem0). This ensures no system gains an artificial advantage through larger context stuffing.

## 2. Embedding Model Parity
All clients utilize an identical dense embedding model: **`sentence-transformers/all-MiniLM-L6-v2`**. This isolates the architectural differences in retrieval algorithms (e.g., Graph vs Vector) rather than comparing fundamentally different representation models.

## 3. Scoring Standardization
All clients are evaluated using a unified **keyword-based Contradiction Resolution Accuracy (CRA)**. The scoring mechanism utilizes an `any_of` match mode: the system receives a score of 1.0 if ANY token from the ground truth answer is present in the synthesized output, otherwise 0.0. This guarantees all architectures are assessed against the exact same lenient factual recall baseline.
