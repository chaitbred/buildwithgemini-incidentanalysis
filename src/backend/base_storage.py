from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class StorageBackend(ABC):
    """
    Abstract interface every storage backend must implement.

    All incident and taxonomy operations are scoped to a *project_id*.
    Use project_id="default" for the single-tenant / backward-compatible path.

    Callers work exclusively against this interface; swapping backends is a
    matter of setting STORAGE_BACKEND (see storage_factory.py).
    """

    # ------------------------------------------------------------------
    # Incident operations (project-scoped)
    # ------------------------------------------------------------------

    @abstractmethod
    def save_incident(
        self,
        incident_data: Dict[str, Any],
        project_id: str = "default",
    ) -> str:
        """
        Persist (insert or upsert) an incident document within *project_id*.

        *incident_data* may arrive in two shapes:
          - Flat dict with top-level keys (incident_id, title, …)
          - Nested dict with "incident" and "classification" sub-dicts

        Returns the incident_id that was written.
        """

    @abstractmethod
    def get_incident(
        self,
        incident_id: str,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch a single incident by ID within *project_id*.

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
        project_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """Return incidents matching all supplied (non-None) filters within *project_id*."""

    # ------------------------------------------------------------------
    # Taxonomy operations (project-scoped)
    # ------------------------------------------------------------------

    @abstractmethod
    def save_taxonomy_tree(
        self,
        tree_dict: Dict[str, Any],
        project_id: str = "default",
    ) -> str:
        """Persist the full taxonomy tree for *project_id*. Returns a stable record ID."""

    @abstractmethod
    def get_taxonomy_tree(
        self,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        """Return the stored taxonomy tree for *project_id*, or None if not yet persisted."""

    # ------------------------------------------------------------------
    # Project management
    # ------------------------------------------------------------------

    @abstractmethod
    def save_project(self, project_data: Dict[str, Any]) -> str:
        """Create or update a project record. Returns the project_id."""

    @abstractmethod
    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a project by ID. Returns None if not found."""

    @abstractmethod
    def list_projects(self) -> List[Dict[str, Any]]:
        """Return all projects ordered by creation time."""

    @abstractmethod
    def delete_project(self, project_id: str) -> bool:
        """
        Delete a project and all its incidents/taxonomy.
        Returns True if deleted, False if the project was not found.
        The "default" project cannot be deleted.
        """
