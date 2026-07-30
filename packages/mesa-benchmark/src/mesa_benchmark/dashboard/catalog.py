from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import yaml

from ..core.paths import resolve_benchmark_path, resolve_config_path
from ..datasets.manifest import load_dataset_manifest, sha256_file

DATASETS: tuple[dict[str, Any], ...] = (
    {
        "id": "smoke",
        "name": "Mini Smoke",
        "group": "internal",
        "config": "resource://configs/internal/smoke_dense.yaml",
        "purpose": "Sistemin uçtan uca çalıştığını hızlı doğrular; kalite sıralaması değildir.",
        "recommended_profiles": ["quality"],
        "sync_target": None,
    },
    {
        "id": "contradiction",
        "name": "Contradiction v3",
        "group": "internal",
        "config": "resource://configs/internal/contradiction_v3.yaml",
        "purpose": "Güncellenen ve çelişen bilgilerde doğru ve son bilgiyi bulmayı ölçer.",
        "recommended_profiles": ["quality", "native"],
        "sync_target": None,
    },
    {
        "id": "holdout",
        "name": "Internal Holdout 600",
        "group": "internal",
        "config": "resource://configs/internal/holdout_600.yaml",
        "purpose": "Recall, abstention, multi-hop, gürültü, tercih ve contradiction regresyonudur.",
        "recommended_profiles": ["quality"],
        "sync_target": None,
    },
    {
        "id": "multi-hop",
        "name": "Multi-hop Raw",
        "group": "internal",
        "config": "resource://configs/internal/multi_hop_raw.yaml",
        "purpose": "Client'ları ortak ham metinde adil multi-hop retrieval ile karşılaştırır.",
        "recommended_profiles": ["quality", "native"],
        "sync_target": None,
    },
    {
        "id": "multi-hop-graph",
        "name": "Multi-hop Graph Ablation",
        "group": "research",
        "config": "resource://configs/internal/multi_hop_graph.yaml",
        "purpose": "MESA graph multi-hop katkısını raw-text kontrolüne karşı inceler.",
        "recommended_profiles": ["native"],
        "sync_target": None,
    },
    {
        "id": "beam",
        "name": "BEAM 128K",
        "group": "release",
        "config": "resource://configs/release/beam_128k.yaml",
        "purpose": "Uzun bağlam, temporal reasoning, preference, update ve multi-session görevlerini ölçer.",
        "recommended_profiles": ["quality", "native"],
        "sync_target": "beam-128k",
        "estimated_download": "BEAM 100K split",
    },
    {
        "id": "longmemeval",
        "name": "LongMemEval S",
        "group": "release",
        "config": "resource://configs/release/longmemeval.yaml",
        "purpose": "Uzun dönem ve çok oturumlu hafıza, temporal ve update başarısını ölçer.",
        "recommended_profiles": ["quality", "native"],
        "sync_target": "longmemeval-s",
        "estimated_download": "LongMemEval_S cleaned",
    },
    {
        "id": "memoryagentbench",
        "name": "MemoryAgentBench Core",
        "group": "release",
        "config": "resource://configs/release/memoryagentbench.yaml",
        "purpose": "Retrieval, conflict resolution, long-range understanding ve test-time learning ölçer.",
        "recommended_profiles": ["quality", "native"],
        "sync_target": "memoryagentbench-core",
        "estimated_download": "MemoryAgentBench core",
    },
    {
        "id": "locomo",
        "name": "LoCoMo",
        "group": "research",
        "config": "resource://configs/research/locomo.yaml",
        "purpose": "Uzun konuşmalarda çok oturumlu hafıza ve reasoning araştırmasıdır.",
        "recommended_profiles": ["quality"],
        "sync_target": "locomo",
        "estimated_download": "LoCoMo research dataset",
    },
    {
        "id": "beam-500k",
        "name": "BEAM 500K",
        "group": "research",
        "config": "resource://configs/research/beam_500k.yaml",
        "purpose": "Büyük bağlamda ölçek ve kapasite davranışını inceler.",
        "recommended_profiles": ["capacity"],
        "sync_target": "beam-500k",
        "estimated_download": "BEAM 500K generated split",
    },
    {
        "id": "beam-1m",
        "name": "BEAM 1M",
        "group": "research",
        "config": "resource://configs/research/beam_1m.yaml",
        "purpose": "Milyon token düzeyinde kapasite ve retrieval ölçeklenmesini ölçer.",
        "recommended_profiles": ["capacity"],
        "sync_target": "beam-1m",
        "estimated_download": "BEAM 1M generated split",
    },
    {
        "id": "memoryagentbench-recsys",
        "name": "MemoryAgentBench Recsys",
        "group": "research",
        "config": "resource://configs/research/memoryagentbench_recsys.yaml",
        "purpose": "Öneri görevlerinde item-ID Recall@5 ikincil araştırma track'idir.",
        "recommended_profiles": ["quality"],
        "sync_target": "memoryagentbench-recsys",
        "estimated_download": "MemoryAgentBench recsys",
    },
)

