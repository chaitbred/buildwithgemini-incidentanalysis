# =============================================================================
# Storage backend — Azure Cosmos DB (NoSQL / Core API, multi-project)
# =============================================================================
# Required env vars:
#   AZURE_COSMOS_ENDPOINT       https://<account>.documents.azure.com:443/
#   AZURE_COSMOS_DATABASE       Name of the Cosmos DB database to use
#
# Authentication (pick one):
#   • AZURE_COSMOS_KEY          Primary or secondary account key
#   • Managed Identity (leave key unset; uses DefaultAzureCredential)
#
# Optional env vars:
#   AZURE_COSMOS_INCIDENTS_CONTAINER   (default: incidents)
#   AZURE_COSMOS_TAXONOMY_CONTAINER    (default: taxonomy)
#   AZURE_COSMOS_PROJECTS_CONTAINER    (default: projects)
#
# Container schema:
#   incidents container: partition key = /project_id
#   taxonomy container:  partition key = /doc_id
#   projects container:  partition key = /project_id
#
# Install:
#   pip install azure-cosmos azure-identity
# =============================================================================

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backend.base_storage import StorageBackend

_DATABASE = os.getenv("AZURE_COSMOS_DATABASE", "incident_analysis")
_INCIDENTS_CONTAINER = os.getenv("AZURE_COSMOS_INCIDENTS_CONTAINER", "incidents")
_TAXONOMY_CONTAINER = os.getenv("AZURE_COSMOS_TAXONOMY_CONTAINER", "taxonomy")
_PROJECTS_CONTAINER = os.getenv("AZURE_COSMOS_PROJECTS_CONTAINER", "projects")


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
    doc["id"] = doc.get("incident_id", doc.get("id", ""))
    return doc


class CosmosDBStorage(StorageBackend):
    """
    Azure Cosmos DB backend using the azure-cosmos SDK.

    Incidents are partitioned by project_id so cross-project queries are cheap.
    """

    def __init__(self) -> None:
        from azure.cosmos import CosmosClient  # noqa: F401
        endpoint = os.getenv("AZURE_COSMOS_ENDPOINT")
        key = os.getenv("AZURE_COSMOS_KEY")

        if not endpoint:
            raise EnvironmentError("AZURE_COSMOS_ENDPOINT is required for CosmosDB storage.")

        if key:
            self._client = CosmosClient(url=endpoint, credential=key)
        else:
            from azure.identity import DefaultAzureCredential
            self._client = CosmosClient(url=endpoint, credential=DefaultAzureCredential())

        db = self._client.get_database_client(_DATABASE)
        self._incidents = db.get_container_client(_INCIDENTS_CONTAINER)
        self._taxonomy = db.get_container_client(_TAXONOMY_CONTAINER)
        self._projects = db.get_container_client(_PROJECTS_CONTAINER)

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
        self._incidents.upsert_item(doc)
        return inc_id

    def get_incident(
        self,
        incident_id: str,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        try:
            return self._incidents.read_item(item=incident_id, partition_key=project_id)
        except Exception as e:
            print(f"[CosmosDBStorage] get_incident failed: {e}")
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
            conditions = ["c.project_id = @project_id"]
            params = [{"name": "@project_id", "value": project_id}]
            if technology:
                conditions.append("c.primary_technology = @tech")
                params.append({"name": "@tech", "value": technology})
            if domain:
                conditions.append("c.root_cause_domain = @domain")
                params.append({"name": "@domain", "value": domain})
            if failure_mechanism:
                conditions.append("c.failure_mechanism = @failure")
                params.append({"name": "@failure", "value": failure_mechanism})

            where = "WHERE " + " AND ".join(conditions)
            query = f"SELECT TOP {limit} * FROM c {where}"

            return list(self._incidents.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            ))
        except Exception as e:
            print(f"[CosmosDBStorage] query_incidents failed: {e}")
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
        self._taxonomy.upsert_item({
            "id": doc_id,
            "doc_id": doc_id,
            "project_id": project_id,
            "tree": tree_dict,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return doc_id

    def get_taxonomy_tree(
        self,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        try:
            doc_id = f"{project_id}::taxonomy"
            doc = self._taxonomy.read_item(item=doc_id, partition_key=doc_id)
            return doc.get("tree")
        except Exception as e:
            print(f"[CosmosDBStorage] get_taxonomy_tree failed: {e}")
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
        project_data["id"] = pid
        self._projects.upsert_item(project_data)
        return pid

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        try:
            doc = self._projects.read_item(item=project_id, partition_key=project_id)
            doc.pop("id", None)
            return doc
        except Exception as e:
            print(f"[CosmosDBStorage] get_project failed: {e}")
            return None

    def list_projects(self) -> List[Dict[str, Any]]:
        try:
            results = list(self._projects.query_items(
                query="SELECT * FROM c ORDER BY c.created_at",
                enable_cross_partition_query=True,
            ))
            for r in results:
                r.pop("id", None)
            return results
        except Exception as e:
            print(f"[CosmosDBStorage] list_projects failed: {e}")
            return []

    def delete_project(self, project_id: str) -> bool:
        if project_id == "default":
            raise ValueError("The 'default' project cannot be deleted.")
        try:
            existing = self.get_project(project_id)
            if not existing:
                return False
            # Delete all incidents
            inc_docs = list(self._incidents.query_items(
                query="SELECT c.id FROM c WHERE c.project_id = @pid",
                parameters=[{"name": "@pid", "value": project_id}],
                enable_cross_partition_query=True,
            ))
            for doc in inc_docs:
                self._incidents.delete_item(item=doc["id"], partition_key=project_id)
            # Delete taxonomy
            try:
                doc_id = f"{project_id}::taxonomy"
                self._taxonomy.delete_item(item=doc_id, partition_key=doc_id)
            except Exception:
                pass
            # Delete project record
            self._projects.delete_item(item=project_id, partition_key=project_id)
            return True
        except Exception as e:
            print(f"[CosmosDBStorage] delete_project failed: {e}")
            return False
