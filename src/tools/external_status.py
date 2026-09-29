import urllib.request
import json
from typing import Dict, Any, List

STATUS_ENDPOINTS = {
    "github": "https://www.githubstatus.com/api/v2/summary.json"
}

def check_external_service_status(service_name: str = "github") -> Dict[str, Any]:
    """
    Checks the real-time operational status and active public incidents of external dependency services
    (e.g., GitHub, GitHub Actions, Git Operations, API Requests, Webhooks) via public status APIs.
    No API key required.
    """
    service_key = service_name.lower().strip()
    url = STATUS_ENDPOINTS.get(service_key, STATUS_ENDPOINTS["github"])

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "IncidentAnalysisAgent/1.0", "Accept": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status != 200:
                return {
                    "service": service_name,
                    "status": "error",
                    "error_message": f"HTTP {response.status}"
                }
            payload = json.loads(response.read().decode("utf-8"))

            overall_status = payload.get("status", {}).get("description", "Unknown")
            page_name = payload.get("page", {}).get("name", service_name)
            
            # Extract key components
            components = []
            for comp in payload.get("components", []):
                if not comp.get("group"):  # filter top-level services
                    components.append({
                        "name": comp.get("name"),
                        "status": comp.get("status")
                    })

            # Extract unresolved / active incidents
            active_incidents = []
            for inc in payload.get("incidents", []):
                if inc.get("status") != "resolved":
                    active_incidents.append({
                        "name": inc.get("name"),
                        "status": inc.get("status"),
                        "impact": inc.get("impact"),
                        "created_at": inc.get("created_at"),
                        "latest_update": (inc.get("incident_updates", [{}])[0]).get("body")
                    })

            return {
                "service": page_name,
                "overall_status": overall_status,
                "active_incidents_count": len(active_incidents),
                "active_incidents": active_incidents,
                "key_components": components[:8]
            }

    except Exception as e:
        return {
            "service": service_name,
            "status": "error",
            "error_message": f"Failed to fetch public service status: {str(e)}"
        }
