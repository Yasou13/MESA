import psutil
import pyarrow as pa
import lancedb

from mesa_memory.config import config
from mesa_memory.security.rbac import AccessControl


class VectorStorage:
    def __init__(self, uri: str = "./storage/vector_index.lance", access_control: AccessControl | None = None):
        self.uri = uri
        self.access_control = access_control
        self.db = lancedb.connect(uri)
        self._tables = {}

    def get_or_create_table(self, dimension: int):
        table_name = f"mesa_memory_{dimension}"
        if table_name in self._tables:
            return self._tables[table_name]

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
            table = self.db.open_table(table_name)
        except Exception:
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
        agent_id: str = "system",
        session_id: str = "system",
    ):
        if self.access_control and not self.access_control.check_access(agent_id, session_id, "WRITE"):
            raise PermissionError(f"Agent '{agent_id}' lacks WRITE access for session '{session_id}'")

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
            table.merge_insert("cmb_id").when_matched_update_all().when_not_matched_insert_all().execute([record])
        except Exception:
            table.add([record])

    def search(self, query_vector: list[float], limit: int = 10) -> list[dict]:
        dimension = len(query_vector)
        table = self.get_or_create_table(dimension)

        results = (
            table
            .search(query_vector)
            .where("expired_at IS NULL")
            .limit(limit)
            .to_list()
        )
        return results

    def soft_delete(self, cmb_id: str):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        try:
            for table_name in self.db.table_names():
                if table_name.startswith("mesa_memory_"):
                    table = self.db.open_table(table_name)
                    table.update(
                        where=f"cmb_id = '{cmb_id}'",
                        values={"expired_at": now},
                    )
        except Exception:
            pass

    def get_all_cmb_ids(self) -> set[str]:
        """Return all cmb_ids currently in the vector table (active or not). Used by reconciliation."""
        all_ids = set()
        try:
            for table_name in self.db.table_names():
                if table_name.startswith("mesa_memory_"):
                    table = self.db.open_table(table_name)
                    rows = table.to_pandas()["cmb_id"].tolist()
                    all_ids.update(rows)
            return all_ids
        except Exception:
            return set()

