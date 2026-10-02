# =============================================================================
# Storage backend — Google Cloud Firestore
# =============================================================================
# Required env vars:
#   FIRESTORE_PROJECT_ID   GCP project that hosts the Firestore database.
#                          Falls back to the GOOGLE_CLOUD_PROJECT env var,
#                          then to the project inferred from ADC credentials.
#
# Authentication (pick one):
#   • Application Default Credentials (ADC) — recommended for GKE / Cloud Run:
#       gcloud auth application-default login   (local dev)
#       Workload Identity                       (GKE)
#       Attached service account                (Cloud Run)
#   • Explicit service-account key file:
#       GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json
#
# Install:
#   pip install google-cloud-firestore
# =============================================================================

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backend.base_storage import StorageBackend

_INCIDENTS_COLLECTION = "incidents"
_TAXONOMY_COLLECTION = "taxonomy"


def _get_project_id() -> str:
    return (
        os.getenv("FIRESTORE_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or "qwiklabs-gcp-04-7df709bbadfe"  # legacy default kept for compatibility
    )


def _flatten(incident_data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise nested {incident, classification} shape into a flat dict."""
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

    def save_incident(self, incident_data: Dict[str, Any]) -> str:
        doc = _flatten(incident_data)
        inc_id = doc.get("incident_id")
        if not inc_id:
            raise ValueError("incident_id is required")
        self._db.collection(_INCIDENTS_COLLECTION).document(inc_id).set(doc, merge=True)
        return inc_id

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        try:
            ref = self._db.collection(_INCIDENTS_COLLECTION).document(incident_id)
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
    ) -> List[Dict[str, Any]]:
        try:
            q = self._db.collection(_INCIDENTS_COLLECTION)
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

    def save_taxonomy_tree(self, tree_dict: Dict[str, Any]) -> str:
        self._db.collection(_TAXONOMY_COLLECTION).document("current").set(
            {"tree": tree_dict, "updated_at": datetime.now(timezone.utc).isoformat()}
        )
        return "current"

    def get_taxonomy_tree(self) -> Optional[Dict[str, Any]]:
        try:
            doc = self._db.collection(_TAXONOMY_COLLECTION).document("current").get()
            return doc.to_dict().get("tree") if doc.exists else None
        except Exception as e:
            print(f"[FirestoreStorage] get_taxonomy_tree failed: {e}")
            return None
