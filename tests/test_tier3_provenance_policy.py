from __future__ import annotations

from mesa_memory.consolidation.loop import _requires_provenance_dual_review
from mesa_storage.dao import _public_tier3_audit


def _audit() -> dict:
    return {
        "route": "dual_llm",
        "route_reason": "provenance_dual_review",
        "decisions": {"primary": "STORE", "secondary": "DISCARD"},
        "justifications": {"primary": "first", "secondary": "second"},
        "models": {"primary": "model-a", "secondary": "model-b"},
        "prompt_version": "tier3-valence-v2",
        "reason": "dual_llm_disagreement",
        "accepted": False,
    }


def test_source_backed_technical_memory_requires_dual_review() -> None:
    record = {
        "source_ref": "decision://AD-1",
        "evidence_span": "section=1",
        "metadata": {"memory_type": "architecture", "importance": 0.8},
    }

    assert _requires_provenance_dual_review(record) is True
    assert _requires_provenance_dual_review({**record, "evidence_span": ""}) is False
    assert _requires_provenance_dual_review(
        {**record, "source_ref": "mcp_tool"}
    ) is False
    assert _requires_provenance_dual_review(
        {**record, "metadata": {"memory_type": "fact", "importance": 0.8}}
    ) is False
    assert _requires_provenance_dual_review(
        {**record, "metadata": {"memory_type": "architecture", "importance": True}}
    ) is False


def test_public_tier3_audit_whitelists_only_safe_receipts() -> None:
    public = _public_tier3_audit(_audit())

    assert public is not None
    assert public["decisions"] == {"primary": "STORE", "secondary": "DISCARD"}
    assert public["reason"] == "dual_llm_disagreement"
    assert _public_tier3_audit(None) is None
    assert _public_tier3_audit({"decisions": {}}) is None
    assert _public_tier3_audit({**_audit(), "decisions": {"primary": "MAYBE", "secondary": "STORE"}}) is None
