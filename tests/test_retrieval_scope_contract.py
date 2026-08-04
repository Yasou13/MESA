"""Shared vector retrieval scoping used by serving and rebuild verification."""

from mesa_storage.retrieval_scope import scope_vector_result_ids


def test_vector_result_scope_excludes_cross_dataset_rows_and_preserves_rank() -> None:
    rows = [
        {"node_id": "dataset-b-nearest", "_distance": 0.01},
        {"node_id": "dataset-a-first", "_distance": 0.02},
        {"node_id": "dataset-a-second", "_distance": 0.03},
        {"node_id": "dataset-a-first", "_distance": 0.04},
    ]

    assert scope_vector_result_ids(
        rows, allowed_ids={"dataset-a-first", "dataset-a-second"}
    ) == ["dataset-a-first", "dataset-a-second"]
