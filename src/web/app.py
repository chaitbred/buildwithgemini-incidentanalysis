import os
import json
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from src.models.incident import RawIncident, EnrichedIncident
from src.models.project import Project, ProjectSettings
from src.agents.classifier_agent import MultiTechClassifierAgent
from src.agents.taxonomy_agent import DynamicTaxonomyAgent
from src.pipeline.batch_processor import IncidentBatchProcessor

app = FastAPI(title="Autonomous Incident Classifier & Dynamic Taxonomy Miner")

DATA_DIR = os.path.abspath("data")
# Legacy paths kept for backward-compat reads of the "default" project
_LEGACY_ENRICHED = os.path.join(DATA_DIR, "enriched_incidents.json")
_LEGACY_TAXONOMY = os.path.join(DATA_DIR, "dynamic_taxonomy.json")

# ---------------------------------------------------------------------------
# Shared (stateless) classifier
# ---------------------------------------------------------------------------
_classifier = MultiTechClassifierAgent()

# ---------------------------------------------------------------------------
# Per-project path helpers
# ---------------------------------------------------------------------------

def _project_dir(project_id: str) -> str:
    return os.path.join(DATA_DIR, "projects", project_id)


def _enriched_path(project_id: str) -> str:
    return os.path.join(_project_dir(project_id), "enriched.json")


def _taxonomy_storage_path(project_id: str) -> str:
    return os.path.join(_project_dir(project_id), "taxonomy.json")


def _meta_path(project_id: str) -> str:
    return os.path.join(_project_dir(project_id), "meta.json")


def _ensure_project_dir(project_id: str) -> None:
    os.makedirs(_project_dir(project_id), exist_ok=True)


# ---------------------------------------------------------------------------
# Project registry (file-based; mirrors LocalStorage.project methods)
# ---------------------------------------------------------------------------

