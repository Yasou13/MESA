import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import faiss
import numpy as np

from mesa_memory.storage.vector.base import BaseVectorProvider

logger = logging.getLogger(__name__)

class FaissVectorProvider(BaseVectorProvider):
    """
    Local vector storage provider using FAISS (Facebook AI Similarity Search).
    """

    def __init__(self, dimension: int, index_type: str = "Flat"):
        """
        Initialize the FAISS vector provider.

        Args:
            dimension: The dimensionality of the embeddings.
            index_type: The type of FAISS index to use (e.g., 'Flat', 'IVF').
        """
        self.dimension = dimension
        self.index_type = index_type
        self.index = self._create_index()
        self.metadata_store: Dict[str, Dict[str, Any]] = {}
        self.id_to_internal: Dict[str, int] = {}
        self.internal_to_id: Dict[int, str] = {}
        self._next_internal_id = 0

    def _create_index(self) -> faiss.Index:
        """Create the underlying FAISS index based on configuration."""
        if self.index_type == "Flat":
            return faiss.IndexFlatL2(self.dimension)
        # Extend with other index types as needed
        raise ValueError(f"Unsupported index_type: {self.index_type}")

    def upsert(self, ids: List[str], vectors: List[List[float]], metadata: Optional[List[Dict[str, Any]]] = None) -> None:
        """Upsert a batch of vectors into the FAISS index."""
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors must have the same length")
        
        # Convert to numpy array
        np_vectors = np.array(vectors, dtype=np.float32)
        
        # Add to index
        # (This is a conceptual placeholder: a robust implementation needs 
        # to handle updates vs inserts, likely using IndexIDMap or similar)
        for i, vector_id in enumerate(ids):
            if vector_id not in self.id_to_internal:
                internal_id = self._next_internal_id
                self._next_internal_id += 1
                self.id_to_internal[vector_id] = internal_id
                self.internal_to_id[internal_id] = vector_id
            
            # Metadata management
            if metadata and i < len(metadata):
                self.metadata_store[vector_id] = metadata[i]
                
        self.index.add(np_vectors)
        logger.debug(f"Upserted {len(ids)} vectors into FAISS.")

    def search(self, query_vector: List[float], limit: int = 10, **kwargs) -> List[Tuple[str, float, Optional[Dict[str, Any]]]]:
        """Search for similar vectors in the FAISS index."""
        if self.index.ntotal == 0:
            return []
            
        np_query = np.array([query_vector], dtype=np.float32)
        distances, indices = self.index.search(np_query, limit)
        
        results = []
        for j, internal_id in enumerate(indices[0]):
            if internal_id != -1 and internal_id in self.internal_to_id:
                vector_id = self.internal_to_id[internal_id]
                dist = float(distances[0][j])
                meta = self.metadata_store.get(vector_id)
                results.append((vector_id, dist, meta))
                
        return results

    def delete(self, ids: List[str]) -> None:
        """Delete vectors from the FAISS index."""
        # FAISS deletion often requires ID maps or rebuilding the index
        logger.warning("Delete operation is conceptually complex in raw FAISS and may require rebuilding.")
        pass

    def load(self, path: str) -> None:
        """Load the FAISS index from disk."""
        index_file = os.path.join(path, "faiss.index")
        if os.path.exists(index_file):
            self.index = faiss.read_index(index_file)
            logger.info(f"Loaded FAISS index from {index_file}")

    def save(self, path: str) -> None:
        """Save the FAISS index to disk."""
        os.makedirs(path, exist_ok=True)
        index_file = os.path.join(path, "faiss.index")
        faiss.write_index(self.index, index_file)
        logger.info(f"Saved FAISS index to {index_file}")
