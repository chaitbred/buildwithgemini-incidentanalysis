import os
import sys
import json
from typing import Dict, Any, Optional

# Ensure project root is in path
sys.path.insert(0, "/config/Desktop/IncidentAnalysis")

from google.adk.agents import Agent
from src.models.incident import RawIncident
from src.agents.classifier_agent import MultiTechClassifierAgent
from src.agents.taxonomy_agent import DynamicTaxonomyAgent
from src.pipeline.batch_processor import IncidentBatchProcessor
from src.backend.firestore_client import (
    FIRESTORE_PROJECT_ID,
    save_incident_to_firestore,
    get_incident_from_firestore,
    query_incidents_from_firestore,
    save_taxonomy_tree_to_firestore,
    get_taxonomy_tree_from_firestore
)
from src.tools.runbook_service import search_runbooks
from src.tools.postmortem_generator import create_postmortem_report
from src.tools.external_status import check_external_service_status

classifier = MultiTechClassifierAgent()
tax_agent = DynamicTaxonomyAgent(storage_path="/config/Desktop/IncidentAnalysis/data/dynamic_taxonomy.json")
batch_processor = IncidentBatchProcessor(
    classifier=classifier,
    taxonomy_agent=tax_agent,
    output_enriched_path="/config/Desktop/IncidentAnalysis/data/enriched_incidents.json"
)

def check_external_provider_status(service_name: str = "github") -> str:
    """
    Checks the real-time operational status, active public incidents, and component health
    for external third-party infrastructure providers (e.g. 'github') via free public APIs without needing an API key.
    Useful for determining if an incident is caused by upstream provider outages (e.g., CI/CD failures, Webhooks, API downtime).
    """
    res = check_external_service_status(service_name)
    return json.dumps(res, indent=2)

def search_remediation_runbooks(technology: str, failure_mechanism: str = "") -> str:
    """
    Searches the internal SRE remediation runbooks repository for diagnostic steps,
    mitigation commands, and verification criteria matching the technology and failure mechanism.
    """
    matches = search_runbooks(technology=technology, failure_mechanism=failure_mechanism)
    return json.dumps({
        "matched_count": len(matches),
        "runbooks": matches
    }, indent=2)

def generate_postmortem_markdown_report(incident_id: str) -> str:
    """
    Fetches an incident from Firestore and generates a comprehensive post-mortem / RCA markdown report,
    persisting it to the reports/ directory and returning the document content.
    """
    try:
        result = create_postmortem_report(incident_id)
        return json.dumps({
            "status": "success",
            "incident_id": incident_id,
            "report_filepath": result["filepath"],
            "report_preview": result["markdown_content"][:600] + "\n...[Report continues]"
        }, indent=2)
    except Exception as e:
        return f"Error generating post-mortem report: {str(e)}"

def classify_and_record_incident(title: str, description: str, resolution: str = "", severity: str = "SEV-2") -> str:
    """
    Classifies an incident, extracts multi-technology signatures and failure mechanism,
    updates the dynamic taxonomy, and writes the enriched incident record to Firestore.
    """
    raw = RawIncident(
        incident_id=f"INC-{os.urandom(2).hex().upper()}",
        title=title,
        description=description,
        resolution=resolution,
        severity=severity
    )
    sig = classifier.classify_incident(raw)
    path = tax_agent.evolve_taxonomy(raw, sig)
    tax_agent.save()

    payload = {
        "incident_id": raw.incident_id,
        "title": raw.title,
        "description": raw.description,
        "resolution": raw.resolution,
        "severity": raw.severity,
        "primary_technology": sig.primary_technology,
        "technologies": sig.technologies,
        "component": sig.component,
        "failure_mechanism": sig.failure_mechanism,
        "root_cause_domain": sig.root_cause_domain,
        "resolution_pattern": sig.resolution_pattern,
        "confidence": sig.confidence,
        "summary_insight": sig.summary_insight,
        "taxonomy_path": path
    }

    try:
        save_incident_to_firestore(payload)
        save_taxonomy_tree_to_firestore(tax_agent.tree.to_hierarchical_dict())
        payload["firestore_persisted"] = True
    except Exception as e:
        payload["firestore_persisted"] = False
        payload["firestore_error"] = str(e)

    return json.dumps(payload, indent=2)

