# =============================================================================
# Storage backend — AWS DynamoDB (multi-project)
# =============================================================================
# Required env vars:
#   AWS_DEFAULT_REGION          e.g. us-east-1
#
# Authentication (boto3 standard credential chain):
#   • IAM role on EC2 / ECS / Lambda (recommended)
#   • AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_SESSION_TOKEN
#   • ~/.aws/credentials
#
# Optional env vars:
#   DYNAMODB_INCIDENTS_TABLE    Table name for incidents (default: incidents)
#   DYNAMODB_TAXONOMY_TABLE     Table name for taxonomy  (default: taxonomy)
#   DYNAMODB_PROJECTS_TABLE     Table name for projects  (default: projects)
#   DYNAMODB_ENDPOINT_URL       Override endpoint (e.g. DynamoDB Local)
#
# Table schema (create once):
#   incidents table: partition key = project_id (String), sort key = incident_id (String)
#   taxonomy table:  partition key = doc_id (String)   e.g. "default::taxonomy"
#   projects table:  partition key = project_id (String)
#
# Install:
#   pip install boto3
# =============================================================================

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backend.base_storage import StorageBackend

_INCIDENTS_TABLE = os.getenv("DYNAMODB_INCIDENTS_TABLE", "incidents")
_TAXONOMY_TABLE = os.getenv("DYNAMODB_TAXONOMY_TABLE", "taxonomy")
_PROJECTS_TABLE = os.getenv("DYNAMODB_PROJECTS_TABLE", "projects")


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


class DynamoDBStorage(StorageBackend):
    """
    DynamoDB backend using boto3.

    Incidents table uses a composite key: project_id (PK) + incident_id (SK).
    query_incidents uses a Query on the partition key plus FilterExpressions for
    technology/domain/failure fields. For large tables, add GSIs on those fields.
    """

    def __init__(self) -> None:
        import boto3
        kwargs: Dict[str, Any] = {}
        endpoint = os.getenv("DYNAMODB_ENDPOINT_URL")
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        self._dynamodb = boto3.resource("dynamodb", **kwargs)
        self._incidents = self._dynamodb.Table(_INCIDENTS_TABLE)
        self._taxonomy = self._dynamodb.Table(_TAXONOMY_TABLE)
        self._projects = self._dynamodb.Table(_PROJECTS_TABLE)

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
        self._incidents.put_item(Item=doc)
        return inc_id

    def get_incident(
        self,
        incident_id: str,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        try:
            resp = self._incidents.get_item(
                Key={"project_id": project_id, "incident_id": incident_id}
            )
            return resp.get("Item")
        except Exception as e:
            print(f"[DynamoDBStorage] get_incident failed: {e}")
            return None

    def query_incidents(
        self,
        technology: Optional[str] = None,
        domain: Optional[str] = None,
        failure_mechanism: Optional[str] = None,
        limit: int = 20,
        project_id: str = "default",
    ) -> List[Dict[str, Any]]:
        from boto3.dynamodb.conditions import Attr, Key
        try:
            filters = []
            if technology:
                filters.append(Attr("primary_technology").eq(technology))
            if domain:
                filters.append(Attr("root_cause_domain").eq(domain))
            if failure_mechanism:
                filters.append(Attr("failure_mechanism").eq(failure_mechanism))

            kwargs: Dict[str, Any] = {
                "KeyConditionExpression": Key("project_id").eq(project_id),
                "Limit": limit,
            }
            if filters:
                expr = filters[0]
                for f in filters[1:]:
                    expr = expr & f
                kwargs["FilterExpression"] = expr

            resp = self._incidents.query(**kwargs)
            return resp.get("Items", [])
        except Exception as e:
            print(f"[DynamoDBStorage] query_incidents failed: {e}")
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
        self._taxonomy.put_item(Item={
            "doc_id": doc_id,
            "tree": json.dumps(tree_dict),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return doc_id

    def get_taxonomy_tree(
        self,
        project_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        try:
            doc_id = f"{project_id}::taxonomy"
            resp = self._taxonomy.get_item(Key={"doc_id": doc_id})
            item = resp.get("Item")
            if item:
                return json.loads(item["tree"])
            return None
        except Exception as e:
            print(f"[DynamoDBStorage] get_taxonomy_tree failed: {e}")
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
        self._projects.put_item(Item=project_data)
        return pid

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._projects.get_item(Key={"project_id": project_id})
            return resp.get("Item")
        except Exception as e:
            print(f"[DynamoDBStorage] get_project failed: {e}")
            return None

    def list_projects(self) -> List[Dict[str, Any]]:
        try:
            resp = self._projects.scan()
            items = resp.get("Items", [])
            items.sort(key=lambda p: p.get("created_at", ""))
            return items
        except Exception as e:
            print(f"[DynamoDBStorage] list_projects failed: {e}")
            return []

    def delete_project(self, project_id: str) -> bool:
        if project_id == "default":
            raise ValueError("The 'default' project cannot be deleted.")
        try:
            existing = self.get_project(project_id)
            if not existing:
                return False
            # Delete all incidents for this project
            from boto3.dynamodb.conditions import Key
            resp = self._incidents.query(
                KeyConditionExpression=Key("project_id").eq(project_id)
            )
            with self._incidents.batch_writer() as batch:
                for item in resp.get("Items", []):
                    batch.delete_item(
                        Key={"project_id": project_id, "incident_id": item["incident_id"]}
                    )
            # Delete taxonomy
            self._taxonomy.delete_item(Key={"doc_id": f"{project_id}::taxonomy"})
            # Delete project record
            self._projects.delete_item(Key={"project_id": project_id})
            return True
        except Exception as e:
            print(f"[DynamoDBStorage] delete_project failed: {e}")
            return False
