# =============================================================================
# Storage backend — Google Cloud Firestore (multi-project)
# =============================================================================
# Required env vars:
#   FIRESTORE_PROJECT_ID   GCP project that hosts the Firestore database.
#                          Falls back to GOOGLE_CLOUD_PROJECT, then ADC.
#
# Authentication (pick one):
#   • Application Default Credentials (ADC) — recommended for GKE / Cloud Run
#   • GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json
#
# Install:
#   pip install google-cloud-firestore
#
# Firestore document layout:
#   projects/{project_id}                     — project metadata
#   projects/{project_id}/incidents/{inc_id}  — incident documents
#   projects/{project_id}/taxonomy/current    — taxonomy tree
# =============================================================================

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backend.base_storage import StorageBackend

_PROJECTS_COLLECTION = "projects"
_INCIDENTS_SUBCOLLECTION = "incidents"
_TAXONOMY_SUBCOLLECTION = "taxonomy"


def _get_project_id() -> str:
    return (
        os.getenv("FIRESTORE_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or "qwiklabs-gcp-04-7df709bbadfe"
    )


def _flatten(incident_data: Dict[str, Any]) -> Dict[str, Any]:
    if "incident" in incident_data and "classification" in incident_data:
        raw = incident_data["incident"]
        cls = incident_data["classification"]
        return {
            "incident_id": raw.get("incident_id"),
            "title": raw.get("title"),
            "description": raw.get("description"),
            "resolution": raw.get("resolution"),
            "severity": raw.get("severity", "SEV-3"),
            "primary_technology": cls.get("primary_technology"),
            "technologies": cls.get("technologies", []),
            "component": cls.get("component"),
            "failure_mechanism": cls.get("failure_mechanism"),
            "root_cause_domain": cls.get("root_cause_domain"),
            "resolution_pattern": cls.get("resolution_pattern"),
            "confidence": cls.get("confidence", 1.0),
            "summary_insight": cls.get("summary_insight"),
            "taxonomy_path": incident_data.get("taxonomy_path", []),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    doc = dict(incident_data)
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    return doc


class FirestoreStorage(StorageBackend):

    def __init__(self) -> None:
        from google.cloud import firestore
        self._db = firestore.Client(project=_get_project_id())

    def _incidents_col(self, project_id: str):
        return (
            self._db
            .collection(_PROJECTS_COLLECTION)
            .document(project_id)
            .collection(_INCIDENTS_SUBCOLLECTION)
        )

    def _taxonomy_col(self, project_id: str):
        return (
            self._db
            .collection(_PROJECTS_COLLECTION)
            .document(project_id)
            .collection(_TAXONOMY_SUBCOLLECTION)
        )

    # ------------------------------------------------------------------
    # Incident operations
    # ------------------------------------------------------------------

    def save_incident(
        self,
        incident_data: Dict[str, Any],
        project_id: str = "default",
    ) -> str:
        doc = _flatten(incident_data)
        inc_id = doc.get("incident_id")
        if not inc_id:
            raise ValueError("incident_id is required")
        self._incidents_col(project_id).document(inc_id).set(doc, merge=True)
        return inc_id

    def get_incident(
        self,
        incident_id: str,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        try:
            ref = self._incidents_col(project_id).document(incident_id)
            doc = ref.get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            print(f"[FirestoreStorage] get_incident failed: {e}")
            return None

    def query_incidents(
        self,
        technology: Optional[str] = None,
        domain: Optional[str] = None,
        failure_mechanism: Optional[str] = None,
        limit: int = 20,
        project_id: str = "default",
    ) -> List[Dict[str, Any]]:
        try:
            q = self._incidents_col(project_id)
            if technology:
                q = q.where("primary_technology", "==", technology)
            if domain:
                q = q.where("root_cause_domain", "==", domain)
            if failure_mechanism:
                q = q.where("failure_mechanism", "==", failure_mechanism)
            return [d.to_dict() for d in q.limit(limit).stream()]
        except Exception as e:
            print(f"[FirestoreStorage] query_incidents failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Taxonomy operations
    # ------------------------------------------------------------------

    def save_taxonomy_tree(
        self,
        tree_dict: Dict[str, Any],
        project_id: str = "default",
    ) -> str:
        self._taxonomy_col(project_id).document("current").set(
            {"tree": tree_dict, "updated_at": datetime.now(timezone.utc).isoformat()}
        )
        return "current"

    def get_taxonomy_tree(
        self,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        try:
            doc = self._taxonomy_col(project_id).document("current").get()
            return doc.to_dict().get("tree") if doc.exists else None
        except Exception as e:
            print(f"[FirestoreStorage] get_taxonomy_tree failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Project management
    # ------------------------------------------------------------------

    def save_project(self, project_data: Dict[str, Any]) -> str:
        pid = project_data.get("project_id", "")
        if not pid:
            raise ValueError("project_id is required")
        if "created_at" not in project_data:
            project_data["created_at"] = datetime.now(timezone.utc).isoformat()
        self._db.collection(_PROJECTS_COLLECTION).document(pid).set(
            project_data, merge=True
        )
        return pid

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        try:
            doc = self._db.collection(_PROJECTS_COLLECTION).document(project_id).get()
            data = doc.to_dict() if doc.exists else None
            # Filter out sub-collection keys that Firestore might include
            if data:
                data.pop(_INCIDENTS_SUBCOLLECTION, None)
                data.pop(_TAXONOMY_SUBCOLLECTION, None)
            return data
        except Exception as e:
            print(f"[FirestoreStorage] get_project failed: {e}")
            return None

    def list_projects(self) -> List[Dict[str, Any]]:
        try:
            docs = self._db.collection(_PROJECTS_COLLECTION).stream()
            results = []
            for doc in docs:
                data = doc.to_dict()
                if data and "project_id" in data:
                    results.append(data)
            results.sort(key=lambda p: p.get("created_at", ""))
            return results
        except Exception as e:
            print(f"[FirestoreStorage] list_projects failed: {e}")
            return []

    def delete_project(self, project_id: str) -> bool:
        if project_id == "default":
            raise ValueError("The 'default' project cannot be deleted.")
        try:
            proj_ref = self._db.collection(_PROJECTS_COLLECTION).document(project_id)
            if not proj_ref.get().exists:
                return False
            # Delete all incidents in the sub-collection
            for doc in self._incidents_col(project_id).stream():
                doc.reference.delete()
            # Delete taxonomy docs
            for doc in self._taxonomy_col(project_id).stream():
                doc.reference.delete()
            proj_ref.delete()
            return True
        except Exception as e:
            print(f"[FirestoreStorage] delete_project failed: {e}")
            return False
