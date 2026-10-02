from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class StorageBackend(ABC):
    """
    Abstract interface every storage backend must implement.

    Callers work exclusively against this interface; swapping backends is a
    matter of setting STORAGE_BACKEND (see storage_factory.py) without
    changing any business logic.
    """

    @abstractmethod
    def save_incident(self, incident_data: Dict[str, Any]) -> str:
        """
        Persist (insert or upsert) an incident document.

        *incident_data* may arrive in two shapes:
          - Flat dict with top-level keys (incident_id, title, …)
          - Nested dict with "incident" and "classification" sub-dicts

        Returns the incident_id that was written.
        """

    @abstractmethod
    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single incident by ID.

        Returns the document as a plain dict, or None when not found or when
        the backend is unreachable (callers must handle None gracefully).
        """

    @abstractmethod
    def query_incidents(
        self,
        technology: Optional[str] = None,
        domain: Optional[str] = None,
        failure_mechanism: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return incidents matching all supplied (non-None) filters."""

    @abstractmethod
    def save_taxonomy_tree(self, tree_dict: Dict[str, Any]) -> str:
        """Persist the full taxonomy tree. Returns a stable document/record ID."""

    @abstractmethod
    def get_taxonomy_tree(self) -> Optional[Dict[str, Any]]:
        """Return the stored taxonomy tree dict, or None if not yet persisted."""
