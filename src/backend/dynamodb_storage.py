# =============================================================================
# Storage backend — AWS DynamoDB
# =============================================================================
# Required env vars:
#   AWS_DEFAULT_REGION          e.g. us-east-1
#
# Authentication (pick one — boto3 standard credential chain):
#   • IAM role attached to EC2 / ECS / Lambda (recommended for AWS-hosted apps)
#   • Environment variables:
#       AWS_ACCESS_KEY_ID
#       AWS_SECRET_ACCESS_KEY
#       AWS_SESSION_TOKEN        (only for temporary credentials)
#   • AWS credentials file:     ~/.aws/credentials
#   • AWS SSO / IAM Identity Center
#
# Optional env vars:
#   DYNAMODB_INCIDENTS_TABLE    Table name for incidents (default: incidents)
#   DYNAMODB_TAXONOMY_TABLE     Table name for taxonomy  (default: taxonomy)
#   DYNAMODB_ENDPOINT_URL       Override endpoint, e.g. http://localhost:8000
#                               for DynamoDB Local during development
#
# Table schema (create once, e.g. via AWS Console or CDK/Terraform):
#   incidents table: partition key = incident_id (String)
#   taxonomy table:  partition key = doc_id      (String)
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

    DynamoDB does not support server-side inequality filters across multiple
    attributes without a GSI (Global Secondary Index).  query_incidents does a
    Scan with FilterExpressions, which is fine for moderate data volumes. For
    large tables, add GSIs on primary_technology / root_cause_domain and switch
    to Query operations.
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

    def save_incident(self, incident_data: Dict[str, Any]) -> str:
        doc = _flatten(incident_data)
        inc_id = doc.get("incident_id")
        if not inc_id:
            raise ValueError("incident_id is required")
        self._incidents.put_item(Item=doc)
        return inc_id

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._incidents.get_item(Key={"incident_id": incident_id})
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
    ) -> List[Dict[str, Any]]:
        from boto3.dynamodb.conditions import Attr
        try:
            filters = []
            if technology:
                filters.append(Attr("primary_technology").eq(technology))
            if domain:
                filters.append(Attr("root_cause_domain").eq(domain))
            if failure_mechanism:
                filters.append(Attr("failure_mechanism").eq(failure_mechanism))

            kwargs: Dict[str, Any] = {"Limit": limit}
            if filters:
                expr = filters[0]
                for f in filters[1:]:
                    expr = expr & f
                kwargs["FilterExpression"] = expr

            resp = self._incidents.scan(**kwargs)
            return resp.get("Items", [])
        except Exception as e:
            print(f"[DynamoDBStorage] query_incidents failed: {e}")
            return []

    def save_taxonomy_tree(self, tree_dict: Dict[str, Any]) -> str:
        self._taxonomy.put_item(Item={
            "doc_id": "current",
            "tree": json.dumps(tree_dict),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return "current"

    def get_taxonomy_tree(self) -> Optional[Dict[str, Any]]:
        try:
            resp = self._taxonomy.get_item(Key={"doc_id": "current"})
            item = resp.get("Item")
            if item:
                return json.loads(item["tree"])
            return None
        except Exception as e:
            print(f"[DynamoDBStorage] get_taxonomy_tree failed: {e}")
            return None