def read_incident_from_firestore(incident_id: str) -> str:
    """
    Reads an incident document directly from Firestore by its Incident ID (e.g. 'INC-1001').
    """
    try:
        doc = get_incident_from_firestore(incident_id)
        if not doc:
            return f"Incident '{incident_id}' not found in Firestore."
        return json.dumps(doc, indent=2)
    except Exception as e:
        return f"Error querying Firestore: {str(e)}"

def query_firestore_incidents(technology: Optional[str] = None, domain: Optional[str] = None, failure_mechanism: Optional[str] = None, limit: int = 10) -> str:
    """
    Queries Firestore incidents collection filtered by primary technology, root cause domain, or failure mechanism.
    """
    try:
        docs = query_incidents_from_firestore(
            technology=technology,
            domain=domain,
            failure_mechanism=failure_mechanism,
            limit=limit
        )
        return json.dumps({
            "count": len(docs),
            "incidents": docs
        }, indent=2)
    except Exception as e:
        return f"Error filtering Firestore incidents: {str(e)}"

def add_custom_ontology(ontology_json_str: str) -> str:
    """Seeds or expands the incident ontology tree with a custom hierarchical structure (JSON string)."""
    try:
        data = json.loads(ontology_json_str)
        added = tax_agent.import_custom_ontology(data)
        save_taxonomy_tree_to_firestore(tax_agent.tree.to_hierarchical_dict())
        return f"Successfully imported custom ontology ({added} nodes created/updated). Total tree nodes: {len(tax_agent.tree.nodes)}"
    except Exception as e:
        return f"Error importing custom ontology: {str(e)}"

def view_taxonomy_tree() -> str:
    """Returns the current dynamic hierarchical ontology tree of all classified incidents."""
    tax_agent.load()
    return json.dumps(tax_agent.tree.to_hierarchical_dict(), indent=2)

def get_analytics_summary() -> str:
    """Returns summary analytics including incident counts grouped by technology, domain, and failure mode."""
    try:
        docs = query_incidents_from_firestore(limit=200)
        if docs:
            techs = {}
            failures = {}
            for item in docs:
                t = item.get("primary_technology", "Unknown")
                f_mode = item.get("failure_mechanism", "Unknown")
                techs[t] = techs.get(t, 0) + 1
                failures[f_mode] = failures.get(f_mode, 0) + 1

            return json.dumps({
                "source": "Firestore (collection: incidents)",
                "total_incidents": len(docs),
                "by_technology": techs,
                "by_failure_mechanism": failures
            }, indent=2)
    except Exception:
        pass

    enriched_path = "/config/Desktop/IncidentAnalysis/data/enriched_incidents.json"
    if not os.path.exists(enriched_path):
        return json.dumps({"status": "No enriched incidents recorded yet."})
    with open(enriched_path, "r") as f:
        data = json.load(f)

    techs = {}
    failures = {}
    for item in data:
        cls = item.get("classification", {})
        t = cls.get("primary_technology", "Unknown")
        f_mode = cls.get("failure_mechanism", "Unknown")
        techs[t] = techs.get(t, 0) + 1
        failures[f_mode] = failures.get(f_mode, 0) + 1

    return json.dumps({
        "source": "Local JSON store",
        "total_incidents": len(data),
        "by_technology": techs,
        "by_failure_mechanism": failures
    }, indent=2)

from src.tools.video_generator import generate_incident_diagnostic_video

from google.adk.models import Gemini
from google.genai import types

MODEL = "gemini-2.5-flash"

# Google ADK Root Agent declaration
root_agent = Agent(
    name="incident_analyst",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description="Autonomous Multi-Technology Incident Investigator, Dynamic Taxonomy Miner, and SRE Remediation Assistant.",
    instruction=(
        f"You are an expert Autonomous Site Reliability & Incident Investigator backed by Firestore (Project: {FIRESTORE_PROJECT_ID}). "
        "You help users categorize complex incidents, query historical outages from Firestore, "
        "search actionable remediation runbooks with step-by-step diagnostic commands, "
        "check upstream third-party status via public status APIs (e.g. GitHub Actions, Webhooks, Git Operations), "
        "generate animated incident diagnostic and simulation videos using Google Omni model, "
        "and generate formal post-mortem RCA reports."
    ),
    tools=[
        check_external_provider_status,
        search_remediation_runbooks,
        generate_postmortem_markdown_report,
        generate_incident_diagnostic_video,
        classify_and_record_incident,
        read_incident_from_firestore,
        query_firestore_incidents,
        add_custom_ontology,
        view_taxonomy_tree,
        get_analytics_summary
    ]
)
