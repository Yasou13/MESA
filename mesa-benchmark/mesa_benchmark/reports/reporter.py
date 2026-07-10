import json
from pathlib import Path
from typing import Dict, Any

class MarkdownReporter:
    def __init__(self, run_id: str, config: Any):
        self.run_id = run_id
        self.config = config

    def generate_report(self, metrics: Any) -> str:
        report_lines = [
            f"# Benchmark Report: {self.config.suite_name}",
            f"Run ID: {self.run_id}",
            "## Summary Metrics",
        ]
        
        # Convert metrics to dict if it's a Pydantic model
        metrics_dict = metrics.model_dump() if hasattr(metrics, 'model_dump') else metrics.dict() if hasattr(metrics, 'dict') else vars(metrics) if hasattr(metrics, '__dict__') else metrics
        for k, v in metrics_dict.items():
            if isinstance(v, dict):
                report_lines.append(f"### {k}")
                for sub_k, sub_v in v.items():
                    report_lines.append(f"- **{sub_k}**: {sub_v}")
            else:
                report_lines.append(f"- **{k}**: {v}")
                
        report_path = f"report_{self.run_id}.md"
        with open(report_path, "w") as f:
            f.write("\\n".join(report_lines))
        return report_path
