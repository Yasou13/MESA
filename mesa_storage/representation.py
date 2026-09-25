"""Version identities and builders for durable V4 representations."""

V4_ASSERTION_REPRESENTATION_VERSION = "assertion-v1"
V4_VECTOR_REPRESENTATION_VERSION = "assertion-semantic-v1"


def build_v4_assertion_vector_payload(
    *, subject: str, predicate: str, object_value: str, evidence_span: str
) -> str:
    """Build the replayable semantic text exclusively from canonical fields."""
    prefix = " ".join(
        value
        for value in (
            subject.strip(),
            predicate.strip(),
            object_value.strip(),
        )
        if value and not value.startswith("mesa-")
    )
    evidence = evidence_span.strip()
    if evidence and prefix:
        return f"{prefix}: {evidence}"
    return evidence or prefix
