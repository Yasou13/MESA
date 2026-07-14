import yaml
from mesa_benchmark.core.config import BenchmarkConfig
from mesa_benchmark.metrics.calculator import calculate_metrics_from_jsonl
from mesa_benchmark.reports.reporter import MarkdownReporter

# Load config
with open("config_multi_hop.yaml") as f:
    config_dict = yaml.safe_load(f)
config = BenchmarkConfig(**config_dict)

run_id = "2fd2ea68-7787-402d-9877-c34756c1dd75"

# Evaluate metrics
metrics = calculate_metrics_from_jsonl(f"results_{run_id}.jsonl")
metrics_dict = metrics.model_dump()

# Generate Report
reporter = MarkdownReporter(run_id, config)
report_path = reporter.generate_report_from_dict(metrics_dict)
print(f"Report generated at: {report_path}")