def _load_project_meta(project_id: str) -> Optional[Dict[str, Any]]:
    path = _meta_path(project_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_project_meta(project_data: Dict[str, Any]) -> None:
    pid = project_data["project_id"]
    _ensure_project_dir(pid)
    with open(_meta_path(pid), "w", encoding="utf-8") as f:
        json.dump(project_data, f, indent=2)


def _list_all_projects() -> List[Dict[str, Any]]:
    projects_root = os.path.join(DATA_DIR, "projects")
    if not os.path.exists(projects_root):
        return []
    results = []
    try:
        for entry in os.scandir(projects_root):
            if entry.is_dir():
                meta = _load_project_meta(entry.name)
                if meta:
                    results.append(meta)
    except Exception:
        pass
    results.sort(key=lambda p: p.get("created_at", ""))
    return results


def _ensure_default_project() -> None:
    if not _load_project_meta("default"):
        _save_project_meta({
            "project_id": "default",
            "name": "Default Project",
            "description": "Auto-created default workspace",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "settings": {
                "classification_hint": "",
                "default_severity": "SEV-3",
                "custom_taxonomy_seed": None,
            },
        })


_ensure_default_project()

# ---------------------------------------------------------------------------
# Per-project taxonomy agent cache
# ---------------------------------------------------------------------------
_taxonomy_agents: Dict[str, DynamicTaxonomyAgent] = {}


def _get_taxonomy_agent(project_id: str) -> DynamicTaxonomyAgent:
    if project_id not in _taxonomy_agents:
        path = _taxonomy_storage_path(project_id)
        # Backward compat: seed from legacy file if this is the default project
        if project_id == "default" and not os.path.exists(path) and os.path.exists(_LEGACY_TAXONOMY):
            _ensure_project_dir(project_id)
            shutil.copy2(_LEGACY_TAXONOMY, path)
        agent = DynamicTaxonomyAgent(storage_path=path)
        _taxonomy_agents[project_id] = agent
    return _taxonomy_agents[project_id]


# ---------------------------------------------------------------------------
# Per-project enriched incidents helpers
# ---------------------------------------------------------------------------

def _load_enriched(project_id: str) -> List[Dict[str, Any]]:
    path = _enriched_path(project_id)
    # Backward compat: check legacy file for the default project
    if not os.path.exists(path) and project_id == "default" and os.path.exists(_LEGACY_ENRICHED):
        path = _LEGACY_ENRICHED
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_enriched(project_id: str, records: List[Dict[str, Any]]) -> None:
    path = _enriched_path(project_id)
    _ensure_project_dir(project_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)


def _append_enriched(project_id: str, new_record: Dict[str, Any]) -> None:
    current = _load_enriched(project_id)
    current.append(new_record)
    _save_enriched(project_id, current)


# ---------------------------------------------------------------------------
# Project settings helper
# ---------------------------------------------------------------------------

def _get_project_settings(project_id: str) -> Dict[str, Any]:
    meta = _load_project_meta(project_id)
    if meta:
        return meta.get("settings", {})
    return {}


# ===========================================================================
# API — Project management
# ===========================================================================

@app.get("/api/projects")
def list_projects():
    projects = _list_all_projects()
    if not projects:
        _ensure_default_project()
        projects = _list_all_projects()
    return JSONResponse(content=projects)


@app.post("/api/projects")
async def create_project(request: Request):
    """
    Create a new project workspace.

    Body (JSON):
      {
        "project_id": "acme-corp",          # URL-safe slug
        "name": "Acme Corp",
        "description": "...",               # optional
        "settings": {
          "classification_hint": "...",     # optional extra LLM context
          "default_severity": "SEV-2",      # optional
          "custom_taxonomy_seed": {...}     # optional ontology dict
        }
      }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    project_id = body.get("project_id", "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    if not re.match(r'^[a-z0-9][a-z0-9\-_]{0,62}$', project_id):
        raise HTTPException(
            status_code=400,
            detail="project_id must be lowercase alphanumeric with hyphens/underscores, max 63 chars",
        )
    if _load_project_meta(project_id):
        raise HTTPException(status_code=409, detail=f"Project '{project_id}' already exists")

    settings_raw = body.get("settings", {})
    project_data = {
        "project_id": project_id,
        "name": body.get("name", project_id),
        "description": body.get("description", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "settings": {
            "classification_hint": settings_raw.get("classification_hint", ""),
            "default_severity": settings_raw.get("default_severity", "SEV-3"),
            "custom_taxonomy_seed": settings_raw.get("custom_taxonomy_seed"),
        },
    }
    _save_project_meta(project_data)

    # Auto-import custom taxonomy seed if provided
    seed = project_data["settings"].get("custom_taxonomy_seed")
    if seed:
        try:
            agent = _get_taxonomy_agent(project_id)
            agent.import_custom_ontology(seed)
        except Exception as e:
            print(f"[app] Failed to seed taxonomy for project {project_id}: {e}")

    return JSONResponse(content=project_data, status_code=201)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    meta = _load_project_meta(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return JSONResponse(content=meta)


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: str, request: Request):
    """Update a project's name, description, or settings (partial update)."""
    meta = _load_project_meta(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if "name" in body:
        meta["name"] = body["name"]
    if "description" in body:
        meta["description"] = body["description"]
    if "settings" in body:
        current_settings = meta.get("settings", {})
        current_settings.update(body["settings"])
        meta["settings"] = current_settings
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_project_meta(meta)

    # Evict cached taxonomy agent so it picks up any new seed on next use
    _taxonomy_agents.pop(project_id, None)

    return JSONResponse(content=meta)


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    if project_id == "default":
        raise HTTPException(status_code=400, detail="The 'default' project cannot be deleted.")
    meta = _load_project_meta(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    pdir = _project_dir(project_id)
    shutil.rmtree(pdir, ignore_errors=True)
    _taxonomy_agents.pop(project_id, None)
    return JSONResponse(content={"status": "deleted", "project_id": project_id})


# ===========================================================================
# API — Taxonomy (project-scoped)
# ===========================================================================

@app.get("/api/taxonomy")
def get_taxonomy(project_id: str = Query(default="default")):
    agent = _get_taxonomy_agent(project_id)
    agent.load()
    tree_dict = agent.tree.to_hierarchical_dict()
    return JSONResponse(content={
        "tree": tree_dict,
        "total_nodes": len(agent.tree.nodes),
        "project_id": project_id,
    })


@app.post("/api/taxonomy/custom")
async def add_custom_ontology(request: Request, project_id: str = Query(default="default")):
    """Import a custom taxonomy/ontology definition into the project's tree."""
    try:
        payload = await request.json()
        agent = _get_taxonomy_agent(project_id)
        added_nodes = agent.import_custom_ontology(payload)
        return JSONResponse(content={
            "status": "success",
            "message": f"Successfully imported custom ontology ({added_nodes} nodes registered/updated).",
            "total_nodes": len(agent.tree.nodes),
            "project_id": project_id,
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to import ontology: {str(e)}")


# ===========================================================================
# API — Incidents (project-scoped)
# ===========================================================================

@app.get("/api/incidents")
def get_incidents(project_id: str = Query(default="default")):
    return JSONResponse(content=_load_enriched(project_id))


@app.get("/api/analytics")
def get_analytics(project_id: str = Query(default="default")):
    incidents = _load_enriched(project_id)
    tech_counts: Dict[str, int] = {}
    domain_counts: Dict[str, int] = {}
    failure_counts: Dict[str, int] = {}
    resolution_counts: Dict[str, int] = {}

    for inc in incidents:
        cls = inc.get("classification", {})
        prim = cls.get("primary_technology", "Unknown")
        domain = cls.get("root_cause_domain", "Unknown")
        fail = cls.get("failure_mechanism", "Unknown")
        pattern = cls.get("resolution_pattern", "Unknown")

        tech_counts[prim] = tech_counts.get(prim, 0) + 1
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        failure_counts[fail] = failure_counts.get(fail, 0) + 1
        resolution_counts[pattern] = resolution_counts.get(pattern, 0) + 1

    return JSONResponse(content={
        "total_incidents": len(incidents),
        "project_id": project_id,
        "by_technology": sorted([{"name": k, "count": v} for k, v in tech_counts.items()], key=lambda x: x["count"], reverse=True),
        "by_domain": sorted([{"name": k, "count": v} for k, v in domain_counts.items()], key=lambda x: x["count"], reverse=True),
        "by_failure": sorted([{"name": k, "count": v} for k, v in failure_counts.items()], key=lambda x: x["count"], reverse=True),
        "by_resolution": sorted([{"name": k, "count": v} for k, v in resolution_counts.items()], key=lambda x: x["count"], reverse=True),
    })


@app.post("/api/classify_single")
def classify_single(
    title: str = Form(...),
    description: str = Form(...),
    resolution: str = Form(""),
    severity: str = Form("SEV-3"),
    project_id: str = Form(default="default"),
):
    settings = _get_project_settings(project_id)
    hint = settings.get("classification_hint", "")

    inc_id = f"ADHOC-{str(uuid.uuid4())[:6].upper()}"
    raw = RawIncident(
        incident_id=inc_id,
        title=title,
        description=description,
        resolution=resolution,
        severity=severity,
    )
    sig = _classifier.classify_incident(raw, classification_hint=hint)
    agent = _get_taxonomy_agent(project_id)
    path = agent.evolve_taxonomy(raw, sig)
    agent.save()

    enriched = EnrichedIncident(incident=raw, classification=sig, taxonomy_path=path)
    _append_enriched(project_id, enriched.model_dump())

    return JSONResponse(content=enriched.model_dump())


@app.post("/api/upload_file")
async def upload_file(
    file: UploadFile = File(...),
    project_id: str = Form(default="default"),
):
    """
    Accepts Excel (.xlsx/.xls) or CSV files of any volume.
    Streams through chunks and classifies incidents autonomously.
    """
    filename = file.filename or "upload.csv"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".csv", ".xlsx", ".xls"]:
        raise HTTPException(status_code=400, detail="Only .csv and .xlsx/.xls files are supported.")

    settings = _get_project_settings(project_id)
    hint = settings.get("classification_hint", "")

    agent = _get_taxonomy_agent(project_id)
    processor = IncidentBatchProcessor(
        classifier=_classifier,
        taxonomy_agent=agent,
        output_enriched_path=_enriched_path(project_id),
        classification_hint=hint,
    )

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        results = processor.process_file_stream(tmp_path)
        return JSONResponse(content={
            "status": "success",
            "filename": filename,
            "processed_count": len(results),
            "total_taxonomy_nodes": len(agent.tree.nodes),
            "project_id": project_id,
        })
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/api/run_batch")
def run_batch(project_id: str = Query(default="default")):
    sample_csv = os.path.join(DATA_DIR, "sample_incidents.csv")
    if not os.path.exists(sample_csv):
        return JSONResponse(status_code=404, content={"error": "sample_incidents.csv not found"})

    settings = _get_project_settings(project_id)
    hint = settings.get("classification_hint", "")

    agent = _get_taxonomy_agent(project_id)
    processor = IncidentBatchProcessor(
        classifier=_classifier,
        taxonomy_agent=agent,
        output_enriched_path=_enriched_path(project_id),
        classification_hint=hint,
    )
    results = processor.process_csv(sample_csv)
    return JSONResponse(content={
        "status": "success",
        "processed_count": len(results),
        "project_id": project_id,
    })


# ===========================================================================
# Frontend (single-page app with project switcher)
# ===========================================================================

@app.get("/", response_class=HTMLResponse)
def index_page():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Incident Taxonomy & Knowledge Miner</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; }
  </style>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen">

  <!-- Header -->
  <header class="border-b border-slate-800 bg-slate-950/80 backdrop-blur px-6 py-3 flex items-center justify-between sticky top-0 z-50">
    <div class="flex items-center gap-3">
      <div class="h-9 w-9 bg-indigo-600 rounded-lg flex items-center justify-center font-bold text-lg shadow-lg shadow-indigo-500/30">AI</div>
      <div>
        <h1 class="text-base font-bold tracking-tight text-white">Incident Taxonomy & Knowledge Miner</h1>
        <p class="text-xs text-slate-400">Multi-Tech Classification & Custom Ontology Engine</p>
      </div>
    </div>
    <!-- Project selector -->
    <div class="flex items-center gap-2 mx-4 flex-1 max-w-xs">
      <svg class="w-4 h-4 text-slate-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>
      <select id="projectSelector" onchange="switchProject(this.value)"
              class="flex-1 bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded-lg px-2 py-1.5 focus:outline-none focus:border-indigo-500">
        <option value="default">Default Project</option>
      </select>
      <button onclick="openNewProjectModal()" title="New project"
              class="bg-slate-700 hover:bg-slate-600 text-white text-xs font-semibold px-2 py-1.5 rounded-lg transition flex items-center gap-1">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
        New
      </button>
    </div>
    <!-- Actions -->
    <div class="flex items-center gap-2">
      <button onclick="openOntologyModal()" class="bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold px-3 py-2 rounded-lg transition flex items-center gap-1.5">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
        Custom Ontology
      </button>
      <label class="cursor-pointer bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold px-3 py-2 rounded-lg transition flex items-center gap-1.5">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
        <span id="uploadLabel">Upload Excel / CSV</span>
        <input type="file" id="fileUploader" accept=".xlsx,.xls,.csv" onchange="uploadDataset(event)" class="hidden">
      </label>
      <button onclick="triggerBatchRun()" id="batchBtn" class="bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold px-3 py-2 rounded-lg transition flex items-center gap-1.5">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        Sample Batch
      </button>
      <button onclick="openModal()" class="bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold px-3 py-2 rounded-lg transition flex items-center gap-1.5">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
        Classify Ad-Hoc
      </button>
    </div>
  </header>

  <!-- Toast -->
  <div id="toast" class="hidden fixed top-16 right-6 z-50 bg-slate-800 border border-slate-700 text-slate-200 px-4 py-3 rounded-lg shadow-xl text-xs max-w-sm flex items-center gap-2">
    <div id="toastDot" class="w-2.5 h-2.5 rounded-full bg-emerald-400"></div>
    <span id="toastMsg">Done.</span>
  </div>

  <!-- Main -->
  <main class="max-w-7xl mx-auto p-6 space-y-6">
    <!-- Project info bar -->
    <div id="projectInfoBar" class="bg-slate-800/50 border border-slate-700/50 rounded-xl px-5 py-3 flex items-center justify-between">
      <div>
        <span class="text-xs text-slate-400 uppercase tracking-wider font-medium">Current Project</span>
        <h2 id="projectName" class="text-base font-bold text-white mt-0.5">Default Project</h2>
        <p id="projectDesc" class="text-xs text-slate-400 mt-0.5"></p>
      </div>
      <div class="flex items-center gap-3">
        <button onclick="openEditProjectModal()" class="text-xs text-slate-400 hover:text-slate-200 transition underline underline-offset-2">Edit settings</button>
        <button id="deleteProjectBtn" onclick="confirmDeleteProject()" class="hidden text-xs text-rose-400 hover:text-rose-300 transition">Delete project</button>
      </div>
    </div>

    <!-- Stat Cards -->
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div class="bg-slate-800/70 border border-slate-700/60 rounded-xl p-4">
        <p class="text-xs uppercase tracking-wider text-slate-400 font-medium">Total Incidents</p>
        <p id="statTotal" class="text-2xl font-bold text-white mt-1">0</p>
      </div>
      <div class="bg-slate-800/70 border border-slate-700/60 rounded-xl p-4">
        <p class="text-xs uppercase tracking-wider text-slate-400 font-medium">Distinct Tech Stacks</p>
        <p id="statTechs" class="text-2xl font-bold text-indigo-400 mt-1">0</p>
      </div>
      <div class="bg-slate-800/70 border border-slate-700/60 rounded-xl p-4">
        <p class="text-xs uppercase tracking-wider text-slate-400 font-medium">Root Domains</p>
        <p id="statDomains" class="text-2xl font-bold text-emerald-400 mt-1">0</p>
      </div>
      <div class="bg-slate-800/70 border border-slate-700/60 rounded-xl p-4">
        <p class="text-xs uppercase tracking-wider text-slate-400 font-medium">Taxonomy Nodes</p>
        <p id="statNodes" class="text-2xl font-bold text-amber-400 mt-1">0</p>
      </div>
    </div>

    <!-- Charts -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div class="bg-slate-800/70 border border-slate-700/60 rounded-xl p-5">
        <h2 class="text-sm font-semibold text-slate-200 mb-3">Technology Distribution</h2>
        <div class="h-64"><canvas id="techChart"></canvas></div>
      </div>
      <div class="bg-slate-800/70 border border-slate-700/60 rounded-xl p-5">
        <h2 class="text-sm font-semibold text-slate-200 mb-3">Domain Breakdown</h2>
        <div class="h-64"><canvas id="domainChart"></canvas></div>
      </div>
    </div>

    <!-- Taxonomy Tree -->
    <div class="bg-slate-800/70 border border-slate-700/60 rounded-xl p-5">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-base font-semibold text-white">Dynamic Hierarchical Ontology Tree</h2>
          <p class="text-xs text-slate-400">Living multi-tier hierarchy: Root &rarr; Domain &rarr; Technology &rarr; Component &rarr; Failure Mode <span class="text-amber-400">(custom nodes highlighted)</span></p>
        </div>
        <span class="text-xs text-slate-500" id="treeStatus">Updated live</span>
      </div>
      <div id="taxonomyContainer" class="space-y-1.5 font-mono text-xs overflow-x-auto max-h-96 p-4 bg-slate-950/80 rounded-lg border border-slate-800"></div>
    </div>

    <!-- Incident Table -->
    <div class="bg-slate-800/70 border border-slate-700/60 rounded-xl p-5">
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4">
        <div>
          <h2 class="text-base font-semibold text-white">Enriched Incident Records</h2>
          <p class="text-xs text-slate-400">Classified fingerprints, root cause mechanisms, and resolutions</p>
        </div>
        <input type="text" id="searchInput" oninput="filterIncidents()" placeholder="Search tech, component, symptom..."
               class="bg-slate-900 border border-slate-700 text-xs text-slate-200 px-3 py-2 rounded-lg focus:outline-none focus:border-indigo-500 w-full md:w-64">
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="border-b border-slate-700/70 text-slate-400 uppercase tracking-wider">
              <th class="py-3 px-3">ID</th>
              <th class="py-3 px-3">Title & Summary</th>
              <th class="py-3 px-3">Primary Tech</th>
              <th class="py-3 px-3">Failure Mechanism</th>
              <th class="py-3 px-3">Remediation Pattern</th>
              <th class="py-3 px-3 text-right">Taxonomy Path</th>
            </tr>
          </thead>
          <tbody id="incidentsTableBody" class="divide-y divide-slate-800 text-slate-300"></tbody>
        </table>
      </div>
    </div>
  </main>

  <!-- Modal: New Project -->
  <div id="newProjectModal" class="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center hidden p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-xl max-w-lg w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div>
          <h3 class="text-sm font-bold text-white">Create New Project</h3>
          <p class="text-[11px] text-slate-400">Each project has its own incident store, taxonomy, and classification settings</p>
        </div>
        <button onclick="closeNewProjectModal()" class="text-slate-400 hover:text-white">&times;</button>
      </div>
      <form onsubmit="submitNewProject(event)" class="space-y-3 text-xs">
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-slate-300 font-medium mb-1">Project ID <span class="text-rose-400">*</span></label>
            <input type="text" id="newProjId" required pattern="[a-z0-9][a-z0-9\\-_]{0,62}"
                   placeholder="acme-corp"
                   class="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500 font-mono">
            <p class="text-[10px] text-slate-500 mt-0.5">Lowercase, hyphens/underscores only</p>
          </div>
          <div>
            <label class="block text-slate-300 font-medium mb-1">Display Name <span class="text-rose-400">*</span></label>
            <input type="text" id="newProjName" required placeholder="Acme Corp SRE"
                   class="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500">
          </div>
        </div>
        <div>
          <label class="block text-slate-300 font-medium mb-1">Description</label>
          <input type="text" id="newProjDesc" placeholder="Incidents for the Acme Corp platform..."
                 class="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500">
        </div>
        <div>
          <label class="block text-slate-300 font-medium mb-1">Classification Hint (optional)</label>
          <textarea id="newProjHint" rows="2"
                    placeholder="e.g. 'This is a fintech platform. Prioritise payment gateway and PCI-DSS failure modes.'"
                    class="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500"></textarea>
          <p class="text-[10px] text-slate-500 mt-0.5">Extra context appended to every LLM classification prompt for this project.</p>
        </div>
        <div>
          <label class="block text-slate-300 font-medium mb-1">Default Severity</label>
          <select id="newProjSeverity" class="bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500">
            <option>SEV-1</option><option>SEV-2</option><option selected>SEV-3</option><option>SEV-4</option>
          </select>
        </div>
        <div>
          <label class="block text-slate-300 font-medium mb-1">Custom Taxonomy Seed (JSON, optional)</label>
          <textarea id="newProjSeed" rows="4" placeholder='{"Domain": {"Technology": ["Component1", "Component2"]}}'
                    class="w-full font-mono bg-slate-950 border border-slate-700 rounded p-2 text-indigo-300 focus:outline-none focus:border-indigo-500 text-[11px]"></textarea>
          <p class="text-[10px] text-slate-500 mt-0.5">Auto-imported into this project's taxonomy on creation.</p>
        </div>
        <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
          <button type="button" onclick="closeNewProjectModal()" class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Cancel</button>
          <button type="submit" id="createProjBtn" class="px-4 py-1.5 rounded bg-indigo-600 hover:bg-indigo-500 text-white font-semibold">Create Project</button>
        </div>
      </form>
    </div>
  </div>

  <!-- Modal: Edit Project -->
  <div id="editProjectModal" class="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center hidden p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-xl max-w-lg w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h3 class="text-sm font-bold text-white">Edit Project Settings</h3>
        <button onclick="closeEditProjectModal()" class="text-slate-400 hover:text-white">&times;</button>
      </div>
      <form onsubmit="submitEditProject(event)" class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-300 font-medium mb-1">Display Name</label>
          <input type="text" id="editProjName" required
                 class="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500">
        </div>
        <div>
          <label class="block text-slate-300 font-medium mb-1">Description</label>
          <input type="text" id="editProjDesc"
                 class="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500">
        </div>
        <div>
          <label class="block text-slate-300 font-medium mb-1">Classification Hint</label>
          <textarea id="editProjHint" rows="2"
                    class="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500"></textarea>
        </div>
        <div>
          <label class="block text-slate-300 font-medium mb-1">Default Severity</label>
          <select id="editProjSeverity" class="bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500">
            <option>SEV-1</option><option>SEV-2</option><option>SEV-3</option><option>SEV-4</option>
          </select>
        </div>
        <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
          <button type="button" onclick="closeEditProjectModal()" class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Cancel</button>
          <button type="submit" id="editProjBtn" class="px-4 py-1.5 rounded bg-indigo-600 hover:bg-indigo-500 text-white font-semibold">Save Changes</button>
        </div>
      </form>
    </div>
  </div>

  <!-- Modal: Custom Ontology -->
  <div id="ontologyModal" class="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center hidden p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-xl max-w-xl w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div>
          <h3 class="text-sm font-bold text-white">Add Custom Ontology</h3>
          <p class="text-[11px] text-slate-400">Seed custom domains, technologies, components, or failure modes into this project's taxonomy</p>
        </div>
        <button onclick="closeOntologyModal()" class="text-slate-400 hover:text-white">&times;</button>
      </div>
      <form onsubmit="submitCustomOntology(event)" class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-300 font-medium mb-1">Ontology Definition (JSON)</label>
          <textarea id="ontologyInput" rows="9" required class="w-full font-mono bg-slate-950 border border-slate-700 rounded p-2.5 text-indigo-300 focus:outline-none focus:border-indigo-500 text-[11px]"></textarea>
          <p class="text-[10px] text-slate-500 mt-1">Accepts nested domain-technology-component trees or list of path objects.</p>
        </div>
        <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
          <button type="button" onclick="loadSampleOntology()" class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Insert Sample Template</button>
          <button type="submit" id="ontoSubmitBtn" class="px-4 py-1.5 rounded bg-purple-600 hover:bg-purple-500 text-white font-semibold">Import Ontology</button>
        </div>
      </form>
    </div>
  </div>

  <!-- Modal: Ad-Hoc Classify -->
  <div id="modal" class="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center hidden p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-xl max-w-xl w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div>
          <h3 class="text-sm font-bold text-white">Classify New Incident</h3>
          <p id="classifyProjectLabel" class="text-[11px] text-slate-400"></p>
        </div>
        <button onclick="closeModal()" class="text-slate-400 hover:text-white">&times;</button>
      </div>
      <form id="classifyForm" onsubmit="submitAdhoc(event)" class="space-y-3 text-xs">
        <input type="hidden" id="classifyProjectId" name="project_id" value="default">
        <div>
          <label class="block text-slate-300 font-medium mb-1">Incident Title</label>
          <input type="text" name="title" required placeholder="e.g. Redis primary high memory eviction"
                 class="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500">
        </div>
        <div>
          <label class="block text-slate-300 font-medium mb-1">Description / Error Logs</label>
          <textarea name="description" rows="3" required placeholder="Paste error messages, logs, symptoms..."
                    class="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500"></textarea>
        </div>
        <div>
          <label class="block text-slate-300 font-medium mb-1">Resolution / Remediation Action (Optional)</label>
          <textarea name="resolution" rows="2" placeholder="What fixed it? (e.g. Scaled replicas, added index, restarted pod)..."
                    class="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-indigo-500"></textarea>
        </div>
        <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
          <button type="button" onclick="closeModal()" class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Cancel</button>
          <button type="submit" id="submitBtn" class="px-4 py-1.5 rounded bg-indigo-600 hover:bg-indigo-500 text-white font-semibold">Run Agent Classification</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    let allIncidents = [];
    let techChart = null;
    let domainChart = null;
    let currentProjectId = 'default';
    let currentProject = null;

    // -----------------------------------------------------------------------
    // Project management
    // -----------------------------------------------------------------------

    async function loadProjects() {
      try {
        const res = await fetch('/api/projects');
        const projects = await res.json();
        const sel = document.getElementById('projectSelector');
        sel.innerHTML = '';
        projects.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.project_id;
          opt.textContent = p.name || p.project_id;
          if (p.project_id === currentProjectId) opt.selected = true;
          sel.appendChild(opt);
        });
        // Refresh current project info
        const cur = projects.find(p => p.project_id === currentProjectId) || projects[0];
        if (cur) updateProjectInfoBar(cur);
      } catch (err) {
        console.error('Failed to load projects:', err);
      }
    }

    function updateProjectInfoBar(project) {
      currentProject = project;
      document.getElementById('projectName').innerText = project.name || project.project_id;
      document.getElementById('projectDesc').innerText = project.description || '';
      const deleteBtn = document.getElementById('deleteProjectBtn');
      if (project.project_id === 'default') {
        deleteBtn.classList.add('hidden');
      } else {
        deleteBtn.classList.remove('hidden');
      }
    }

    function switchProject(projectId) {
      currentProjectId = projectId;
      loadData();
      // Update current project info bar
      fetch('/api/projects/' + projectId)
        .then(r => r.json())
        .then(p => updateProjectInfoBar(p))
        .catch(() => {});
    }

    // New project modal
    function openNewProjectModal() {
      document.getElementById('newProjectModal').classList.remove('hidden');
    }
    function closeNewProjectModal() {
      document.getElementById('newProjectModal').classList.add('hidden');
    }

    async function submitNewProject(e) {
      e.preventDefault();
      const btn = document.getElementById('createProjBtn');
      btn.innerText = 'Creating...';
      btn.disabled = true;

      const project_id = document.getElementById('newProjId').value.trim();
      const name = document.getElementById('newProjName').value.trim();
      const description = document.getElementById('newProjDesc').value.trim();
      const hint = document.getElementById('newProjHint').value.trim();
      const severity = document.getElementById('newProjSeverity').value;
      const seedText = document.getElementById('newProjSeed').value.trim();

      let custom_taxonomy_seed = null;
      if (seedText) {
        try { custom_taxonomy_seed = JSON.parse(seedText); }
        catch { showToast('Invalid JSON in taxonomy seed', true); btn.innerText = 'Create Project'; btn.disabled = false; return; }
      }

      try {
        const res = await fetch('/api/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_id, name, description,
            settings: { classification_hint: hint, default_severity: severity, custom_taxonomy_seed }
          })
        });
        const data = await res.json();
        if (!res.ok) { showToast(data.detail || 'Failed to create project', true); return; }

        closeNewProjectModal();
        currentProjectId = project_id;
        await loadProjects();
        await loadData();
        document.getElementById('projectSelector').value = project_id;
        showToast(`Project "${name}" created!`);
        // Reset form
        e.target.reset();
      } catch (err) {
        showToast('Error: ' + err, true);
      } finally {
        btn.innerText = 'Create Project';
        btn.disabled = false;
      }
    }

    // Edit project modal
    function openEditProjectModal() {
      if (!currentProject) return;
      document.getElementById('editProjName').value = currentProject.name || '';
      document.getElementById('editProjDesc').value = currentProject.description || '';
      const s = currentProject.settings || {};
      document.getElementById('editProjHint').value = s.classification_hint || '';
      document.getElementById('editProjSeverity').value = s.default_severity || 'SEV-3';
      document.getElementById('editProjectModal').classList.remove('hidden');
    }
    function closeEditProjectModal() {
      document.getElementById('editProjectModal').classList.add('hidden');
    }

    async function submitEditProject(e) {
      e.preventDefault();
      const btn = document.getElementById('editProjBtn');
      btn.innerText = 'Saving...';
      btn.disabled = true;
      try {
        const res = await fetch('/api/projects/' + currentProjectId, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: document.getElementById('editProjName').value,
            description: document.getElementById('editProjDesc').value,
            settings: {
              classification_hint: document.getElementById('editProjHint').value,
              default_severity: document.getElementById('editProjSeverity').value,
            }
          })
        });
        if (res.ok) {
          const updated = await res.json();
          updateProjectInfoBar(updated);
          await loadProjects();
          closeEditProjectModal();
          showToast('Project settings saved!');
        } else {
          const data = await res.json();
          showToast(data.detail || 'Update failed', true);
        }
      } finally {
        btn.innerText = 'Save Changes';
        btn.disabled = false;
      }
    }

    async function confirmDeleteProject() {
      if (!currentProject || currentProject.project_id === 'default') return;
      if (!confirm(`Delete project "${currentProject.name}"? This will permanently remove all its incidents and taxonomy.`)) return;
      try {
        const res = await fetch('/api/projects/' + currentProjectId, { method: 'DELETE' });
        if (res.ok) {
          currentProjectId = 'default';
          await loadProjects();
          document.getElementById('projectSelector').value = 'default';
          await loadData();
          showToast('Project deleted.');
        }
      } catch (err) {
        showToast('Delete failed: ' + err, true);
      }
    }

    // -----------------------------------------------------------------------
    // Data loading
    // -----------------------------------------------------------------------

    async function loadData() {
      try {
        const pid = currentProjectId;
        const qs = '?project_id=' + encodeURIComponent(pid);
        const [incRes, taxRes, anaRes] = await Promise.all([
          fetch('/api/incidents' + qs),
          fetch('/api/taxonomy' + qs),
          fetch('/api/analytics' + qs)
        ]);

        allIncidents = await incRes.json();
        const taxonomyData = await taxRes.json();
        const analytics = await anaRes.json();

        document.getElementById('statTotal').innerText = analytics.total_incidents || 0;
        document.getElementById('statTechs').innerText = analytics.by_technology?.length || 0;
        document.getElementById('statDomains').innerText = analytics.by_domain?.length || 0;
        document.getElementById('statNodes').innerText = taxonomyData.total_nodes || 0;

        renderTaxonomyTree(taxonomyData.tree);
        renderTable(allIncidents);
        renderCharts(analytics);
      } catch (err) {
        console.error('Failed to load data:', err);
      }
    }

    // -----------------------------------------------------------------------
    // Rendering
    // -----------------------------------------------------------------------

    function renderTaxonomyTree(node) {
      const container = document.getElementById('taxonomyContainer');
      if (!node || !node.children || node.children.length === 0) {
        container.innerHTML = '<span class="text-slate-500 italic">No taxonomy nodes yet. Click "Sample Batch" or upload a dataset above.</span>';
        return;
      }
      container.innerHTML = buildTreeHTML(node, 0);
    }

    function buildTreeHTML(node, depth) {
      if (node.id === 'root') return node.children.map(c => buildTreeHTML(c, 0)).join('');
      const indent = depth * 16;
      let badgeColor = 'bg-slate-700 text-slate-200';
      if (node.level === 'Domain') badgeColor = 'bg-emerald-900/60 text-emerald-300 border border-emerald-700/50';
      if (node.level === 'Technology') badgeColor = 'bg-indigo-900/60 text-indigo-300 border border-indigo-700/50';
      if (node.level === 'Component') badgeColor = 'bg-purple-900/60 text-purple-300 border border-purple-700/50';
      if (node.level === 'FailureMode') badgeColor = 'bg-rose-900/60 text-rose-300 border border-rose-700/50';
      const customBadge = node.is_custom ? '<span class="badge bg-amber-900/60 text-amber-300 border border-amber-700/50 text-[9px]">Custom</span>' : '';
      let html = `
        <div style="margin-left:${indent}px" class="py-1 flex items-center gap-2 hover:bg-slate-900/60 rounded px-1 transition">
          <span class="text-slate-600">${depth > 0 ? '&bull;' : '&blacktriangleright;'}</span>
          <span class="badge ${badgeColor}">${node.level}</span>
          ${customBadge}
          <span class="text-slate-200 font-medium">${node.name}</span>
          <span class="text-slate-500 text-[10px]">(${node.count} incident${node.count === 1 ? '' : 's'})</span>
        </div>`;
      if (node.children?.length > 0) html += node.children.map(c => buildTreeHTML(c, depth + 1)).join('');
      return html;
    }

    function renderTable(incidents) {
      const tbody = document.getElementById('incidentsTableBody');
      if (incidents.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center py-6 text-slate-500">No incidents in this project yet.</td></tr>';
        return;
      }
      tbody.innerHTML = incidents.slice(-200).reverse().map(item => {
        const inc = item.incident;
        const cls = item.classification;
        const path = (item.taxonomy_path || []).join(' &rarr; ');
        return `
          <tr class="hover:bg-slate-800/40 transition">
            <td class="py-3 px-3 font-mono text-indigo-400 font-bold">${inc.incident_id}</td>
            <td class="py-3 px-3">
              <div class="font-semibold text-white">${inc.title}</div>
              <div class="text-slate-400 text-[11px] line-clamp-1 mt-0.5">${cls.summary_insight || inc.description}</div>
            </td>
            <td class="py-3 px-3"><span class="badge bg-indigo-900/50 text-indigo-300 border border-indigo-700/50">${cls.primary_technology}</span></td>
            <td class="py-3 px-3"><span class="badge bg-rose-900/50 text-rose-300 border border-rose-700/50">${cls.failure_mechanism}</span></td>
            <td class="py-3 px-3"><span class="badge bg-emerald-900/50 text-emerald-300 border border-emerald-700/50">${cls.resolution_pattern}</span></td>
            <td class="py-3 px-3 text-right font-mono text-[10px] text-slate-400">${path}</td>
          </tr>`;
      }).join('');
    }

    function renderCharts(analytics) {
      const techCtx = document.getElementById('techChart').getContext('2d');
      if (techChart) techChart.destroy();
      techChart = new Chart(techCtx, {
        type: 'bar',
        data: {
          labels: (analytics.by_technology || []).map(x => x.name),
          datasets: [{ data: (analytics.by_technology || []).map(x => x.count), backgroundColor: '#6366f1', borderRadius: 4 }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
          scales: { x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { display: false } },
                    y: { ticks: { color: '#94a3b8', stepSize: 1 }, grid: { color: '#334155' } } } }
      });
      const domCtx = document.getElementById('domainChart').getContext('2d');
      if (domainChart) domainChart.destroy();
      domainChart = new Chart(domCtx, {
        type: 'doughnut',
        data: {
          labels: (analytics.by_domain || []).map(x => x.name),
          datasets: [{ data: (analytics.by_domain || []).map(x => x.count),
            backgroundColor: ['#10b981','#3b82f6','#f59e0b','#ec4899','#8b5cf6','#14b8a6','#f97316'], borderWidth: 0 }]
        },
        options: { responsive: true, maintainAspectRatio: false,
          plugins: { legend: { position: 'right', labels: { color: '#cbd5e1', font: { size: 10 } } } } }
      });
    }

    function filterIncidents() {
      const q = document.getElementById('searchInput').value.toLowerCase();
      renderTable(allIncidents.filter(item => JSON.stringify(item).toLowerCase().includes(q)));
    }

    // -----------------------------------------------------------------------
    // Actions
    // -----------------------------------------------------------------------

    async function uploadDataset(event) {
      const file = event.target.files[0];
      if (!file) return;
      const label = document.getElementById('uploadLabel');
      const orig = label.innerText;
      label.innerText = 'Ingesting & Analyzing...';
      const formData = new FormData();
      formData.append('file', file);
      formData.append('project_id', currentProjectId);
      try {
        const res = await fetch('/api/upload_file', { method: 'POST', body: formData });
        const data = await res.json();
        if (res.ok) { showToast(`Ingested ${data.processed_count} incidents from ${data.filename}!`); await loadData(); }
        else showToast(data.detail || 'Upload error', true);
      } catch (err) { showToast('Upload failed: ' + err, true); }
      finally { label.innerText = orig; event.target.value = ''; }
    }

    async function triggerBatchRun() {
      const btn = document.getElementById('batchBtn');
      btn.innerText = 'Processing...';
      btn.disabled = true;
      try {
        await fetch('/api/run_batch?project_id=' + encodeURIComponent(currentProjectId), { method: 'POST' });
        showToast('Sample batch processed!');
        await loadData();
      } catch (err) { showToast('Batch failed: ' + err, true); }
      finally { btn.innerText = 'Sample Batch'; btn.disabled = false; }
    }

    function openOntologyModal() {
      document.getElementById('ontologyModal').classList.remove('hidden');
      if (!document.getElementById('ontologyInput').value) loadSampleOntology();
    }
    function closeOntologyModal() { document.getElementById('ontologyModal').classList.add('hidden'); }

    function loadSampleOntology() {
      const sample = {
        "Data & Streaming Infrastructure": {
          "ClickHouse": { "ZooKeeper KeeperClient": ["Session Expiration", "Connection Refused"],
                          "MergeTree Engine": ["Part Mutex Contention", "Max Parts Per Partition Exceeded"] },
          "Apache Flink": { "Checkpoint Coordinator": ["Checkpoint Barrier Alignment Timeout"],
                            "TaskExecutor": ["OOM Managed Memory Starvation"] }
        },
        "Security & Identity": {
          "HashiCorp Vault": { "Raft Storage": ["Consensus Lost", "Disk Latency Warning"],
                              "Transit Engine": ["Token Lease Expired"] }
        }
      };
      document.getElementById('ontologyInput').value = JSON.stringify(sample, null, 2);
    }

    async function submitCustomOntology(e) {
      e.preventDefault();
      const btn = document.getElementById('ontoSubmitBtn');
      btn.innerText = 'Importing...'; btn.disabled = true;
      try {
        const payload = JSON.parse(document.getElementById('ontologyInput').value);
        const res = await fetch('/api/taxonomy/custom?project_id=' + encodeURIComponent(currentProjectId), {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) { showToast(data.message); closeOntologyModal(); await loadData(); }
        else showToast(data.detail || 'Ontology import failed', true);
      } catch (err) { showToast('Invalid JSON: ' + err, true); }
      finally { btn.innerText = 'Import Ontology'; btn.disabled = false; }
    }

    function openModal() {
      document.getElementById('classifyProjectId').value = currentProjectId;
      document.getElementById('classifyProjectLabel').innerText =
        'Classifying into: ' + (currentProject?.name || currentProjectId);
      document.getElementById('modal').classList.remove('hidden');
    }
    function closeModal() { document.getElementById('modal').classList.add('hidden'); }

    async function submitAdhoc(e) {
      e.preventDefault();
      const form = document.getElementById('classifyForm');
      const btn = document.getElementById('submitBtn');
      btn.innerText = 'Analyzing...'; btn.disabled = true;
      try {
        await fetch('/api/classify_single', { method: 'POST', body: new FormData(form) });
        form.reset();
        form.querySelector('[name=project_id]').value = currentProjectId;
        closeModal();
        showToast('Incident classified and taxonomy updated!');
        await loadData();
      } catch (err) { showToast('Classification failed: ' + err, true); }
      finally { btn.innerText = 'Run Agent Classification'; btn.disabled = false; }
    }

    function showToast(msg, isError = false) {
      const toast = document.getElementById('toast');
      const dot = document.getElementById('toastDot');
      document.getElementById('toastMsg').innerText = msg;
      dot.className = `w-2.5 h-2.5 rounded-full ${isError ? 'bg-rose-500' : 'bg-emerald-400'}`;
      toast.classList.remove('hidden');
      setTimeout(() => toast.classList.add('hidden'), 4000);
    }

    // -----------------------------------------------------------------------
    // Init
    // -----------------------------------------------------------------------
    window.onload = async () => {
      await loadProjects();
      await loadData();
    };
  </script>
</body>
</html>
    """
