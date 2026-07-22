"""Dataset synchronization, suite orchestration, and evidence-bundle verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..reports.statistics import holm_adjust, mcnemar_test, paired_bootstrap_ci
from .config import ClientConfig, load_config
from .paths import (
    REPOSITORY_ROOT,
    data_root,
    resolve_config_path,
    resolve_results_root,
    resource_root,
)
from .preflight import file_sha256, validate_config_and_dataset
from .runner import BenchmarkRunner

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SUITES_ROOT = resource_root() / "suites"


class SuiteRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    group: str
    config: str
    client_override: ClientConfig | None = None
    optional: bool = False


class SuiteDatasetCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    config: str
    profile: Literal["internal", "publishable"] = "internal"


class SuiteDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "1.0"
    name: str
    profile: Literal["internal", "publishable", "research"]
    sync: list[str] = Field(default_factory=list)
    dataset_checks: list[SuiteDatasetCheck] = Field(default_factory=list)
    runs: list[SuiteRun]
    required_systems: list[str] = Field(default_factory=list)
    group_required_systems: dict[str, list[str]] = Field(default_factory=dict)
    judge_calibration_path: str | None = None

    @model_validator(mode="after")
    def unique_run_ids(self) -> "SuiteDefinition":
        ids = [item.id for item in self.runs]
        if len(ids) != len(set(ids)):
            raise ValueError("suite run IDs must be unique")
        return self


def resolve_suite_path(value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.exists():
        return candidate
    named = SUITES_ROOT / f"{value}.yaml"
    if named.exists():
        return named
    raise ValueError(f"suite not found: {value}")


def load_suite(value: str | Path) -> tuple[Path, SuiteDefinition]:
    path = resolve_suite_path(value)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("suite YAML root must be an object")
    return path, SuiteDefinition.model_validate(raw)


def _script(name: str, *arguments: str) -> None:
    command = [
        sys.executable,
        "-m",
        f"mesa_benchmark.sync_tools.{Path(name).stem}",
        *arguments,
    ]
    subprocess.run(command, check=True)


def sync_suite(value: str | Path) -> dict[str, Any]:
    _, suite = load_suite(value)
    completed: list[str] = []
    for target in suite.sync:
        if target == "quality-synthetic":
            completed.append("quality-synthetic:packaged")
            continue
        elif target == "beam-128k":
            _script(
                "download_beam.py",
                "--output",
                str(data_root() / "external" / "beam" / "v2" / "dataset.json"),
                "--split",
                "100K",
            )
        elif target in {"beam-500k", "beam-1m"}:
            split = "500K" if target == "beam-500k" else "1M"
            filename = "500k.json" if split == "500K" else "1m.json"
            _script(
                "download_beam.py",
                "--output",
                str(data_root() / "generated" / "beam" / "scale" / filename),
                "--split",
                split,
            )
        elif target == "beam-10m-capacity-opt-in":
            if os.environ.get("MESA_BENCHMARK_ENABLE_10M") == "1":
                _script("generate_beam_capacity.py", "--target-tokens", "10000000")
            else:
                completed.append(f"{target}:skipped")
                continue
        elif target == "beam-512-64-ablation":
            _script(
                "generate_beam_chunk_ablation.py",
                "--chunk-size",
                "512",
                "--overlap",
                "64",
            )
        elif target == "longmemeval-s":
            _script("download_longmemeval.py")
        elif target == "memoryagentbench-core":
            _script("download_memoryagentbench.py", "--chunk-size", "512")
        elif target == "memoryagentbench-recsys":
            _script(
                "download_memoryagentbench.py",
                "--track",
                "recsys",
                "--chunk-size",
                "512",
            )
        elif target == "locomo":
            _script("download_locomo.py")
        else:
            raise ValueError(f"unknown dataset sync target: {target}")
        completed.append(target)
    result = check_suite(value)
    result["synced"] = completed
    return result


def _resolved_config_path(run: SuiteRun, directory: Path) -> Path:
    source = resolve_config_path(run.config)
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if run.client_override is not None:
        raw["client"] = run.client_override.model_dump(exclude_none=True)
    target = directory / f"{run.id}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return target


def check_suite(value: str | Path) -> dict[str, Any]:
    path, suite = load_suite(value)
    checked: list[dict[str, Any]] = []
    dataset_checks: list[dict[str, Any]] = []
    for item in suite.dataset_checks:
        config_path = resolve_config_path(item.config)
        dataset = validate_config_and_dataset(config_path, profile=item.profile)
        dataset_checks.append(
            {
                "id": item.id,
                "config": str(config_path),
                "dataset_sha256": dataset["dataset_sha256"],
                "questions": dataset["questions"],
                "ready": True,
            }
        )
    systems_by_group: dict[str, set[str]] = defaultdict(set)
    for run in suite.runs:
        config_path = resolve_config_path(run.config)
        config = load_config(config_path)
        client = run.client_override or config.client
        systems_by_group[run.group].add(client.name)
        if run.optional and run.client_override:
            referenced = set(
                re.findall(r"\$\{([^}]+)\}", run.client_override.model_dump_json())
            )
            missing_environment = sorted(
                variable for variable in referenced if not os.environ.get(variable)
            )
            if missing_environment:
                checked.append(
                    {
                        "id": run.id,
                        "optional": True,
                        "ready": False,
                        "reason": "missing optional environment variables: "
                        + ", ".join(missing_environment),
                    }
                )
                continue
        profile = "publishable" if suite.profile == "publishable" else "internal"
        try:
            dataset = validate_config_and_dataset(config_path, profile=profile)
        except Exception as exc:
            if run.optional:
                checked.append(
                    {"id": run.id, "optional": True, "ready": False, "reason": str(exc)}
                )
                continue
            raise
        checked.append(
            {
                "id": run.id,
                "group": run.group,
                "system": client.name,
                "dataset_sha256": dataset["dataset_sha256"],
                "questions": dataset["questions"],
                "ready": True,
            }
        )
    if suite.required_systems or suite.group_required_systems:
        for group, systems in systems_by_group.items():
            required = suite.group_required_systems.get(group, suite.required_systems)
            missing = set(required).difference(systems)
            if missing:
                raise ValueError(
                    f"suite group {group!r} is missing required systems: {sorted(missing)}"
                )
    return {
        "suite": suite.name,
        "suite_file": str(path),
        "profile": suite.profile,
        "dataset_checks": dataset_checks,
        "runs": checked,
        "ready": all(
            item["ready"]
            for item in [*dataset_checks, *checked]
            if not item.get("optional")
        ),
    }


def _code_sha256() -> str:
    digest = hashlib.sha256()
    roots = [PACKAGE_ROOT]
    for path in sorted(item for root in roots for item in root.rglob("*.py")):
        digest.update(str(path.relative_to(REPOSITORY_ROOT)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run_suite(
    value: str | Path, results_root: str | Path | None = None
) -> dict[str, Any]:
    suite_path, suite = load_suite(value)
    check_suite(value)
    bundle_id = str(uuid.uuid4())
    bundle_dir = resolve_results_root(results_root) / f"{suite.name}-{bundle_id}"
    bundle_root = bundle_dir.resolve()
    config_dir = bundle_dir / "resolved_configs"
    runs_dir = bundle_dir / "runs"
    config_dir.mkdir(parents=True, exist_ok=True)
    run_entries: list[dict[str, Any]] = []
    for run in suite.runs:
        config_path = _resolved_config_path(run, config_dir)
        try:
            outcome = BenchmarkRunner(config_path, results_root=runs_dir).run()
        except Exception:
            if run.optional:
                continue
            raise
        result_path = Path(outcome["results_file"])
        manifest_path = result_path.parent / f"manifest_{outcome['run_id']}.json"
        config = load_config(config_path)
        run_entries.append(
            {
                "id": run.id,
                "group": run.group,
                "system": config.client.name,
                "config": str(config_path.resolve().relative_to(bundle_root)),
                "config_file_sha256": file_sha256(config_path),
                "results": str(result_path.resolve().relative_to(bundle_root)),
                "manifest": str(manifest_path.resolve().relative_to(bundle_root)),
                "metrics": outcome["metrics"],
            }
        )
    bundle = {
        "schema_version": 3,
        "bundle_id": bundle_id,
        "suite": suite.name,
        "suite_sha256": file_sha256(suite_path),
        "profile": suite.profile,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_sha256": _code_sha256(),
        "required_systems": suite.required_systems,
        "judge_calibration_path": suite.judge_calibration_path
        or os.environ.get("BENCHMARK_JUDGE_CALIBRATION_PATH"),
        "runs": run_entries,
    }
    bundle_path = bundle_dir / "bundle.json"
    bundle_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    verification = verify_results(bundle_path)
    return {"bundle": str(bundle_path), "verification": verification}


def _bundle_member(bundle_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else bundle_path.parent / path


def _rows(path: Path) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = f"{row['iteration']}:{row['scenario_id']}:{row['question_id']}"
        values[key] = row
    return values


def verify_results(value: str | Path) -> dict[str, Any]:
    bundle_path = Path(value)
    if bundle_path.is_dir():
        bundle_path = bundle_path / "bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if bundle.get("schema_version") != 3:
        raise ValueError("result bundle must use schema version 3")
    errors: list[str] = []
    comparisons: dict[str, Any] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in bundle.get("runs", []):
        manifest_path = _bundle_member(bundle_path, run["manifest"])
        results_path = _bundle_member(bundle_path, run["results"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = _rows(results_path)
        if not rows:
            errors.append(f"{run['id']}: no result rows")
        if any(row.get("infrastructure_error") for row in rows.values()):
            errors.append(f"{run['id']}: infrastructure error present")
        if any(row.get("schema_version") != 3 for row in rows.values()):
            errors.append(f"{run['id']}: non-v3 result row")
        expected_questions = (manifest.get("dataset_counts") or {}).get("questions")
        expected_rows = (
            int(expected_questions) * int(manifest.get("iterations", 1))
            if expected_questions is not None
            else None
        )
        if expected_rows is not None and len(rows) != expected_rows:
            errors.append(
                f"{run['id']}: incomplete question coverage "
                f"({len(rows)}/{expected_rows})"
            )
        grouped[run["group"]].append({"run": run, "manifest": manifest, "rows": rows})
    for group, entries in grouped.items():
        reference = entries[0]
        reference_keys = set(reference["rows"])
        parity_fields = (
            "input_context_ids",
            "chunk_hashes",
            "top_k",
            "secondary_top_k_sweep",
            "context_token_budget",
        )
        for candidate in entries[1:]:
            if set(candidate["rows"]) != reference_keys:
                errors.append(f"{group}: question keys differ across systems")
                continue
            for key in sorted(reference_keys):
                for field in parity_fields:
                    if candidate["rows"][key].get(field) != reference["rows"][key].get(
                        field
                    ):
                        errors.append(
                            f"{group}:{key}: adapter parity mismatch for {field}"
                        )
                        break
            for field in (
                "dataset_sha256",
                "embedding_model",
                "generator_model",
                "judge_model",
                "multi_judge_models",
                "top_k",
                "secondary_top_k_sweep",
                "track",
                "context_token_budget",
                "chunking",
                "isolation",
                "ingest_mode",
            ):
                if candidate["manifest"].get(field) != reference["manifest"].get(field):
                    errors.append(f"{group}: manifest mismatch for {field}")
            keys = sorted(reference_keys)
            scores_a = [float(reference["rows"][key]["score"]) for key in keys]
            scores_b = [float(candidate["rows"][key]["score"]) for key in keys]
            binary_a = [bool(reference["rows"][key]["is_correct"]) for key in keys]
            binary_b = [bool(candidate["rows"][key]["is_correct"]) for key in keys]
            name = f"{reference['run']['system']}_vs_{candidate['run']['system']}"
            comparisons[f"{group}:{name}"] = {
                "paired_bootstrap": paired_bootstrap_ci(scores_a, scores_b),
                "mcnemar": mcnemar_test(binary_a, binary_b),
            }
    p_values = {
        name: float(value["mcnemar"]["p_value"]) for name, value in comparisons.items()
    }
    adjusted = holm_adjust(p_values)
    for name, adjusted_value in adjusted.items():
        comparisons[name]["holm_adjusted_mcnemar_p"] = adjusted_value

    publishable = bundle.get("profile") == "publishable"
    if publishable:
        for group, entries in grouped.items():
            systems = {entry["run"]["system"] for entry in entries}
            if len(systems) < 3:
                errors.append(
                    f"{group}: publishable comparison needs at least three systems"
                )
            for entry in entries:
                manifest = entry["manifest"]
                if manifest.get("dataset_designation") != "external-publishable":
                    errors.append(
                        f"{entry['run']['id']}: dataset is not external-publishable"
                    )
                license_info = manifest.get("dataset_license") or {}
                if (
                    not license_info.get("spdx_id")
                    or license_info.get("redistribution") != "allowed"
                ):
                    errors.append(
                        f"{entry['run']['id']}: license/provenance gate failed"
                    )
                if not all(
                    row.get("judge_quorum_met") is True
                    for row in entry["rows"].values()
                ):
                    errors.append(f"{entry['run']['id']}: judge quorum was not met")
                if manifest.get("evidence_tier") != "publishable" or int(
                    manifest.get("judge_evaluations", 0)
                ) < len(entry["rows"]):
                    errors.append(
                        f"{entry['run']['id']}: independent judge did not evaluate "
                        "every question"
                    )
                generator = str(manifest.get("generator_model") or "").removeprefix(
                    "openai/"
                )
                judges = {
                    str(item).removeprefix("openai/")
                    for item in [
                        manifest.get("judge_model"),
                        *(manifest.get("multi_judge_models") or []),
                    ]
                    if item
                }
                if not generator or not any(item != generator for item in judges):
                    errors.append(
                        f"{entry['run']['id']}: independent judge policy failed"
                    )
        calibration_path = bundle.get("judge_calibration_path")
        if not calibration_path:
            errors.append("publishable bundle requires judge calibration")
        else:
            calibration = json.loads(
                _bundle_member(bundle_path, calibration_path).read_text(
                    encoding="utf-8"
                )
            )
            category_counts = calibration.get("category_counts") or {}
            if (
                calibration.get("sample_size", 0) < 100
                or calibration.get("cohens_kappa", 0) < 0.70
                or len(category_counts) < 5
                or min(category_counts.values(), default=0) < 10
            ):
                errors.append(
                    "judge calibration requires n>=100 and Cohen's kappa>=0.70"
                )
    result = {
        "bundle": str(bundle_path),
        "protocol_valid": not errors,
        "publishable": publishable and not errors,
        "errors": errors,
        "comparisons": comparisons,
    }
    verification_path = bundle_path.parent / "verification.json"
    verification_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if errors:
        raise ValueError("result bundle verification failed: " + "; ".join(errors[:10]))
    return result
