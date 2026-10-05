from typing import Optional
from src.models.incident import RawIncident, TechStackSignature
from src.agents.base_llm import LLMClient

# Hard caps prevent runaway token usage on verbose incident descriptions.
# P99 useful signal fits within these bounds; excess text is noise.
_MAX_DESC_CHARS = 1200
_MAX_RES_CHARS = 600

# Compact prompt — field names in the response schema already define the
# extraction targets, so verbose per-field instructions are redundant.
EXTRACTION_PROMPT = """\
SRE incident analyst. Extract a structured JSON tech-stack signature from the incident below.

Title: {title} | Severity: {severity}
Description: {description}
Resolution: {resolution}
{hint_section}
Return all fields: technologies (list), primary_technology, component, \
failure_mechanism, root_cause_domain, resolution_pattern, confidence (0-1), summary_insight."""


class MultiTechClassifierAgent:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    def classify_incident(
        self,
        incident: RawIncident,
        classification_hint: str = "",
    ) -> TechStackSignature:
        """
        Classify an incident into a TechStackSignature.

        *classification_hint* is extra domain context from project settings,
        appended to the prompt when non-empty (e.g. 'This is a fintech platform.
        Prioritise PCI-DSS and payment-related failure modes.').
        """
        hint_section = (
            f"Project context: {classification_hint.strip()}"
            if classification_hint and classification_hint.strip()
            else ""
        )
        desc = incident.description[:_MAX_DESC_CHARS]
        res = (incident.resolution or "No resolution provided")[:_MAX_RES_CHARS]
        prompt = EXTRACTION_PROMPT.format(
            title=incident.title,
            severity=incident.severity or "Unknown",
            description=desc,
            resolution=res,
            hint_section=hint_section,
        )
        return self.llm.generate_structured(prompt, TechStackSignature)