CLIENTS: dict[str, dict[str, Any]] = {
    "mesa": {
        "name": "MESA",
        "adapter_class": "mesa_benchmark.clients.mesa_client.MesaV4ClientAdapter",
        "module": "mesa_memory",
        "parameters": {
            "enable_multi_hop": False,
            "enable_rerank": False,
            "embedding_model": "all-MiniLM-L6-v2",
        },
    },
    "dense-rag": {
        "name": "Dense RAG",
        "adapter_class": "mesa_benchmark.clients.dense_rag_client.DenseRagClientAdapter",
        "module": "sentence_transformers",
        "parameters": {
            "embedding_backend": "sentence-transformers",
            "embedding_model": "all-MiniLM-L6-v2",
        },
    },
    "mem0": {
        "name": "Mem0",
        "adapter_class": "mesa_benchmark.clients.mem0_client.Mem0ClientAdapter",
        "module": "mem0",
        "parameters": {"embedding_model": "all-MiniLM-L6-v2"},
    },
    "letta": {
        "name": "Letta",
        "adapter_class": "mesa_benchmark.clients.letta_client.LettaClientAdapter",
        "module": "letta_client",
        "parameters": {
            "base_url": "${LETTA_BASE_URL}",
            "agent_model": "${LETTA_AGENT_MODEL}",
            "embedding_model": "${LETTA_EMBEDDING_MODEL}",
            "letta_embedding_model": "${LETTA_EMBEDDING_MODEL}",
        },
        "environment": (
            "LETTA_BASE_URL",
            "LETTA_AGENT_MODEL",
            "LETTA_EMBEDDING_MODEL",
        ),
    },
    "zep": {
        "name": "Zep",
        "adapter_class": "mesa_benchmark.clients.zep_client.ZepClientAdapter",
        "module": "zep_cloud",
        "parameters": {
            "api_key": "${ZEP_API_KEY}",
            "embedding_model": "${ZEP_EMBEDDING_MODEL}",
        },
        "environment": ("ZEP_API_KEY", "ZEP_EMBEDDING_MODEL"),
    },
}


def client_catalog() -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for client_id, spec in CLIENTS.items():
        reasons: list[str] = []
        if importlib.util.find_spec(spec["module"]) is None:
            reasons.append("gerekli Python paketi kurulu değil")
        missing = [
            name for name in spec.get("environment", ()) if not os.environ.get(name)
        ]
        if missing:
            reasons.append("eksik ortam değişkenleri: " + ", ".join(missing))
        values.append(
            {
                "id": client_id,
                "name": spec["name"],
                "available": not reasons,
                "reason": "; ".join(reasons) if reasons else None,
                "quality_mode": True,
                "native_mode": client_id in {"mesa", "mem0", "letta", "zep"},
            }
        )
    return values


def dataset_spec(dataset_id: str) -> dict[str, Any]:
    for item in DATASETS:
        if item["id"] == dataset_id:
            return item
    raise KeyError(dataset_id)


def _resolved_dataset(spec: dict[str, Any]) -> tuple[dict[str, Any], Path, Path]:
    config_path = resolve_config_path(spec["config"])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    dataset_path = resolve_benchmark_path(
        config["dataset"]["path"], base_dir=config_path.parent
    )
    manifest_path = resolve_benchmark_path(
        config["dataset"]["manifest_path"], base_dir=config_path.parent
    )
    return config, dataset_path, manifest_path


def dataset_catalog() -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for spec in DATASETS:
        config, dataset_path, manifest_path = _resolved_dataset(spec)
        manifest = load_dataset_manifest(manifest_path)
        ready = dataset_path.is_file()
        checksum_valid = (
            sha256_file(dataset_path) == manifest.checksums.converted_sha256
            if ready
            else False
        )
        values.append(
            {
                **spec,
                "version": config["dataset"]["version"],
                "ready": ready and checksum_valid,
                "file_present": ready,
                "checksum_valid": checksum_valid,
                "file_size_bytes": dataset_path.stat().st_size if ready else None,
                "license": manifest.license.spdx_id,
                "redistribution": manifest.license.redistribution,
                "designation": manifest.designation,
                "source": manifest.source.model_dump(),
                "checksum": manifest.checksums.converted_sha256,
                "counts": manifest.counts.model_dump(),
                "categories": manifest.counts.categories,
                "isolation": manifest.isolation,
                "ingest_mode": manifest.ingest_mode,
                "metrics": manifest.metrics.model_dump(),
            }
        )
    return values


def dataset_detail(dataset_id: str) -> dict[str, Any]:
    return next(item for item in dataset_catalog() if item["id"] == dataset_id)


def dataset_scenarios(
    dataset_id: str, *, offset: int = 0, limit: int = 10
) -> dict[str, Any]:
    spec = dataset_spec(dataset_id)
    _, dataset_path, _ = _resolved_dataset(spec)
    if not dataset_path.is_file():
        raise FileNotFoundError("Dataset henüz indirilmemiş veya üretilmemiş")
    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    scenarios = raw if isinstance(raw, list) else raw.get("scenarios", [])
    selected = scenarios[offset : offset + limit]
    items: list[dict[str, Any]] = []
    for scenario in selected:
        items.append(
            {
                "id": scenario.get("id"),
                "contexts": [
                    {
                        "id": item.get("id"),
                        "text": str(item.get("text", ""))[:4_000],
                    }
                    for item in scenario.get("contexts", [])
                ],
                "questions": [
                    {
                        "id": item.get("id"),
                        "query": item.get("query"),
                        "ground_truth": item.get("ground_truth"),
                        "reference_answers": item.get("reference_answers", []),
                        "supporting_context_ids": item.get(
                            "supporting_context_ids",
                            item.get("expected_context_ids", []),
                        ),
                        "category": item.get("category"),
                    }
                    for item in scenario.get("questions", [])
                ],
            }
        )
    return {"total": len(scenarios), "offset": offset, "limit": limit, "items": items}
