# My agent: Incident Analyst & Dynamic Taxonomy Miner
One-liner: A conversational site reliability agent that helps SRE and DevOps engineers classify production incidents, trace failure mechanisms, and explore an evolving multi-technology ontology with a catalog of incident records and remediation runbooks.

Tool coverage:
- Memory: Remembers on-call user preferences, recently investigated incidents, and ongoing troubleshooting context.
- Tools: Classify and record incidents, fetch live service health/logs, search remediation runbooks, query Firestore incidents catalog.
- Catalog/UI: Catalog of incident post-mortems and multi-technology failure taxonomy nodes rendered as interactive cards/tables.
- Image gen: Architecture fault topology diagrams or incident severity summary visual cards.
- Sandbox: Python code sandbox for log rate parsing, MTTR metric calculations, and failure clustering.

Recommended for every project: memory, storage, tools, image generation, A2UI
Agent-specific / stretch (pick what fits): Cloud Logging / Monitoring API, Code sandbox for incident time-series statistics, GitHub/Jira webhook integration
