# =============================================================================
# Storage backend — Azure Cosmos DB (NoSQL / Core API)
# =============================================================================
# Required env vars:
#   AZURE_COSMOS_ENDPOINT       https://<account>.documents.azure.com:443/
#   AZURE_COSMOS_DATABASE       Name of the Cosmos DB database to use
#
# Authentication (pick one):
#   • Account key (simplest for dev/test):
#       AZURE_COSMOS_KEY        Primary or secondary account key
#   • Azure Managed Identity (recommended for Azure-hosted apps):
#       Leave AZURE_COSMOS_KEY unset; the SDK uses DefaultAzureCredential,
#       which picks up the VM/App Service/AKS managed identity automatically.
#       Install: pip install azure-identity
#   • Azure CLI / local dev:
#       az login  →  DefaultAzureCredential resolves it automatically
#
# Optional env vars:
#   AZURE_COSMOS_INCIDENTS_CONTAINER   Container name (default: incidents)
#   AZURE_COSMOS_TAXONOMY_CONTAINER    Container name (default: taxonomy)
#
# Container schema (create once via Azure Portal / Bicep / Terraform):
#   incidents container: partition key = /incident_id
#   taxonomy container:  partition key = /doc_id
#
# Install:
#   pip install azure-cosmos
#   pip install azure-identity   # only needed for Managed Identity auth
# =============================================================================

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backend.base_storage import StorageBackend

_DATABASE = os.getenv("AZURE_COSMOS_DATABASE", "incident_analysis")
_INCIDENTS_CONTAINER = os.getenv("AZURE_COSMOS_INCIDENTS_CONTAINER", "incidents")
_TAXONOMY_CONTAINER = os.getenv("AZURE_COSMOS_TAXONOMY_CONTAINER", "taxonomy")


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
    # Cosmos DB requires an `id` field (maps to incident_id here)
    doc["id"] = doc.get("incident_id", doc.get("id", ""))
    return doc


class CosmosDBStorage(StorageBackend):
    """
    Azure Cosmos DB backend using the azure-cosmos SDK.

    Queries use the SQL-like Cosmos DB query language with parameterised
    WHERE clauses, so no extra indexes are required beyond the partition key.
    """

    def __init__(self) -> None:
        from azure.cosmos import CosmosClient, PartitionKey  # noqa: F401
        endpoint = os.getenv("AZURE_COSMOS_ENDPOINT")
        key = os.getenv("AZURE_COSMOS_KEY")

        if not endpoint:
            raise EnvironmentError(
                "AZURE_COSMOS_ENDPOINT is required for CosmosDB storage."
            )

        if key:
            self._client = CosmosClient(url=endpoint, credential=key)
        else:
            # Managed Identity / DefaultAzureCredential
            from azure.identity import DefaultAzureCredential
            self._client = CosmosClient(url=endpoint, credential=DefaultAzureCredential())

        db = self._client.get_database_client(_DATABASE)
        self._incidents = db.get_container_client(_INCIDENTS_CONTAINER)
        self._taxonomy = db.get_container_client(_TAXONOMY_CONTAINER)

    def save_incident(self, incident_data: Dict[str, Any]) -> str:
        doc = _flatten(incident_data)
        inc_id = doc.get("incident_id")
        if not inc_id:
            raise ValueError("incident_id is required")
        self._incidents.upsert_item(doc)
        return inc_id

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._incidents.read_item(
                item=incident_id, partition_key=incident_id
            )
        except Exception as e:
            print(f"[CosmosDBStorage] get_incident failed: {e}")
            return None

    def query_incidents(
        self,
        technology: Optional[str] = None,
        domain: Optional[str] = None,
        failure_mechanism: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        try:
            conditions, params = [], []
            if technology:
                conditions.append("c.primary_technology = @tech")
                params.append({"name": "@tech", "value": technology})
            if domain:
                conditions.append("c.root_cause_domain = @domain")
                params.append({"name": "@domain", "value": domain})
            if failure_mechanism:
                conditions.append("c.failure_mechanism = @failure")
                params.append({"name": "@failure", "value": failure_mechanism})

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            query = f"SELECT TOP {limit} * FROM c {where}"

            return list(self._incidents.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            ))
        except Exception as e:
            print(f"[CosmosDBStorage] query_incidents failed: {e}")
            return []

    def save_taxonomy_tree(self, tree_dict: Dict[str, Any]) -> str:
        self._taxonomy.upsert_item({
            "id": "current",
            "doc_id": "current",
            "tree": tree_dict,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return "current"

    def get_taxonomy_tree(self) -> Optional[Dict[str, Any]]:
        try:
            doc = self._taxonomy.read_item(item="current", partition_key="current")
            return doc.get("tree")
        except Exception as e:
            print(f"[CosmosDBStorage] get_taxonomy_tree failed: {e}")
            return None
