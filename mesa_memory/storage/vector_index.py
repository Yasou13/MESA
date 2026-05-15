import logging
import threading

import lancedb
import psutil
import pyarrow as pa

from mesa_memory.config import config
from mesa_memory.security.rbac import AccessControl
from mesa_memory.security.rbac_constants import _UNSET_IDENTITY

logger = logging.getLogger("MESA_VectorStorage")


class VectorStorage:
    def __init__(
        self,
        uri: str = "./storage/vector_index.lance",
        access_control: AccessControl | None = None,
    ):
        self.uri = uri
        self.access_control = access_control
        self.db = lancedb.connect(uri)
        self._tables: dict[str, object] = {}
        self._lock = threading.Lock()

    def _list_table_names(self) -> list[str]:
        """Return table names from LanceDB, handling API version differences.

        Newer LanceDB versions return a Pydantic response object with a
        ``.tables`` attribute, while older versions return a plain list.
        """
        result = self.db.list_tables()
        if isinstance(result, list):
            return result
        if hasattr(result, "tables"):
            return result.tables
        return list(result)

    def get_or_create_table(self, dimension: int):
        table_name = f"mesa_memory_{dimension}"

        with self._lock:
            if table_name in self._tables:
                return self._tables[table_name]

            schema = pa.schema(
                [
                    pa.field("cmb_id", pa.string(), nullable=False),
                    pa.field(
                        "embedding", pa.list_(pa.float32(), dimension), nullable=False
                    ),
                    pa.field("content_payload", pa.string(), nullable=False),
                    pa.field("source", pa.string(), nullable=False),
                    pa.field("fitness_score", pa.float32(), nullable=False),
                    pa.field("embedding_dim", pa.int32(), nullable=False),
                    pa.field("created_at", pa.string(), nullable=False),
                    pa.field("expired_at", pa.string(), nullable=True),
                ]
            )
            try:
                table = self.db.open_table(table_name)
            except (FileNotFoundError, ValueError):
                table = self.db.create_table(table_name, schema=schema)

            self._tables[table_name] = table
            return table

    def _check_memory_limit(self):
        mem = psutil.virtual_memory()
        used_estimate = mem.total - mem.available
        if used_estimate > config.lancedb_memory_limit_bytes:
            raise MemoryError(
                f"LanceDB memory usage ({used_estimate} bytes) exceeds limit "
                f"({config.lancedb_memory_limit_bytes} bytes)"
            )

    def upsert_vector(
        self,
        cmb_id: str,
        embedding: list[float],
        content_payload: str = "",
        source: str = "",
        fitness_score: float = 0.0,
        created_at: str = "",
        agent_id: str = _UNSET_IDENTITY,
        session_id: str = _UNSET_IDENTITY,
    ):
        if self.access_control and not self.access_control.check_access(
            agent_id, session_id, "WRITE"
        ):
            raise PermissionError(
                f"Agent '{agent_id}' lacks WRITE access for session '{session_id}'"
            )

        self._check_memory_limit()

        dimension = len(embedding)
        table = self.get_or_create_table(dimension)

        record = {
            "cmb_id": cmb_id,
            "embedding": embedding,
            "content_payload": content_payload,
            "source": source,
            "fitness_score": fitness_score,
            "embedding_dim": dimension,
            "created_at": created_at,
            "expired_at": None,
        }

        try:
            table.merge_insert(
                "cmb_id"
            ).when_matched_update_all().when_not_matched_insert_all().execute([record])
        except RuntimeError as exc:
            logger.warning(
                "merge_insert failed for cmb_id=%s, falling back to add(): %s",
                cmb_id,
                exc,
                exc_info=True,
            )
            table.add([record])

    def search(self, query_vector: list[float], limit: int = 10) -> list[dict]:
        dimension = len(query_vector)
        table = self.get_or_create_table(dimension)

        results = (
            table.search(query_vector)
            .where("expired_at IS NULL")
            .limit(limit)
            .to_list()
        )
        return results

    def soft_delete(self, cmb_id: str):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        try:
            for table_name in self._list_table_names():
                if table_name.startswith("mesa_memory_"):
                    table = self.db.open_table(table_name)
                    table.update(
                        where=f"cmb_id = '{cmb_id}'",
                        values={"expired_at": now},
                    )
            with self._lock:
                self._tables.clear()
        except (RuntimeError, OSError) as exc:
            logger.error(
                "Vector soft_delete failed for cmb_id=%s: %s",
                cmb_id,
                exc,
                exc_info=True,
            )
            raise

    def get_all_cmb_ids(self) -> set[str]:
        """Return all cmb_ids currently in the vector table (active or not). Used by reconciliation."""
        all_ids = set()
        try:
            for table_name in self._list_table_names():
                if table_name.startswith("mesa_memory_"):
                    table = self.db.open_table(table_name)
                    arrow_table = table.to_arrow()
                    all_ids.update(arrow_table.column("cmb_id").to_pylist())
            return all_ids
        except (RuntimeError, OSError, KeyError) as exc:
            logger.error(
                "get_all_cmb_ids failed: %s",
                exc,
                exc_info=True,
            )
            return set()

    def get_all_embeddings(self, limit: int = 500) -> list[list[float]]:
        """Return active embedding vectors from persistent storage.

        Used to hydrate the ValenceMotor's in-memory embedding cache on
        startup, ensuring novelty detection survives process restarts.

        Args:
            limit: Maximum number of embeddings to return.  Defaults to
                ``max_embedding_history`` (500) to match the ValenceMotor's
                ring-buffer size.  The most recent embeddings are preferred
                when the store exceeds this limit.

        Returns:
            A list of embedding vectors (each a list of floats), ordered
            from oldest to newest (tail = most recent).  Returns an empty
            list if no tables exist or on any storage error.
        """
        embeddings: list[list[float]] = []
        try:
            for table_name in self._list_table_names():
                if not table_name.startswith("mesa_memory_"):
                    continue
                table = self.db.open_table(table_name)
                # Only return non-expired (active) records
                arrow_table = (
                    table.search().where("expired_at IS NULL").limit(limit).to_arrow()
                )
                emb_column = arrow_table.column("embedding")
                for vec in emb_column:
                    embeddings.append(vec.as_py())
            # Return only the most recent `limit` embeddings (tail-end)
            if len(embeddings) > limit:
                embeddings = embeddings[-limit:]
            return embeddings
        except (RuntimeError, OSError, KeyError, Exception) as exc:
            logger.warning(
                "get_all_embeddings failed (ValenceMotor will cold-start): %s",
                exc,
            )
            return []
