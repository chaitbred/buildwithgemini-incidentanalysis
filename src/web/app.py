import os
import json
import shutil
import tempfile
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.models.incident import RawIncident, EnrichedIncident
from src.agents.classifier_agent import MultiTechClassifierAgent
from src.agents.taxonomy_agent import DynamicTaxonomyAgent
from src.pipeline.batch_processor import IncidentBatchProcessor

app = FastAPI(title="Autonomous Incident Classifier & Dynamic Taxonomy Miner")

DATA_DIR = os.path.abspath("data")
TAXONOMY_PATH = os.path.join(DATA_DIR, "dynamic_taxonomy.json")
ENRICHED_PATH = os.path.join(DATA_DIR, "enriched_incidents.json")

classifier = MultiTechClassifierAgent()
taxonomy_agent = DynamicTaxonomyAgent(storage_path=TAXONOMY_PATH)
batch_processor = IncidentBatchProcessor(
    classifier=classifier,
    taxonomy_agent=taxonomy_agent,
    output_enriched_path=ENRICHED_PATH
)

def load_enriched() -> List[Dict[str, Any]]:
    if os.path.exists(ENRICHED_PATH):
        try:
            with open(ENRICHED_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

@app.get("/api/taxonomy")
def get_taxonomy():
    taxonomy_agent.load()
    tree_dict = taxonomy_agent.tree.to_hierarchical_dict()
    return JSONResponse(content={
        "tree": tree_dict,
        "total_nodes": len(taxonomy_agent.tree.nodes)
    })

@app.post("/api/taxonomy/custom")
async def add_custom_ontology(request: Request):
    """
    Imports a custom user-defined taxonomy/ontology definition.
    Accepts JSON hierarchy or list of path mappings.
    """
    try:
        payload = await request.json()
        added_nodes = taxonomy_agent.import_custom_ontology(payload)
        return JSONResponse(content={
            "status": "success",
            "message": f"Successfully imported custom ontology ({added_nodes} nodes registered/updated).",
            "total_nodes": len(taxonomy_agent.tree.nodes)
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to import ontology: {str(e)}")

@app.get("/api/incidents")
def get_incidents():
    return JSONResponse(content=load_enriched())

@app.get("/api/analytics")
def get_analytics():
    incidents = load_enriched()
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
        "by_technology": sorted([{"name": k, "count": v} for k, v in tech_counts.items()], key=lambda x: x["count"], reverse=True),
        "by_domain": sorted([{"name": k, "count": v} for k, v in domain_counts.items()], key=lambda x: x["count"], reverse=True),
        "by_failure": sorted([{"name": k, "count": v} for k, v in failure_counts.items()], key=lambda x: x["count"], reverse=True),
        "by_resolution": sorted([{"name": k, "count": v} for k, v in resolution_counts.items()], key=lambda x: x["count"], reverse=True)
    })

@app.post("/api/classify_single")
def classify_single(
    title: str = Form(...),
    description: str = Form(...),
    resolution: str = Form(""),
    severity: str = Form("SEV-3")
):
    import uuid
    inc_id = f"ADHOC-{str(uuid.uuid4())[:6].upper()}"
    raw = RawIncident(
        incident_id=inc_id,
        title=title,
        description=description,
        resolution=resolution,
        severity=severity
    )
    sig = classifier.classify_incident(raw)
    path = taxonomy_agent.evolve_taxonomy(raw, sig)
    taxonomy_agent.save()

    enriched = EnrichedIncident(
        incident=raw,
        classification=sig,
        taxonomy_path=path
    )
    current = load_enriched()
    current.append(enriched.model_dump())
    with open(ENRICHED_PATH, "w") as f:
        json.dump(current, f, indent=2)

    return JSONResponse(content=enriched.model_dump())

@app.post("/api/upload_file")
async def upload_file(file: UploadFile = File(...)):
    """
    Accepts Excel (.xlsx, .xls) or CSV (.csv) files of any volume.
    Streams through chunks and classifies incidents autonomously.
    """
    filename = file.filename or "upload.csv"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".csv", ".xlsx", ".xls"]:
        raise HTTPException(status_code=400, detail="Only .csv and .xlsx/.xls files are supported.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        results = batch_processor.process_file_stream(tmp_path)
        return JSONResponse(content={
            "status": "success",
            "filename": filename,
            "processed_count": len(results),
            "total_taxonomy_nodes": len(taxonomy_agent.tree.nodes)
        })
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.post("/api/run_batch")
def run_batch():
    sample_csv = os.path.join(DATA_DIR, "sample_incidents.csv")
    if not os.path.exists(sample_csv):
        return JSONResponse(status_code=404, content={"error": "sample_incidents.csv not found"})
    results = batch_processor.process_csv(sample_csv)
    return JSONResponse(content={"status": "success", "processed_count": len(results)})

@app.get("/", response_class=HTMLResponse)
def index_page():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Autonomous Incident Classifier & Dynamic Taxonomy Miner</title>
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
  <header class="border-b border-slate-800 bg-slate-950/80 backdrop-blur px-6 py-4 flex items-center justify-between sticky top-0 z-50">
    <div class="flex items-center gap-3">
      <div class="h-9 w-9 bg-indigo-600 rounded-lg flex items-center justify-center font-bold text-lg shadow-lg shadow-indigo-500/30">
        AI
      </div>
      <div>
        <h1 class="text-lg font-bold tracking-tight text-white">Incident Taxonomy & Knowledge Miner</h1>
        <p class="text-xs text-slate-400">Autonomous Multi-Tech Classification & Custom Ontology Engine</p>
      </div>
    </div>
    <div class="flex items-center gap-2">
      <button onclick="openOntologyModal()" class="bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold px-3 py-2 rounded-lg transition flex items-center gap-1.5 shadow-sm">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
        Custom Ontology
      </button>
      <label class="cursor-pointer bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold px-3 py-2 rounded-lg transition flex items-center gap-1.5 shadow-sm">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
        <span id="uploadLabel">Upload Excel / CSV</span>
        <input type="file" id="fileUploader" accept=".xlsx,.xls,.csv" onchange="uploadDataset(event)" class="hidden">
      </label>
      <button onclick="triggerBatchRun()" id="batchBtn" class="bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold px-3 py-2 rounded-lg transition flex items-center gap-1.5 shadow-sm">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        Sample Batch
      </button>
      <button onclick="openModal()" class="bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold px-3 py-2 rounded-lg transition flex items-center gap-1.5 shadow-sm">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
        Classify Ad-Hoc
      </button>
    </div>
  </header>

  <!-- Notification Toast -->
  <div id="toast" class="hidden fixed top-16 right-6 z-50 bg-slate-800 border border-slate-700 text-slate-200 px-4 py-3 rounded-lg shadow-xl text-xs max-w-sm flex items-center gap-2">
    <div id="toastDot" class="w-2.5 h-2.5 rounded-full bg-emerald-400"></div>
    <span id="toastMsg">Processing completed.</span>
  </div>

  <!-- Main Content -->
  <main class="max-w-7xl mx-auto p-6 space-y-6">
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

    <!-- Analytics Charts -->
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

    <!-- Dynamic Taxonomy Tree Visualizer -->
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

    <!-- Incident Table & Deep Dive -->
    <div class="bg-slate-800/70 border border-slate-700/60 rounded-xl p-5">
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4">
        <div>
          <h2 class="text-base font-semibold text-white">Enriched Incident Records</h2>
          <p class="text-xs text-slate-400">Classified multi-technology fingerprints, root cause mechanisms, and resolutions</p>
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

  <!-- Modal for Custom Ontology Seeding -->
  <div id="ontologyModal" class="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center hidden p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-xl max-w-xl w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div>
          <h3 class="text-sm font-bold text-white">Add Custom Ontology</h3>
          <p class="text-[11px] text-slate-400">Seed custom domains, technologies, components, or failure modes</p>
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

  <!-- Modal for Ad-Hoc Classification -->
  <div id="modal" class="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center hidden p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-xl max-w-xl w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h3 class="text-sm font-bold text-white">Classify New Incident</h3>
        <button onclick="closeModal()" class="text-slate-400 hover:text-white">&times;</button>
      </div>
      <form id="classifyForm" onsubmit="submitAdhoc(event)" class="space-y-3 text-xs">
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

    function showToast(msg, isError = false) {
      const toast = document.getElementById('toast');
      const dot = document.getElementById('toastDot');
      document.getElementById('toastMsg').innerText = msg;
      dot.className = `w-2.5 h-2.5 rounded-full ${isError ? 'bg-rose-500' : 'bg-emerald-400'}`;
      toast.classList.remove('hidden');
      setTimeout(() => toast.classList.add('hidden'), 4000);
    }

    async function loadData() {
      try {
        const [incRes, taxRes, anaRes] = await Promise.all([
          fetch('/api/incidents'),
          fetch('/api/taxonomy'),
          fetch('/api/analytics')
        ]);

        allIncidents = await incRes.json();
        const taxonomyData = await taxRes.json();
        const analytics = await anaRes.json();

        // Update stats
        document.getElementById('statTotal').innerText = analytics.total_incidents || 0;
        document.getElementById('statTechs').innerText = analytics.by_technology?.length || 0;
        document.getElementById('statDomains').innerText = analytics.by_domain?.length || 0;
        document.getElementById('statNodes').innerText = taxonomyData.total_nodes || 0;

        renderTaxonomyTree(taxonomyData.tree);
        renderTable(allIncidents);
        renderCharts(analytics);
      } catch (err) {
        console.error("Failed to load data:", err);
      }
    }

    function renderTaxonomyTree(node) {
      const container = document.getElementById('taxonomyContainer');
      if (!node || !node.children || node.children.length === 0) {
        container.innerHTML = '<span class="text-slate-500 italic">No taxonomy nodes generated yet. Click "Sample Batch" or upload a dataset above.</span>';
        return;
      }
      container.innerHTML = buildTreeHTML(node, 0);
    }

    function buildTreeHTML(node, depth) {
      if (node.id === "root") {
        return node.children.map(c => buildTreeHTML(c, 0)).join('');
      }

      const indent = depth * 16;
      let badgeColor = "bg-slate-700 text-slate-200";
      if (node.level === "Domain") badgeColor = "bg-emerald-900/60 text-emerald-300 border border-emerald-700/50";
      if (node.level === "Technology") badgeColor = "bg-indigo-900/60 text-indigo-300 border border-indigo-700/50";
      if (node.level === "Component") badgeColor = "bg-purple-900/60 text-purple-300 border border-purple-700/50";
      if (node.level === "FailureMode") badgeColor = "bg-rose-900/60 text-rose-300 border border-rose-700/50";

      const customBadge = node.is_custom ? '<span class="badge bg-amber-900/60 text-amber-300 border border-amber-700/50 text-[9px]">Custom</span>' : '';

      let html = `
        <div style="margin-left: ${indent}px" class="py-1 flex items-center gap-2 hover:bg-slate-900/60 rounded px-1 transition">
          <span class="text-slate-600">${depth > 0 ? '&bull;' : '&blacktriangleright;'}</span>
          <span class="badge ${badgeColor}">${node.level}</span>
          ${customBadge}
          <span class="text-slate-200 font-medium">${node.name}</span>
          <span class="text-slate-500 text-[10px]">(${node.count} incident${node.count === 1 ? '' : 's'})</span>
        </div>
      `;

      if (node.children && node.children.length > 0) {
        html += node.children.map(c => buildTreeHTML(c, depth + 1)).join('');
      }
      return html;
    }

    function renderTable(incidents) {
      const tbody = document.getElementById('incidentsTableBody');
      if (incidents.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center py-6 text-slate-500">No incidents available.</td></tr>';
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
            <td class="py-3 px-3">
              <span class="badge bg-indigo-900/50 text-indigo-300 border border-indigo-700/50">${cls.primary_technology}</span>
            </td>
            <td class="py-3 px-3">
              <span class="badge bg-rose-900/50 text-rose-300 border border-rose-700/50">${cls.failure_mechanism}</span>
            </td>
            <td class="py-3 px-3">
              <span class="badge bg-emerald-900/50 text-emerald-300 border border-emerald-700/50">${cls.resolution_pattern}</span>
            </td>
            <td class="py-3 px-3 text-right font-mono text-[10px] text-slate-400">
              ${path}
            </td>
          </tr>
        `;
      }).join('');
    }

    function renderCharts(analytics) {
      const techCtx = document.getElementById('techChart').getContext('2d');
      if (techChart) techChart.destroy();
      const techLabels = (analytics.by_technology || []).map(x => x.name);
      const techData = (analytics.by_technology || []).map(x => x.count);

      techChart = new Chart(techCtx, {
        type: 'bar',
        data: {
          labels: techLabels,
          datasets: [{
            data: techData,
            backgroundColor: '#6366f1',
            borderRadius: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { display: false } },
            y: { ticks: { color: '#94a3b8', stepSize: 1 }, grid: { color: '#334155' } }
          }
        }
      });

      const domCtx = document.getElementById('domainChart').getContext('2d');
      if (domainChart) domainChart.destroy();
      const domLabels = (analytics.by_domain || []).map(x => x.name);
      const domData = (analytics.by_domain || []).map(x => x.count);

      domainChart = new Chart(domCtx, {
        type: 'doughnut',
        data: {
          labels: domLabels,
          datasets: [{
            data: domData,
            backgroundColor: ['#10b981', '#3b82f6', '#f59e0b', '#ec4899', '#8b5cf6', '#14b8a6', '#f97316'],
            borderWidth: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'right', labels: { color: '#cbd5e1', font: { size: 10 } } }
          }
        }
      });
    }

    function filterIncidents() {
      const q = document.getElementById('searchInput').value.toLowerCase();
      const filtered = allIncidents.filter(item => JSON.stringify(item).toLowerCase().includes(q));
      renderTable(filtered);
    }

    async function uploadDataset(event) {
      const file = event.target.files[0];
      if (!file) return;

      const label = document.getElementById('uploadLabel');
      const orig = label.innerText;
      label.innerText = "Ingesting & Analyzing...";

      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch('/api/upload_file', {
          method: 'POST',
          body: formData
        });
        const data = await res.json();
        if (res.ok) {
          showToast(`Ingested ${data.processed_count} incidents from ${data.filename}!`);
          await loadData();
        } else {
          showToast(data.detail || "Upload error", true);
        }
      } catch (err) {
        showToast("File upload failed: " + err, true);
      } finally {
        label.innerText = orig;
        event.target.value = '';
      }
    }

    function openOntologyModal() { 
      document.getElementById('ontologyModal').classList.remove('hidden'); 
      if (!document.getElementById('ontologyInput').value) {
        loadSampleOntology();
      }
    }
    function closeOntologyModal() { document.getElementById('ontologyModal').classList.add('hidden'); }

    function loadSampleOntology() {
      const sample = {
        "Data & Streaming Infrastructure": {
          "ClickHouse": {
            "ZooKeeper KeeperClient": ["Session Expiration", "Connection Refused"],
            "MergeTree Engine": ["Part Mutex Contention", "Max Parts Per Partition Exceeded"]
          },
          "Apache Flink": {
            "Checkpoint Coordinator": ["Checkpoint Barrier Alignment Timeout"],
            "TaskExecutor": ["OOM Managed Memory Starvation"]
          }
        },
        "Security & Identity": {
          "HashiCorp Vault": {
            "Raft Storage": ["Consensus Lost", "Disk Latency Warning"],
            "Transit Engine": ["Token Lease Expired"]
          }
        }
      };
      document.getElementById('ontologyInput').value = JSON.stringify(sample, null, 2);
    }

    async function submitCustomOntology(e) {
      e.preventDefault();
      const btn = document.getElementById('ontoSubmitBtn');
      btn.innerText = "Importing...";
      btn.disabled = true;

      try {
        const text = document.getElementById('ontologyInput').value;
        const payload = JSON.parse(text);

        const res = await fetch('/api/taxonomy/custom', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
          showToast(data.message);
          closeOntologyModal();
          await loadData();
        } else {
          showToast(data.detail || "Ontology import failed", true);
        }
      } catch (err) {
        showToast("Invalid JSON format: " + err, true);
      } finally {
        btn.innerText = "Import Ontology";
        btn.disabled = false;
      }
    }

    async function triggerBatchRun() {
      const btn = document.getElementById('batchBtn');
      btn.innerText = "Processing...";
      btn.disabled = true;
      try {
        await fetch('/api/run_batch', { method: 'POST' });
        showToast("Sample batch processed successfully!");
        await loadData();
      } catch (err) {
        showToast("Batch run failed: " + err, true);
      } finally {
        btn.innerText = "Sample Batch";
        btn.disabled = false;
      }
    }

    function openModal() { document.getElementById('modal').classList.remove('hidden'); }
    function closeModal() { document.getElementById('modal').classList.add('hidden'); }

    async function submitAdhoc(e) {
      e.preventDefault();
      const form = document.getElementById('classifyForm');
      const formData = new FormData(form);
      const btn = document.getElementById('submitBtn');
      btn.innerText = "Analyzing...";
      btn.disabled = true;

      try {
        await fetch('/api/classify_single', {
          method: 'POST',
          body: formData
        });
        form.reset();
        closeModal();
        showToast("Incident classified and taxonomy updated!");
        await loadData();
      } catch (err) {
        showToast("Classification failed: " + err, true);
      } finally {
        btn.innerText = "Run Agent Classification";
        btn.disabled = false;
      }
    }

    window.onload = loadData;
  </script>
</body>
</html>
    """
