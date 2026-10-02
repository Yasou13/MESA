"""Version identities and builders for durable V4 representations."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

V4_ASSERTION_REPRESENTATION_VERSION = "assertion-v1"
V4_VECTOR_REPRESENTATION_VERSION = "assertion-semantic-v1"


@dataclass(frozen=True)
class V4VectorConsumerContract:
    """Canonical compatibility contract for V4 assertion vectors.

    ``representation_version`` identifies the assertion payload construction
    and document/query role split.  The embedding-space fields fence that
    representation to the exact producer identity used by the active query
    embedder.  Both parts are required: equal dimensions alone do not make two
    embedding spaces compatible.
    """

    representation_version: str = V4_VECTOR_REPRESENTATION_VERSION
    artifact_kind: str = "ASSERTION_VECTOR"
    document_embedding_role: str = "document"
    query_embedding_role: str = "query"

    @staticmethod
    def _identity_metadata(identity: Any) -> dict[str, Any] | None:
        if identity is None:
            return None
        as_dict = getattr(identity, "as_dict", None)
        if not callable(as_dict) or inspect.iscoroutinefunction(as_dict):
            return None
        value = as_dict()
        if not isinstance(value, Mapping):
            return None
        required = (
            "embedding_space_id",
            "provider",
            "model",
            "model_revision",
            "version",
            "dimension",
            "normalized",
        )
        if not all(field in value for field in required):
            return None
        return {key: value[key] for key in required}

    def compatibility_errors(
        self, metadata: Mapping[str, Any], *, embedding_identity: Any
    ) -> tuple[str, ...]:
        """Return stable fail-closed reasons for one registry artifact."""
        errors: list[str] = []
        if metadata.get("representation_version") != self.representation_version:
            errors.append("representation_version")

        expected = self._identity_metadata(embedding_identity)
        if expected is None:
            errors.append("runtime_embedding_identity")
            return tuple(errors)

        for field, expected_value in expected.items():
            if metadata.get(field) != expected_value:
                errors.append(field)
        if metadata.get("embedding_dimension") != expected["dimension"]:
            errors.append("embedding_dimension")
        return tuple(errors)

    def accepts(self, metadata: Mapping[str, Any], *, embedding_identity: Any) -> bool:
        """Return whether an artifact is safe for the active vector consumer."""
        return not self.compatibility_errors(
            metadata, embedding_identity=embedding_identity
        )


V4_VECTOR_CONSUMER_CONTRACT = V4VectorConsumerContract()


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
