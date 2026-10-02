# =============================================================================
# Storage backend — Local JSON files
# =============================================================================
# No cloud account or SDK required.  All data is persisted as JSON files on
# the local filesystem.  This is the default backend when STORAGE_BACKEND is
# unset, making the application fully self-contained for development, CI, and
# Docker runs without external credentials.
#
# Optional env vars:
#   LOCAL_STORAGE_DIR   Directory to store JSON files (default: data/)
#
# Files created:
#   <LOCAL_STORAGE_DIR>/incidents.json   — list of incident documents
#   <LOCAL_STORAGE_DIR>/taxonomy.json    — taxonomy tree document
# =============================================================================

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backend.base_storage import StorageBackend

_STORAGE_DIR = os.getenv("LOCAL_STORAGE_DIR", "data")


def _incidents_path() -> str:
    return os.path.join(_STORAGE_DIR, "incidents.json")


def _taxonomy_path() -> str:
    return os.path.join(_STORAGE_DIR, "taxonomy.json")


def _load_incidents() -> Dict[str, Dict[str, Any]]:
    path = _incidents_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
        # Support both list-of-dicts and dict-keyed-by-id shapes
        if isinstance(records, list):
            return {r["incident_id"]: r for r in records if "incident_id" in r}
        return records
    except Exception:
        return {}


def _save_incidents(store: Dict[str, Dict[str, Any]]) -> None:
    os.makedirs(_STORAGE_DIR, exist_ok=True)
    with open(_incidents_path(), "w", encoding="utf-8") as f:
        json.dump(list(store.values()), f, indent=2)


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


class LocalStorage(StorageBackend):
    """
    File-system storage backend.  Thread-safety note: concurrent writes are not
    atomic.  For single-process use (development, tests, single-worker Docker)
    this is fine; use a real database backend for multi-worker deployments.
    """

    def save_incident(self, incident_data: Dict[str, Any]) -> str:
        doc = _flatten(incident_data)
        inc_id = doc.get("incident_id")
        if not inc_id:
            raise ValueError("incident_id is required")
        store = _load_incidents()
        store[inc_id] = doc
        _save_incidents(store)
        return inc_id

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return _load_incidents().get(incident_id)

    def query_incidents(
        self,
        technology: Optional[str] = None,
        domain: Optional[str] = None,
        failure_mechanism: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        results = []
        for doc in _load_incidents().values():
            if technology and doc.get("primary_technology") != technology:
                continue
            if domain and doc.get("root_cause_domain") != domain:
                continue
            if failure_mechanism and doc.get("failure_mechanism") != failure_mechanism:
                continue
            results.append(doc)
            if len(results) >= limit:
                break
        return results

    def save_taxonomy_tree(self, tree_dict: Dict[str, Any]) -> str:
        os.makedirs(_STORAGE_DIR, exist_ok=True)
        with open(_taxonomy_path(), "w", encoding="utf-8") as f:
            json.dump(
                {"tree": tree_dict, "updated_at": datetime.now(timezone.utc).isoformat()},
                f,
                indent=2,
            )
        return "current"

    def get_taxonomy_tree(self) -> Optional[Dict[str, Any]]:
        path = _taxonomy_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("tree")
        except Exception as e:
            print(f"[LocalStorage] get_taxonomy_tree failed: {e}")
            return None
