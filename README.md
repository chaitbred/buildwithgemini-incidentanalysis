# Autonomous Incident Classifier & Dynamic Taxonomy Miner

An agentic application designed to autonomously classify multi-technology production incidents, induce and evolve a dynamic hierarchical taxonomy, and provide rich batch analytics.

## Key Capabilities

1. **Multi-Technology Fingerprinting**: Identifies multi-layer footprints across Kubernetes, PostgreSQL, Kafka, Redis, Envoy, AWS IAM, RabbitMQ, and more.
2. **Dynamic Taxonomy Generation**: Eliminates rigid static categories. The ontology agent dynamically constructs and maintains a 4-tier living hierarchy:
   $$\text{Root} \longrightarrow \text{Domain} \longrightarrow \text{Technology} \longrightarrow \text{Component} \longrightarrow \text{Failure Mode}$$
3. **Firestore Cloud Backend**: Persists all enriched incident logs and dynamic ontology states to Google Cloud Firestore (`incidents` and `taxonomy` collections).
4. **Action & Diagnostic Tools**:
   - `search_remediation_runbooks`: Immediate CLI commands, diagnostic steps, and verification procedures for SREs.
   - `generate_postmortem_markdown_report`: Autonomously generates standardized Post-Mortem RCA reports (`reports/postmortem_*.md`).
5. **Batch Mining & Streaming Ingestion**: Ingests bulk CSV and Excel (`.xlsx`, `.xls`) files of any volume without memory bottlenecks.
6. **Interactive Analytics Dashboard & ADK Playground**: Visual tree explorer, distribution charts, and ADK Playground web UI.

## Architecture

- **`src/models/`**: Pydantic schemas for `RawIncident`, `TechStackSignature`, and `DynamicTaxonomyTree`.
- **`src/agents/`**:
  - `classifier_agent.py`: Multi-tech classification with structured LLM output (Gemini 2.5 Flash / fallback).
  - `taxonomy_agent.py`: Dynamic ontology evolution and deduplication.
  - `base_llm.py`: Unified LLM client supporting Google GenAI and fallback heuristics.
- **`src/pipeline/`**: Batch streaming processor for historical ticket repositories.
- **`src/web/app.py`**: FastAPI server powering the interactive web dashboard and REST API.

## Quick Start

### 1. Run the Web Dashboard
```bash
uvicorn src.web.app:app --host 0.0.0.0 --port 8000 --reload
```
Open your browser at `http://localhost:8000`.

### 2. Run Batch Ingestion via CLI
```bash
python3 -c "
from src.agents.classifier_agent import MultiTechClassifierAgent
from src.agents.taxonomy_agent import DynamicTaxonomyAgent
from src.pipeline.batch_processor import IncidentBatchProcessor

classifier = MultiTechClassifierAgent()
tax_agent = DynamicTaxonomyAgent()
processor = IncidentBatchProcessor(classifier, tax_agent)
processor.process_csv('data/sample_incidents.csv')
"
```

### 3. Run Test Suite
```bash
python3 -m unittest tests/test_pipeline.py
```
