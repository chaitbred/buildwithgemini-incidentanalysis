# =============================================================================
# Backward-compatibility shim
# =============================================================================
# All storage logic now lives in the individual backend modules under
# src/backend/. The active backend is selected via the STORAGE_BACKEND env var
# (see storage_factory.py). This file re-exports the original function names
# so that existing callers (agents/incident_analyst/agent.py,
# src/tools/postmortem_generator.py, etc.) continue to work without changes.
#
# All operations target the "default" project unless an explicit project_id
# keyword argument is passed.
# =============================================================================

import os
from typing import Any, Dict, List, Optional

from src.backend.storage_factory import get_storage

FIRESTORE_PROJECT_ID = os.getenv(
    "FIRESTORE_PROJECT_ID",
    "qwiklabs-gcp-04-7df709bbadfe",
)


def save_incident_to_firestore(
    incident_data: Dict[str, Any],
    project_id: str = "default",
) -> str:
    return get_storage().save_incident(incident_data, project_id=project_id)


def get_incident_from_firestore(
    incident_id: str,
    project_id: str = "default",
) -> Optional[Dict[str, Any]]:
    try:
        return get_storage().get_incident(incident_id, project_id=project_id)
    except Exception as e:
        print(f"[firestore_client] get_incident_from_firestore failed: {e}")
        return None


def query_incidents_from_firestore(
    technology: Optional[str] = None,
    domain: Optional[str] = None,
    failure_mechanism: Optional[str] = None,
    limit: int = 20,
    project_id: str = "default",
) -> List[Dict[str, Any]]:
    return get_storage().query_incidents(
        technology, domain, failure_mechanism, limit, project_id=project_id
    )


def save_taxonomy_tree_to_firestore(
    tree_dict: Dict[str, Any],
    project_id: str = "default",
) -> str:
    return get_storage().save_taxonomy_tree(tree_dict, project_id=project_id)


def get_taxonomy_tree_from_firestore(
    project_id: str = "default",
) -> Optional[Dict[str, Any]]:
    return get_storage().get_taxonomy_tree(project_id=project_id)
