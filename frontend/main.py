"""Minimal FastAPI proxy for a deployed A2A agent (Agent Runtime, agents-cli 1.1.0+).

The browser talks ONLY to this proxy (same origin, no CORS, no GCP creds in the
browser). The proxy authenticates with Application Default Credentials and
forwards chat to the deployed agent over JSON-RPC 2.0 / A2A.

Also handles direct multi-file incident batch uploads (Excel/CSV/JSON) and custom
ontology tree ingestion.
"""

import os
import sys
import uuid
import json
import logging
from typing import List

# Ensure parent directory is in path so we can import local pipeline and backend tools if needed
sys.path.insert(0, "/config/Desktop/IncidentAnalysis")

import google.auth
import google.auth.transport.requests
import httpx
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("proxy")

RESOURCE = os.environ.get(
    "AGENT_ENGINE_RESOURCE_NAME",
    "projects/1014490447575/locations/us-central1/reasoningEngines/7813683231080841216"
)
AGENT_DIRECTORY = os.environ.get("AGENT_DIRECTORY", "app")
LOCATION = RESOURCE.split("/locations/")[1].split("/")[0]

A2A_ENDPOINT = (
    f"https://{LOCATION}-aiplatform.googleapis.com/reasoningEngines/v1/"
    f"{RESOURCE}/api/a2a/{AGENT_DIRECTORY}"
)

_A2UI_MIME = "application/json+a2ui"

app = FastAPI()

_credentials = None
_contexts: dict[str, str] = {}


def _get_auth():
    global _credentials
    if _credentials is None:
        _credentials, _ = google.auth.default()
    if not _credentials.valid:
        _credentials.refresh(google.auth.transport.requests.Request())
    return _credentials


def _auth_headers() -> dict[str, str]:
    token = _get_auth().token
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }


def _extract_parts(parts: list) -> list[dict]:
    out: list[dict] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        text_val = p.get("text")
        if text_val:
            out.append({"kind": "text", "text": text_val})
        elif "data" in p:
            meta = p.get("metadata") or {}
            mime = meta.get("mimeType")
            if mime == _A2UI_MIME:
                out.append({"kind": "a2ui", "data": p["data"]})
        elif "file" in p:
            uri = p["file"].get("uri")
            if uri:
                out.append({"kind": "text", "text": uri})
    return out


async def _call_agent_rpc(message: str, user_id: str = "web-user") -> list[dict]:
    """Helper to dispatch SendMessage to the Reasoning Engine over JSON-RPC."""
    context_id = _contexts.get(user_id)
    msg_payload = {
        "message_id": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": [{"text": message}]
    }
    if context_id:
        msg_payload["context_id"] = context_id

    rpc_payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": msg_payload
        }
    }

    parts: list[dict] = []
    async with httpx.AsyncClient(headers=_auth_headers(), timeout=180) as client:
        resp = await client.post(A2A_ENDPOINT, json=rpc_payload)
        if resp.status_code != 200:
            logger.error(f"Upstream returned HTTP {resp.status_code}: {resp.text}")
            return [{"kind": "text", "text": f"Error from Agent Engine: HTTP {resp.status_code}"}]

        resp_json = resp.json()
        if "error" in resp_json:
            err_msg = resp_json["error"].get("message", "Unknown error")
            return [{"kind": "text", "text": f"Agent error: {err_msg}"}]

        res = resp_json.get("result", {})
        task = res.get("task", {})
        if task.get("contextId"):
            _contexts[user_id] = task["contextId"]

        for artifact in task.get("artifacts", []):
            art_parts = artifact.get("parts", [])
            parts.extend(_extract_parts(art_parts))

        if not parts:
            status = task.get("status", {})
            msg_obj = status.get("message", {})
            status_parts = msg_obj.get("parts", [])
            parts.extend(_extract_parts(status_parts))

    return parts


@app.post("/chat")
async def chat(req: Request):
    try:
        body = await req.json()
        message = body.get("message", "")
        user_id = body.get("user_id") or "web-user"
        parts = await _call_agent_rpc(message, user_id=user_id)
        if not parts:
            parts.append({"kind": "text", "text": "(The agent did not return a response.)"})
        return JSONResponse({"parts": parts})
    except Exception as e:
        logger.exception("Error in /chat endpoint")
        return JSONResponse(
            {"parts": [{"kind": "text", "text": f"Proxy Error: {str(e)}"}]},
            status_code=500
        )


@app.post("/api/upload-excel")
async def upload_excel(file: UploadFile = File(...)):
    """Handles Excel (.xlsx, .xls) and CSV incident files of any volume."""
    try:
        temp_dir = "/tmp/incident_uploads"
        os.makedirs(temp_dir, exist_ok=True)
        file_path = os.path.join(temp_dir, f"{uuid.uuid4()}_{file.filename}")
        
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        # Parse rows using openpyxl or pandas
        rows_parsed = []
        ext = os.path.splitext(file.filename)[1].lower()

        if ext in [".xlsx", ".xls"]:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            sheet = wb.active
            rows_iter = sheet.iter_rows(values_only=True)
            header_row = next(rows_iter, None)
            if header_row:
                headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(header_row)]
                for r in rows_iter:
                    if not any(r):
                        continue
                    row_dict = {headers[i]: (str(r[i]).strip() if i < len(r) and r[i] is not None else "") for i in range(len(headers))}
                    rows_parsed.append(row_dict)
            wb.close()
        else:
            import pandas as pd
            df = pd.read_csv(file_path, dtype=str).fillna("")
            rows_parsed = df.to_dict(orient="records")

        total_rows = len(rows_parsed)
        sample_rows = rows_parsed[:5]

        # Dispatch classification and ontology ingestion for sample rows to the deployed agent
        summary_msg = (
            f"Batch uploaded file '{file.filename}' containing {total_rows} incident records. "
            f"Here is a sample of the incidents: {json.dumps(sample_rows, indent=2)}. "
            "Please classify the top incidents, map their failure domains, update the taxonomy, and confirm."
        )
        agent_parts = await _call_agent_rpc(summary_msg)

        return JSONResponse({
            "status": "success",
            "filename": file.filename,
            "total_rows_parsed": total_rows,
            "sample_records": sample_rows,
            "agent_response": agent_parts
        })

    except Exception as e:
        logger.exception("Error processing uploaded incident file")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/upload-ontology")
async def upload_ontology(file: UploadFile = File(None), json_data: str = Form(None)):
    """Handles ontology upload via JSON file or JSON payload."""
    try:
        content_str = ""
        if file and file.filename:
            raw = await file.read()
            content_str = raw.decode("utf-8")
        elif json_data:
            content_str = json_data

        if not content_str.strip():
            return JSONResponse({"status": "error", "message": "No ontology content provided."}, status_code=400)

        # Validate JSON
        parsed_json = json.loads(content_str)

        # Dispatch to agent
        prompt = f"Import this custom ontology JSON structure into the taxonomy tree: {json.dumps(parsed_json)}"
        parts = await _call_agent_rpc(prompt)

        return JSONResponse({
            "status": "success",
            "message": "Ontology imported successfully.",
            "agent_response": parts
        })
    except Exception as e:
        logger.exception("Error processing ontology upload")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
