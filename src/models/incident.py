from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class RawIncident(BaseModel):
    incident_id: str
    title: str
    description: str
    resolution: Optional[str] = None
    severity: Optional[str] = "SEV-3"
    metadata: Dict[str, Any] = Field(default_factory=dict)

class TechStackSignature(BaseModel):
    technologies: List[str] = Field(
        description="Key technologies or frameworks identified, e.g. ['Kubernetes', 'JVM', 'Prometheus']",
        default_factory=list
    )
    primary_technology: str = Field(
        description="The primary technology where the breakdown manifested, e.g. 'Kubernetes'",
        default="Unknown"
    )
    component: str = Field(
        description="The specific component or subsystem, e.g. 'Kubelet', 'pgbouncer', 'CoreDNS'",
        default="General"
    )
    failure_mechanism: str = Field(
        description="The technical failure mode, e.g. 'OOMKilled', 'Connection Pool Starvation', 'DiskPressure'",
        default="Unspecified Failure"
    )
    root_cause_domain: str = Field(
        description="High level engineering domain, e.g. 'Infrastructure & Runtime', 'Database & Storage', 'Messaging & Streaming', 'Networking & Ingress', 'Security & Access'",
        default="General"
    )
    resolution_pattern: str = Field(
        description="Categorized remediation action, e.g. 'Resource Limit Adjustment', 'Index & Query Optimization', 'Rate Limiting / Timeout Tuning', 'Configuration Rollback'",
        default="Manual Remediation"
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence score between 0.0 and 1.0 for the classification"
    )
    summary_insight: str = Field(
        default="",
        description="One-line technical summary of what broke and how it was resolved"
    )

class EnrichedIncident(BaseModel):
    incident: RawIncident
    classification: TechStackSignature
    taxonomy_path: List[str] = Field(
        description="Hierarchy path assigned in the dynamic ontology, e.g. ['Infrastructure & Runtime', 'Kubernetes', 'Memory & Cgroup', 'OOMKilled']",
        default_factory=list
    )
