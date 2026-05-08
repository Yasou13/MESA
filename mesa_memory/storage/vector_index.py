import psutil
import pyarrow as pa
import lancedb

from mesa_memory.config import config


class VectorStorage:
    def __init__(self, uri: str = "./storage/vector_index.lance"):
        self.uri = uri
        self.db = lancedb.connect(uri)
        self._table = None
        self._dimension = None

    def create_table(self, dimension: int):
        self._dimension = dimension
        schema = pa.schema([
            pa.field("cmb_id", pa.string(), nullable=False),
            pa.field("embedding", pa.list_(pa.float32(), dimension), nullable=False),
            pa.field("content_payload", pa.string(), nullable=False),
            pa.field("source", pa.string(), nullable=False),
            pa.field("fitness_score", pa.float32(), nullable=False),
            pa.field("embedding_dim", pa.int32(), nullable=False),
            pa.field("created_at", pa.string(), nullable=False),
            pa.field("expired_at", pa.string(), nullable=True),
        ])
        try:
            self._table = self.db.open_table("cmb_vectors")
        except Exception:
            self._table = self.db.create_table("cmb_vectors", schema=schema)
        return self._table

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
    ):
        self._check_memory_limit()

        dimension = len(embedding)
        if self._table is None:
            self.create_table(dimension)

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
            self._table.merge_insert("cmb_id").when_matched_update_all().when_not_matched_insert_all().execute([record])
        except Exception:
            self._table.add([record])

    def search(self, query_vector: list[float], limit: int = 10) -> list[dict]:
        if self._table is None:
            return []

        if self._dimension and len(query_vector) != self._dimension:
            raise ValueError(f"Query vector dimension mismatch. Expected {self._dimension}, got {len(query_vector)}")

        results = (
            self._table
            .search(query_vector)
            .where("expired_at IS NULL")
            .limit(limit)
            .to_list()
        )
        return results

    def soft_delete(self, cmb_id: str):
        if self._table is None:
            return
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self._table.update(
            where=f"cmb_id = '{cmb_id}'",
            values={"expired_at": now},
        )

    def get_all_cmb_ids(self) -> set[str]:
        """Return all cmb_ids currently in the vector table (active or not). Used by reconciliation."""
        if self._table is None:
            return set()
        try:
            rows = self._table.to_pandas()["cmb_id"].tolist()
            return set(rows)
        except Exception:
            return set()

