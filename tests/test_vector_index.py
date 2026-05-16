from unittest.mock import MagicMock, patch

import pytest

from mesa_memory.storage.vector_index import VectorStorage


def test_get_or_create_table():
    mock_db = MagicMock()
    with patch(
        "mesa_memory.storage.vector_index.lancedb.connect", return_value=mock_db
    ):
        vs = VectorStorage()
        mock_db.open_table.side_effect = FileNotFoundError()
        vs.get_or_create_table(128)
        mock_db.create_table.assert_called_once()
        assert "mesa_memory_128" in vs._tables


def test_upsert_fallback():
    mock_db = MagicMock()
    with patch(
        "mesa_memory.storage.vector_index.lancedb.connect", return_value=mock_db
    ):
        vs = VectorStorage()
        mock_table = MagicMock()
        mock_db.open_table.return_value = mock_table

        # raise RuntimeError on merge_insert to trigger table.add fallback
        mock_table.merge_insert.return_value.when_matched_update_all.return_value.when_not_matched_insert_all.return_value.execute.side_effect = RuntimeError(
            "Lance error"
        )

        with patch.object(vs, "_check_memory_limit"):
            vs.upsert_vector("cmb1", [0.1, 0.2])
        mock_table.add.assert_called_once()


def test_memory_limit():
    with patch("mesa_memory.storage.vector_index.lancedb.connect"):
        with patch("mesa_memory.storage.vector_index.psutil.virtual_memory") as mock_vm:
            mock_vm.return_value = MagicMock(total=1000, available=0)  # 1000 used
            with patch(
                "mesa_memory.storage.vector_index.config.lancedb_memory_limit_bytes",
                500,
            ):
                vs = VectorStorage()
                with pytest.raises(MemoryError):
                    vs.upsert_vector("cmb1", [0.1])


def test_search_isolation():
    mock_db = MagicMock()
    with patch(
        "mesa_memory.storage.vector_index.lancedb.connect", return_value=mock_db
    ):
        vs = VectorStorage()
        mock_table2 = MagicMock()
        mock_table3 = MagicMock()

        def mock_open_table(name):
            if name == "mesa_memory_2":
                return mock_table2
            if name == "mesa_memory_3":
                return mock_table3
            raise FileNotFoundError()

        mock_db.open_table.side_effect = mock_open_table

        vs.search([0.1, 0.2])
        mock_table2.search.assert_called_once()
        mock_table3.search.assert_not_called()


def test_soft_delete():
    mock_db = MagicMock()
    with patch(
        "mesa_memory.storage.vector_index.lancedb.connect", return_value=mock_db
    ):
        vs = VectorStorage()
        vs.db.list_tables.return_value = ["mesa_memory_2", "other_table"]
        mock_table = MagicMock()
        mock_db.open_table.return_value = mock_table

        vs.soft_delete("cmb1")
        mock_table.update.assert_called_once()


def test_get_all_cmb_ids():
    mock_db = MagicMock()
    with patch(
        "mesa_memory.storage.vector_index.lancedb.connect", return_value=mock_db
    ):
        vs = VectorStorage()
        vs.db.list_tables.return_value = ["mesa_memory_2"]
        mock_table = MagicMock()
        mock_db.open_table.return_value = mock_table

        mock_arrow = MagicMock()
        mock_arrow.column.return_value.to_pylist.return_value = ["cmb1", "cmb2"]
        mock_table.to_arrow.return_value = mock_arrow

        assert vs.get_all_cmb_ids() == {"cmb1", "cmb2"}


def test_get_all_embeddings():
    mock_db = MagicMock()
    with patch(
        "mesa_memory.storage.vector_index.lancedb.connect", return_value=mock_db
    ):
        vs = VectorStorage()
        vs.db.list_tables.return_value = ["mesa_memory_2"]
        mock_table = MagicMock()
        mock_db.open_table.return_value = mock_table

        mock_arrow = MagicMock()
        mock_arrow.column.return_value = [
            MagicMock(as_py=lambda: [0.1]),
            MagicMock(as_py=lambda: [0.2]),
        ]
        mock_table.search.return_value.where.return_value.limit.return_value.to_arrow.return_value = (
            mock_arrow
        )

        res = vs.get_all_embeddings(limit=1)
        # Should return only tail end
        assert res == [[0.2]]


def test_list_tables_variations():
    with patch("mesa_memory.storage.vector_index.lancedb.connect"):
        vs = VectorStorage()

        # Variation 1: list
        vs.db.list_tables.return_value = ["a"]
        assert vs._list_table_names() == ["a"]

        # Variation 2: Pydantic response
        vs.db.list_tables.return_value = MagicMock(tables=["b"])
        assert vs._list_table_names() == ["b"]

        # Variation 3: Iterable
        vs.db.list_tables.return_value = iter(["c"])
        assert vs._list_table_names() == ["c"]


def test_rbac_write():
    mock_ac = MagicMock()
    mock_ac.check_access.return_value = False
    with patch("mesa_memory.storage.vector_index.lancedb.connect"):
        vs = VectorStorage(access_control=mock_ac)
        with pytest.raises(PermissionError):
            vs.upsert_vector("cmb1", [0.1])
