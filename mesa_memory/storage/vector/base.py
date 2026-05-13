import abc
from typing import Any, Dict, List, Optional, Tuple

class BaseVectorProvider(abc.ABC):
    """
    Abstract base class for vector storage providers.
    
    This interface defines the required methods for interacting with a 
    vector database, ensuring consistency across different implementations.
    """

    @abc.abstractmethod
    def upsert(self, ids: List[str], vectors: List[List[float]], metadata: Optional[List[Dict[str, Any]]] = None) -> None:
        """
        Upsert a batch of vectors into the storage.
        
        Args:
            ids: List of unique string identifiers for the vectors.
            vectors: List of embedding vectors (list of floats).
            metadata: Optional list of metadata dictionaries corresponding to each vector.
        """
        pass

    @abc.abstractmethod
    def search(self, query_vector: List[float], limit: int = 10, **kwargs) -> List[Tuple[str, float, Optional[Dict[str, Any]]]]:
        """
        Search for the most similar vectors to a query vector.
        
        Args:
            query_vector: The vector to search for.
            limit: Maximum number of results to return.
            **kwargs: Additional provider-specific search parameters.
            
        Returns:
            A list of tuples containing (id, distance/score, metadata).
        """
        pass

    @abc.abstractmethod
    def delete(self, ids: List[str]) -> None:
        """
        Delete vectors from the storage by their IDs.
        
        Args:
            ids: List of unique string identifiers to delete.
        """
        pass

    @abc.abstractmethod
    def load(self, path: str) -> None:
        """
        Load the vector store from disk.
        
        Args:
            path: The directory or file path to load the index from.
        """
        pass

    @abc.abstractmethod
    def save(self, path: str) -> None:
        """
        Save the vector store to disk.
        
        Args:
            path: The directory or file path to save the index to.
        """
        pass
