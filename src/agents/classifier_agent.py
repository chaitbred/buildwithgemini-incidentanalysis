from typing import Optional
from src.models.incident import RawIncident, TechStackSignature, EnrichedIncident
from src.agents.base_llm import LLMClient

EXTRACTION_PROMPT = """
You are an expert Autonomous Site Reliability & Incident Investigator.
Analyze the following incident title, description, and resolution notes.
Extract a structured multi-technology technical signature.

Incident Title: {title}
Severity: {severity}
Description: {description}
Resolution Notes: {resolution}

Instructions:
1. Identify all technologies/tools mentioned or involved (e.g., Kubernetes, JVM, PostgreSQL, pgbouncer, Envoy, Docker, Kafka).
2. Pinpoint the primary technology where the fault originated or manifested.
3. Identify the specific subcomponent (e.g. 'CoreDNS', 'Kubelet', 'Connection Pool', 'Consumer Group', 'Shard Allocation').
4. Identify the precise failure mechanism (e.g. 'OOMKilled', 'Connection Pool Starvation', 'Replication Lag Saturation', '504 Gateway Timeout').
5. Map to a high-level Root Cause Domain (e.g. 'Infrastructure & Runtime', 'Database & Storage', 'Messaging & Streaming', 'Networking & Ingress', 'Security & Cloud Access').
6. Classify the Resolution Pattern (e.g. 'Resource Limit Tuning', 'Index & Query Optimization', 'Canary Rollback', 'Scaling & Circuit Breaking').
7. Provide a concise summary insight.
"""

class MultiTechClassifierAgent:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    def classify_incident(self, incident: RawIncident) -> TechStackSignature:
        prompt = EXTRACTION_PROMPT.format(
            title=incident.title,
            severity=incident.severity or "Unknown",
            description=incident.description,
            resolution=incident.resolution or "No resolution provided"
        )
        signature = self.llm.generate_structured(prompt, TechStackSignature)
        return signature
