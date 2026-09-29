import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from google.cloud import firestore

# Hardcoded project ID as required for Agent Platform compatibility
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-04-7df709bbadfe"
INCIDENTS_COLLECTION = "incidents"
TAXONOMY_COLLECTION = "taxonomy"

def get_firestore_client() -> firestore.Client:
    """Returns a Firestore client initialized strictly with the hardcoded project ID."""
    return firestore.Client(project=FIRESTORE_PROJECT_ID)

def save_incident_to_firestore(incident_data: Dict[str, Any]) -> str:
    """
    Writes or updates an incident document in the Firestore 'incidents' collection.
    Sensible fields:
      - incident_id (str)
      - title (str)
      - description (str)
      - resolution (str)
      - severity (str)
      - primary_technology (str)
      - technologies (list[str])
      - component (str)
      - failure_mechanism (str)
      - root_cause_domain (str)
      - resolution_pattern (str)
      - taxonomy_path (list[str])
      - confidence (float)
      - summary_insight (str)
      - updated_at (str ISO timestamp)
    """
    db = get_firestore_client()
    inc_id = incident_data.get("incident_id") or incident_data.get("incident", {}).get("incident_id")
    if not inc_id:
        raise ValueError("incident_id is required to persist incident")

    # Flatten structured models if nested
    doc_data = {}
    if "incident" in incident_data and "classification" in incident_data:
        raw = incident_data["incident"]
        cls = incident_data["classification"]
        doc_data = {
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
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
    else:
        doc_data = dict(incident_data)
        doc_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    doc_ref = db.collection(INCIDENTS_COLLECTION).document(inc_id)
    doc_ref.set(doc_data, merge=True)
    return inc_id

def get_incident_from_firestore(incident_id: str) -> Optional[Dict[str, Any]]:
    """Reads a single incident document by ID from Firestore."""
    db = get_firestore_client()
    doc_ref = db.collection(INCIDENTS_COLLECTION).document(incident_id)
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return None

def query_incidents_from_firestore(
    technology: Optional[str] = None,
    domain: Optional[str] = None,
    failure_mechanism: Optional[str] = None,
    limit: int = 20
) -> List[Dict[str, Any]]:
    """Queries incidents from Firestore with optional filtering."""
    db = get_firestore_client()
    query = db.collection(INCIDENTS_COLLECTION)

    if technology:
        query = query.where("primary_technology", "==", technology)
    if domain:
        query = query.where("root_cause_domain", "==", domain)
    if failure_mechanism:
        query = query.where("failure_mechanism", "==", failure_mechanism)

    docs = query.limit(limit).stream()
    return [doc.to_dict() for doc in docs]

def save_taxonomy_tree_to_firestore(tree_dict: Dict[str, Any]) -> str:
    """Stores the dynamic taxonomy hierarchy in Firestore under 'taxonomy/current'."""
    db = get_firestore_client()
    doc_ref = db.collection(TAXONOMY_COLLECTION).document("current")
    doc_ref.set({
        "tree": tree_dict,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })
    return "current"

def get_taxonomy_tree_from_firestore() -> Optional[Dict[str, Any]]:
    """Retrieves the current taxonomy hierarchy from Firestore."""
    db = get_firestore_client()
    doc_ref = db.collection(TAXONOMY_COLLECTION).document("current")
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict().get("tree")
    return None
