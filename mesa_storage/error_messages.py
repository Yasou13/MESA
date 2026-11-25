"""Single-source failure-class to plain-English message map for document status (D96)."""

from __future__ import annotations

import re

_SAFE_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

DEFAULT_FAILURE_CODE = "processing_failed"
DEFAULT_FAILURE_MESSAGE = (
    "Document processing failed. Retry the upload or check the server status."
)

FAILURE_CLASS_MESSAGES: dict[str, str] = {
    # Ingestion and extraction failures
    "Tier3Unavailable": (
        "Document processing failed because the configured model backend was unavailable."
    ),
    "FactExtractionError": (
        "Fact extraction failed while processing a chunk. Check the configured LLM model."
    ),
    "FactExtractionUnavailableError": (
        "Fact extraction is temporarily unavailable. Check the configured LLM provider."
    ),
    "PromptInjectionError": (
        "A chunk was rejected by input validation because it matched a blocked pattern."
    ),
    "TokenBudgetExceededError": (
        "A chunk exceeded the configured model token budget."
    ),
    # Projection and vector/embedding failures
    "PermanentProjectionError": (
        "Indexing failed permanently for one or more chunks because the data could not be projected."
    ),
    "ProjectionParityMismatch": (
        "Indexed storage did not match canonical records. Retry or rebuild the index."
    ),
    "ProjectionParityError": (
        "Indexed storage did not match canonical records. Retry or rebuild the index."
    ),
    "ProjectionOwnershipError": (
        "Index ownership verification failed during projection."
    ),
    "PhysicalCompensationPending": (
        "Previous index cleanup is still pending before this revision can finish."
    ),
    "EmbeddingError": (
        "Vector indexing failed while generating embeddings for a chunk."
    ),
    "EmbeddingGenerationError": (
        "Vector indexing failed while generating embeddings. Check the embedding model."
    ),
    "EmbeddingUnavailableError": (
        "The embedding service is unavailable. Verify the embedding server is running."
    ),
    "EmbeddingCompositionError": (
        "The embedding service could not be initialized with the current configuration."
    ),
    "EmbeddingIdentityMismatchError": (
        "The configured embedding model does not match this store's existing vector index."
    ),
    "EmbeddingMigrationRequiredError": (
        "The vector index requires a rebuild to match the configured embedding dimension."
    ),
    "EmbeddingIdentityAdoptionError": (
        "The store could not adopt the configured embedding model identity."
    ),
    "ExternalProviderForbiddenError": (
        "External model providers are disabled by configuration."
    ),
    "VectorSearchError": (
        "The vector index encountered an error during indexing or verification."
    ),
    "SemanticRuntimeDisabledError": (
        "Vector indexing is disabled in the current runtime configuration."
    ),
    # Queue, storage, session, and maintenance failures
    "QueueOverCapacityError": (
        "The ingestion queue was full. Retry the upload after pending work drains."
    ),
    "QueueRecordTooLargeError": (
        "A chunk exceeded the durable queue payload size limit."
    ),
    "QueueUnavailableError": (
        "The durable ingestion queue is temporarily unavailable."
    ),
    "StorageWriterLockError": (
        "Another process holds the storage writer lock."
    ),
    "IncompleteSessionWork": (
        "Pending work did not finish before the session closed."
    ),
    "FeatureDisabled": (
        "This operation is disabled in the current server configuration."
    ),
    "InvalidBounds": (
        "The operation parameters were outside supported bounds."
    ),
    "WriterActive": (
        "Another write or maintenance operation is currently active."
    ),
    "RebuildInterrupted": (
        "An index rebuild was interrupted before completion."
    ),
    "RollbackVerificationFailed": (
        "Rollback verification failed; inspect server status before retrying."
    ),
    "PostCutoverVerificationFailed": (
        "Post-rebuild verification failed; inspect server status before retrying."
    ),
    "cleanup_failed": (
        "Artifact cleanup failed after a previous rollback or purge."
    ),
    # Generic runtime / IO failure classes recorded via type(exc).__name__
    "TimeoutError": (
        "Processing timed out waiting for a storage or model backend."
    ),
    "ConnectionError": (
        "Could not connect to a configured model or storage backend."
    ),
    "OSError": (
        "A local disk or operating-system I/O error interrupted processing."
    ),
    "OperationalError": (
        "A database error interrupted document indexing. Retry the upload."
    ),
    "ValueError": (
        "Document processing failed due to invalid chunk or metadata values."
    ),
    "RuntimeError": (
        "An unexpected runtime error interrupted document processing."
    ),
    # Dispatch queue terminal outcomes
    "DEFERRED": (
        "Processing did not finish after repeated attempts. Retry the upload."
    ),
    "pending": (
        "Processing did not start after repeated attempts. Check worker health."
    ),
    "processing": (
        "Processing was interrupted repeatedly before completing."
    ),
    "failed": (
        "Document processing failed after repeated attempts. Retry the upload."
    ),
    DEFAULT_FAILURE_CODE: DEFAULT_FAILURE_MESSAGE,
}


def format_failure_error(failure_class: str | None) -> dict[str, str]:
    """Map an internal failure class to a safe, plain-English {code, message} dict."""
    raw = (failure_class or "").strip()
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    if not raw or not _SAFE_CODE_RE.fullmatch(raw):
        code = DEFAULT_FAILURE_CODE
    else:
        code = raw
    message = FAILURE_CLASS_MESSAGES.get(code, DEFAULT_FAILURE_MESSAGE)
    return {"code": code, "message": message}
