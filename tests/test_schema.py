import pytest
from pydantic import ValidationError

from mesa_memory.schema.cmb import CMB, AffectiveState, ResourceCost, generate_uuid7


def test_uuid7_generation():
    uid = generate_uuid7()
    assert isinstance(uid, str)
    assert len(uid) > 0


def test_cmb_valid_instantiation():
    cmb = CMB(
        content_payload="Test memory content",
        source="user",
        performative="assert",
        cat7_focus=0.8,
        cat7_mood=AffectiveState(valence=0.5, arousal=0.3),
        resource_cost=ResourceCost(token_count=100, latency_ms=45.0),
    )
    assert cmb.cmb_id is not None
    assert len(cmb.cmb_id) > 0
    assert cmb.created_at is not None


def test_cmb_validation_errors():
    with pytest.raises(ValidationError):
        CMB(
            content_payload="",
            source="user",
            performative="assert",
            resource_cost=ResourceCost(token_count=10, latency_ms=5.0),
        )

    with pytest.raises(ValidationError):
        AffectiveState(valence=1.5, arousal=0.5)

    with pytest.raises(ValidationError):
        ResourceCost(token_count=10, latency_ms=-1.0)
