# =============================================================================
# Storage backend — Local JSON files (default, multi-project)
# =============================================================================
# No cloud account or SDK required. Data is persisted as JSON files on the
# local filesystem. This is the default backend when STORAGE_BACKEND is unset.
#
# Optional env vars:
#   LOCAL_STORAGE_DIR   Root directory for all project data (default: data/)
#
# Directory layout:
#   <LOCAL_STORAGE_DIR>/
#     projects/
#       <project_id>/
#         meta.json        — project metadata (name, settings, …)
#         incidents.json   — list of flat incident documents
#         taxonomy.json    — taxonomy tree document
#
# Backward-compat note:
#   The legacy flat files data/incidents.json and data/dynamic_taxonomy.json
#   are checked first for the "default" project if the new paths don't exist.
# =============================================================================

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backend.base_storage import StorageBackend

_ROOT = os.getenv("LOCAL_STORAGE_DIR", "data")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _project_dir(project_id: str) -> str:
    return os.path.join(_ROOT, "projects", project_id)


def _meta_path(project_id: str) -> str:
    return os.path.join(_project_dir(project_id), "meta.json")


def _incidents_path(project_id: str) -> str:
    return os.path.join(_project_dir(project_id), "incidents.json")


def _taxonomy_path(project_id: str) -> str:
    return os.path.join(_project_dir(project_id), "taxonomy.json")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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


def _load_incidents(project_id: str) -> Dict[str, Dict[str, Any]]:
    path = _incidents_path(project_id)
    # Backward compat: legacy flat file for the default project
    if not os.path.exists(path) and project_id == "default":
        path = os.path.join(_ROOT, "incidents.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
        if isinstance(records, list):
            return {r["incident_id"]: r for r in records if "incident_id" in r}
        return records
    except Exception:
        return {}


def _save_incidents(store: Dict[str, Dict[str, Any]], project_id: str) -> None:
    path = _incidents_path(project_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(list(store.values()), f, indent=2)


def _load_meta(project_id: str) -> Optional[Dict[str, Any]]:
    path = _meta_path(project_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_meta(project_data: Dict[str, Any]) -> None:
    pid = project_data.get("project_id", "")
    path = _meta_path(pid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(project_data, f, indent=2)


def _ensure_default_project() -> None:
    """Create the default project meta.json if it doesn't exist yet."""
    if not os.path.exists(_meta_path("default")):
        _save_meta({
            "project_id": "default",
            "name": "Default Project",
            "description": "Auto-created default project",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "settings": {
                "classification_hint": "",
                "default_severity": "SEV-3",
                "custom_taxonomy_seed": None,
            },
        })


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------

class LocalStorage(StorageBackend):
    """
    File-system storage backend with per-project namespacing.

    Thread-safety note: concurrent writes are not atomic. For single-process
    use (development, tests, single-worker Docker) this is fine; use a real
    database backend for multi-worker deployments.
    """

    def __init__(self) -> None:
        _ensure_default_project()

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
        store = _load_incidents(project_id)
        store[inc_id] = doc
        _save_incidents(store, project_id)
        return inc_id

    def get_incident(
        self,
        incident_id: str,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        return _load_incidents(project_id).get(incident_id)

    def query_incidents(
        self,
        technology: Optional[str] = None,
        domain: Optional[str] = None,
        failure_mechanism: Optional[str] = None,
        limit: int = 20,
        project_id: str = "default",
    ) -> List[Dict[str, Any]]:
        results = []
        for doc in _load_incidents(project_id).values():
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

    # ------------------------------------------------------------------
    # Taxonomy operations
    # ------------------------------------------------------------------

    def save_taxonomy_tree(
        self,
        tree_dict: Dict[str, Any],
        project_id: str = "default",
    ) -> str:
        path = _taxonomy_path(project_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"tree": tree_dict, "updated_at": datetime.now(timezone.utc).isoformat()},
                f,
                indent=2,
            )
        return "current"

    def get_taxonomy_tree(
        self,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        path = _taxonomy_path(project_id)
        # Backward compat: legacy taxonomy file for default project
        if not os.path.exists(path) and project_id == "default":
            path = os.path.join(_ROOT, "dynamic_taxonomy.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("tree")
        except Exception as e:
            print(f"[LocalStorage] get_taxonomy_tree failed: {e}")
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
        _save_meta(project_data)
        return pid

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        return _load_meta(project_id)

    def list_projects(self) -> List[Dict[str, Any]]:
        projects_root = os.path.join(_ROOT, "projects")
        if not os.path.exists(projects_root):
            _ensure_default_project()
        results = []
        try:
            for entry in os.scandir(projects_root):
                if entry.is_dir():
                    meta = _load_meta(entry.name)
                    if meta:
                        results.append(meta)
        except Exception as e:
            print(f"[LocalStorage] list_projects failed: {e}")
        results.sort(key=lambda p: p.get("created_at", ""))
        return results

    def delete_project(self, project_id: str) -> bool:
        if project_id == "default":
            raise ValueError("The 'default' project cannot be deleted.")
        pdir = _project_dir(project_id)
        if not os.path.exists(pdir):
            return False
        shutil.rmtree(pdir, ignore_errors=True)
        return True
