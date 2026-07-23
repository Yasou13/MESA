from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import DashboardJob

CSV_FIELDS = (
    "client",
    "shard_id",
    "scenario_id",
    "question_id",
    "query",
    "ground_truth",
    "actual_answer",
    "expected_context_ids",
    "retrieved_context_ids",
    "is_correct",
    "score",
    "answer_exact_match",
    "answer_token_f1",
    "retrieval_latency_ms",
    "generation_latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "failure_attribution",
    "infrastructure_error",
)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return normalized[:80] or "benchmark"


def _safe_cell(value: Any) -> str:
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif value is None:
        text = ""
    else:
        text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for task in plan.get("tasks", []):
        outcome = task.get("outcome") or {}
        path = outcome.get("results_file")
        if not path or not Path(path).is_file():
            continue
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["client"] = task["client"]
            row["shard_id"] = task["shard_id"]
            values.append(row)
    return values


def _safe_plan(plan: dict[str, Any]) -> dict[str, Any]:
    request = dict(plan.get("request") or {})
    request.pop("config", None)
    return {
        "schema_version": plan.get("schema_version"),
        "id": plan.get("id"),
        "name": plan.get("name"),
        "profile": plan.get("profile"),
        "created_at": plan.get("created_at"),
        "seed": plan.get("seed"),
        "clients": plan.get("clients", []),
        "source_dataset_sha256": plan.get("source_dataset_sha256"),
        "shard_mode": plan.get("shard_mode"),
        "target_shard_minutes": plan.get("target_shard_minutes"),
        "time_limit_minutes": plan.get("time_limit_minutes"),
        "request": request,
        "shards": [
            {
                key: shard.get(key)
                for key in (
                    "id",
                    "index",
                    "scenarios",
                    "contexts",
                    "questions",
                    "scenario_ids",
                )
            }
            for shard in plan.get("shards", [])
        ],
        "tasks": [
            {
                key: task.get(key)
                for key in ("id", "shard_id", "client", "status", "attempt")
            }
            for task in plan.get("tasks", [])
        ],
    }


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep reportable aggregates while excluding local artifact paths."""
    return {
        key: value
        for key, value in result.items()
        if key
        in {
            "schema_version",
            "job_id",
            "profile",
            "verified",
            "provisional",
            "total_rows",
            "systems",
        }
    }


def _markdown(
    job: DashboardJob,
    plan: dict[str, Any],
    result: dict[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    status = "Doğrulandı" if result.get("verified") else "Geçici / Kısmi"
    lines = [
        "# MESA Benchmark Console Raporu",
        "",
        f"- **Çalışma:** {job.name}",
        f"- **Durum:** {status}",
        f"- **Profil:** {job.profile}",
        f"- **Oluşturma:** {job.created_at}",
        f"- **Rapor zamanı:** {datetime.now(timezone.utc).isoformat()}",
        f"- **Client sırası:** {', '.join(plan.get('clients', []))}",
        f"- **Shard:** {len(plan.get('shards', []))}",
        f"- **Sonuç satırı:** {len(rows)}",
        "",
        "> Bu rapor genel zekâ veya ürün üstünlüğü kanıtı değildir. "
        "Doğrulanmamış sonuçlar yalnız operasyonel gözlem amacı taşır.",
        "",
        "## Client karşılaştırması",
        "",
        "| Client | Soru | Valid | Hit@1 | Hit@5 | MRR | nDCG | Token F1 | Retrieval ms | Generation ms |",
        "|---|---:|:---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for client, metrics in result.get("systems", {}).items():
        percent = lambda key: (  # noqa: E731
            "N/A" if metrics.get(key) is None else f"%{float(metrics[key]) * 100:.2f}"
        )
        latency = lambda key: (  # noqa: E731
            "N/A" if metrics.get(key) is None else f"{float(metrics[key]):.2f}"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    client,
                    str(metrics.get("questions", 0)),
                    "Evet" if metrics.get("valid") else "Hayır",
                    percent("hit_at_1"),
                    percent("hit_at_5"),
                    percent("mrr"),
                    percent("ndcg"),
                    percent("token_f1"),
                    latency("avg_retrieval_ms"),
                    latency("avg_generation_ms"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Çalıştırma planı",
            "",
            f"- Shard modu: `{plan.get('shard_mode', 'limits')}`",
            f"- Aktif süre: `{job.active_elapsed_seconds:.1f}` saniye",
            f"- Süre limiti: `{job.time_limit_minutes or 'kapalı'}` dakika",
            f"- Verification: `{'geçti' if result.get('verified') else 'tamamlanmadı'}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_export(job: DashboardJob, plan: dict[str, Any], format_name: str) -> Path:
    if format_name not in {"md", "json", "csv"}:
        raise ValueError("Desteklenmeyen export formatı")
    export_root = Path(job.plan_path).parent / "exports"
    export_root.mkdir(exist_ok=True)
    result = (
        job.result
        or job.provisional_result
        or {
            "schema_version": 1,
            "job_id": job.id,
            "profile": job.profile,
            "verified": False,
            "provisional": True,
            "total_rows": 0,
            "systems": {},
        }
    )
    rows = _rows(plan)
    target = export_root / f"{_slug(job.name)}.{format_name}"
    if format_name == "md":
        target.write_text(_markdown(job, plan, result, rows) + "\n", encoding="utf-8")
    elif format_name == "json":
        target.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "verified" if result.get("verified") else "provisional",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "plan": _safe_plan(plan),
                    "result": _safe_result(result),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=CSV_FIELDS, extrasaction="ignore"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {field: _safe_cell(row.get(field)) for field in CSV_FIELDS}
                )
    return target
