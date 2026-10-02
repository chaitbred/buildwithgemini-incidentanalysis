# =============================================================================
# Storage backend — MongoDB (Atlas or self-hosted)
# =============================================================================
# Required env vars:
#   MONGODB_URI          Connection string, e.g.:
#                          mongodb://localhost:27017               (local)
#                          mongodb+srv://user:pass@cluster.mongodb.net  (Atlas)
#
# Optional env vars:
#   MONGODB_DATABASE     Database name (default: incident_analysis)
#   MONGODB_INCIDENTS_COLLECTION   Collection name (default: incidents)
#   MONGODB_TAXONOMY_COLLECTION    Collection name (default: taxonomy)
#
# Authentication is embedded in MONGODB_URI.  For Atlas with X.509 or AWS IAM,
# follow the PyMongo authentication docs and adjust the URI accordingly.
#
# Cloud options:
#   • MongoDB Atlas   — managed, multi-cloud (AWS / Azure / GCP)
#   • AWS DocumentDB  — MongoDB-compatible, set MONGODB_URI to the DocumentDB
#                        cluster endpoint (TLS required; download the CA bundle)
#   • Azure Cosmos DB for MongoDB — use the Cosmos DB connection string with
#                        the MongoDB API option (set in the Azure Portal)
#   • Self-hosted     — any MongoDB 4.4+ replica set or standalone
#
# Install:
#   pip install pymongo
# =============================================================================

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backend.base_storage import StorageBackend

_DATABASE = os.getenv("MONGODB_DATABASE", "incident_analysis")
_INCIDENTS_COL = os.getenv("MONGODB_INCIDENTS_COLLECTION", "incidents")
_TAXONOMY_COL = os.getenv("MONGODB_TAXONOMY_COLLECTION", "taxonomy")


def _flatten(incident_data: Dict[str, Any]) -> Dict[str, Any]:
    if "incident" in incident_data and "classification" in incident_data:
        raw = incident_data["incident"]
        cls = incident_data["classification"]
        doc = {
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
    else:
        doc = dict(incident_data)
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    return doc


class MongoDBStorage(StorageBackend):
    """
    MongoDB backend using PyMongo.

    Incidents are stored with incident_id as the natural _id.
    Recommended indexes (create once):
      db.incidents.create_index("primary_technology")
      db.incidents.create_index("root_cause_domain")
      db.incidents.create_index("failure_mechanism")
    """

    def __init__(self) -> None:
        from pymongo import MongoClient
        uri = os.getenv("MONGODB_URI")
        if not uri:
            raise EnvironmentError("MONGODB_URI is required for MongoDB storage.")
        self._client = MongoClient(uri)
        db = self._client[_DATABASE]
        self._incidents = db[_INCIDENTS_COL]
        self._taxonomy = db[_TAXONOMY_COL]

    def save_incident(self, incident_data: Dict[str, Any]) -> str:
        doc = _flatten(incident_data)
        inc_id = doc.get("incident_id")
        if not inc_id:
            raise ValueError("incident_id is required")
        # Use incident_id as MongoDB _id for natural upserts
        doc["_id"] = inc_id
        self._incidents.replace_one({"_id": inc_id}, doc, upsert=True)
        return inc_id

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        try:
            doc = self._incidents.find_one({"_id": incident_id})
            if doc:
                doc.pop("_id", None)
            return doc
        except Exception as e:
            print(f"[MongoDBStorage] get_incident failed: {e}")
            return None

    def query_incidents(
        self,
        technology: Optional[str] = None,
        domain: Optional[str] = None,
        failure_mechanism: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        try:
            query: Dict[str, Any] = {}
            if technology:
                query["primary_technology"] = technology
            if domain:
                query["root_cause_domain"] = domain
            if failure_mechanism:
                query["failure_mechanism"] = failure_mechanism

            cursor = self._incidents.find(query, {"_id": 0}).limit(limit)
            return list(cursor)
        except Exception as e:
            print(f"[MongoDBStorage] query_incidents failed: {e}")
            return []

    def save_taxonomy_tree(self, tree_dict: Dict[str, Any]) -> str:
        self._taxonomy.replace_one(
            {"_id": "current"},
            {
                "_id": "current",
                "tree": tree_dict,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            upsert=True,
        )
        return "current"

    def get_taxonomy_tree(self) -> Optional[Dict[str, Any]]:
        try:
            doc = self._taxonomy.find_one({"_id": "current"})
            return doc.get("tree") if doc else None
        except Exception as e:
            print(f"[MongoDBStorage] get_taxonomy_tree failed: {e}")
            return None
