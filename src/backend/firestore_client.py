# =============================================================================
# Backward-compatibility shim
# =============================================================================
# All storage logic now lives in the individual backend modules under
# src/backend/.  The active backend is selected via the STORAGE_BACKEND env var
# (see storage_factory.py).  This file re-exports the original function names
# so that existing callers (agents/incident_analyst/agent.py,
# src/tools/postmortem_generator.py, etc.) continue to work without changes.
# =============================================================================

import os
from typing import Any, Dict, List, Optional

from src.backend.storage_factory import get_storage

# Kept for callers that import this constant directly (e.g. agent.py).
FIRESTORE_PROJECT_ID = os.getenv(
    "FIRESTORE_PROJECT_ID",
    "qwiklabs-gcp-04-7df709bbadfe",
)


def save_incident_to_firestore(incident_data: Dict[str, Any]) -> str:
    return get_storage().save_incident(incident_data)


def get_incident_from_firestore(incident_id: str) -> Optional[Dict[str, Any]]:
    return get_storage().get_incident(incident_id)


def query_incidents_from_firestore(
    technology: Optional[str] = None,
    domain: Optional[str] = None,
    failure_mechanism: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    return get_storage().query_incidents(technology, domain, failure_mechanism, limit)


def save_taxonomy_tree_to_firestore(tree_dict: Dict[str, Any]) -> str:
    return get_storage().save_taxonomy_tree(tree_dict)


def get_taxonomy_tree_from_firestore() -> Optional[Dict[str, Any]]:
    return get_storage().get_taxonomy_tree()
