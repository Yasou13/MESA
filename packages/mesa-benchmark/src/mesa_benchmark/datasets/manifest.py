"""Typed dataset provenance and quality contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DatasetDesignation = Literal[
    "internal-regression", "external-research", "external-publishable"
]
IsolationMode = Literal["scenario", "cumulative"]
IngestMode = Literal["batch", "sequential"]


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str
    revision: str
    split: str


class ChecksumSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    raw_sha256: str | None = None
    converted_sha256: str


class LicenseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    spdx_id: str
    redistribution: Literal["allowed", "restricted", "prohibited"]


class ChunkingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class MetricSupport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    supported: list[str]
    unsupported: list[str] = Field(default_factory=list)


class DatasetCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scenarios: int = Field(ge=0)
    contexts: int = Field(ge=0)
    questions: int = Field(ge=0)
    categories: dict[str, int] = Field(default_factory=dict)


class ConverterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class QualitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    normalized_duplicate_query_budget: float = Field(1.0, ge=0.0, le=1.0)


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str
    dataset_name: str
    dataset_version: str
    source: SourceSpec
    checksums: ChecksumSpec
    license: LicenseSpec
    designation: DatasetDesignation
    isolation: IsolationMode
    ingest_mode: IngestMode
    chunking: ChunkingSpec
    metrics: MetricSupport
    counts: DatasetCounts
    converter: ConverterSpec
    quality: QualitySpec = Field(default_factory=QualitySpec)
    known_annotation_exceptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_external_provenance(self) -> "DatasetManifest":
        if self.designation.startswith("external-"):
            if not self.source.url.startswith(("https://", "http://")):
                raise ValueError("external datasets require an HTTP(S) source URL")
            if not self.source.revision.strip():
                raise ValueError("external datasets require a pinned revision")
            if not self.checksums.raw_sha256:
                raise ValueError("external datasets require raw_sha256")
        return self


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_dataset_manifest(path: str | Path) -> DatasetManifest:
    manifest_path = Path(path)
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"dataset manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid dataset manifest JSON: {exc}") from exc
    return DatasetManifest.model_validate(value)


def validate_dataset_manifest(
    manifest: DatasetManifest, dataset_path: str | Path, *, profile: str
) -> dict[str, Any]:
    path = Path(dataset_path)
    if not path.exists():
        raise ValueError(f"dataset file not found: {path}")
    actual = sha256_file(path)
    if actual != manifest.checksums.converted_sha256:
        raise ValueError(
            "converted_sha256 mismatch: "
            f"expected={manifest.checksums.converted_sha256} actual={actual}"
        )
    if profile not in {"internal", "publishable"}:
        raise ValueError("profile must be internal or publishable")
    if profile == "publishable":
        if manifest.designation != "external-publishable":
            raise ValueError(
                f"dataset designation {manifest.designation!r} is not publishable"
            )
        if not manifest.license.spdx_id.strip():
            raise ValueError("publishable profile requires an SPDX license")
        if manifest.license.redistribution != "allowed":
            raise ValueError("publishable profile requires allowed redistribution")
        if not manifest.checksums.raw_sha256:
            raise ValueError("publishable profile requires raw_sha256")
    return {
        "manifest_schema": manifest.schema_version,
        "designation": manifest.designation,
        "license": manifest.license.spdx_id,
        "converted_sha256": actual,
        "profile": profile,
    }
