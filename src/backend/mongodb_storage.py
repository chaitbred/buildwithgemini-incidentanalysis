# =============================================================================
# Storage backend — MongoDB (Atlas or self-hosted, multi-project)
# =============================================================================
# Required env vars:
#   MONGODB_URI          Connection string, e.g.:
#                          mongodb://localhost:27017
#                          mongodb+srv://user:pass@cluster.mongodb.net
#
# Optional env vars:
#   MONGODB_DATABASE                   Database name (default: incident_analysis)
#   MONGODB_INCIDENTS_COLLECTION       Collection name (default: incidents)
#   MONGODB_TAXONOMY_COLLECTION        Collection name (default: taxonomy)
#   MONGODB_PROJECTS_COLLECTION        Collection name (default: projects)
#
# Recommended indexes (create once):
#   db.incidents.create_index([("project_id", 1), ("primary_technology", 1)])
#   db.incidents.create_index([("project_id", 1), ("root_cause_domain", 1)])
#   db.incidents.create_index([("project_id", 1), ("failure_mechanism", 1)])
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
_PROJECTS_COL = os.getenv("MONGODB_PROJECTS_COLLECTION", "projects")


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

    Incidents use a compound _id of "{project_id}::{incident_id}" so a single
    collection can hold multiple projects without index conflicts.
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
        self._projects = db[_PROJECTS_COL]

    def _incident_id(self, project_id: str, incident_id: str) -> str:
        return f"{project_id}::{incident_id}"

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
        doc["project_id"] = project_id
        doc["_id"] = self._incident_id(project_id, inc_id)
        self._incidents.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return inc_id

    def get_incident(
        self,
        incident_id: str,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        try:
            doc = self._incidents.find_one(
                {"_id": self._incident_id(project_id, incident_id)}
            )
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
        project_id: str = "default",
    ) -> List[Dict[str, Any]]:
        try:
            query: Dict[str, Any] = {"project_id": project_id}
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

    # ------------------------------------------------------------------
    # Taxonomy operations
    # ------------------------------------------------------------------

    def save_taxonomy_tree(
        self,
        tree_dict: Dict[str, Any],
        project_id: str = "default",
    ) -> str:
        doc_id = f"{project_id}::taxonomy"
        self._taxonomy.replace_one(
            {"_id": doc_id},
            {
                "_id": doc_id,
                "project_id": project_id,
                "tree": tree_dict,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            upsert=True,
        )
        return doc_id

    def get_taxonomy_tree(
        self,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        try:
            doc = self._taxonomy.find_one({"_id": f"{project_id}::taxonomy"})
            return doc.get("tree") if doc else None
        except Exception as e:
            print(f"[MongoDBStorage] get_taxonomy_tree failed: {e}")
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
        doc = {**project_data, "_id": pid}
        self._projects.replace_one({"_id": pid}, doc, upsert=True)
        return pid

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        try:
            doc = self._projects.find_one({"_id": project_id})
            if doc:
                doc.pop("_id", None)
            return doc
        except Exception as e:
            print(f"[MongoDBStorage] get_project failed: {e}")
            return None

    def list_projects(self) -> List[Dict[str, Any]]:
        try:
            docs = list(self._projects.find({}, {"_id": 0}).sort("created_at", 1))
            return docs
        except Exception as e:
            print(f"[MongoDBStorage] list_projects failed: {e}")
            return []

    def delete_project(self, project_id: str) -> bool:
        if project_id == "default":
            raise ValueError("The 'default' project cannot be deleted.")
        try:
            result = self._projects.delete_one({"_id": project_id})
            if result.deleted_count == 0:
                return False
            self._incidents.delete_many({"project_id": project_id})
            self._taxonomy.delete_one({"_id": f"{project_id}::taxonomy"})
            return True
        except Exception as e:
            print(f"[MongoDBStorage] delete_project failed: {e}")
            return False
