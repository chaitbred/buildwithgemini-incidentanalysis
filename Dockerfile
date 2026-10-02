# =============================================================================
# Incident Analysis — main API service (src/web/app.py)
# =============================================================================
# Build:
#   docker build -t incident-analysis .
#
# Run (Gemini, default):
#   docker run -p 8080:8080 \
#     -e GEMINI_API_KEY=<your-key> \
#     incident-analysis
#
# Run (Anthropic Claude):
#   docker run -p 8080:8080 \
#     -e LLM_PROVIDER=anthropic \
#     -e ANTHROPIC_API_KEY=<your-key> \
#     incident-analysis
#
# Run (OpenAI):
#   docker run -p 8080:8080 \
#     -e LLM_PROVIDER=openai \
#     -e OPENAI_API_KEY=<your-key> \
#     incident-analysis
#
# Persist taxonomy and enriched incident data across restarts:
#   docker run -p 8080:8080 \
#     -e GEMINI_API_KEY=<your-key> \
#     -v $(pwd)/data:/app/data \
#     incident-analysis
#
# Google Cloud (Firestore / Storage) credentials:
#   Mount a service account key and set GOOGLE_APPLICATION_CREDENTIALS, or
#   use Workload Identity when running on GKE / Cloud Run.
# =============================================================================

# ── Stage 1: dependency installation ─────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /build

# Install OS build tools needed by some Python packages (e.g. numpy, pandas)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy both requirements files so the layer is cached unless they change.
# Root requirements cover the main app; optional SDK installs for extra
# LLM providers are handled below.
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
    # Optional LLM provider SDKs — install the ones you need.
    # Gemini (google-genai) is already in requirements.txt.
    # Uncomment the line(s) for the provider(s) you plan to use:
    #
    # Anthropic Claude  →  LLM_PROVIDER=anthropic
    # anthropic \
    #
    # OpenAI GPT        →  LLM_PROVIDER=openai
    # openai \
    && true

# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from the build stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application source (exclude .venv, __pycache__, local data, etc.)
COPY src/       ./src/
COPY data/      ./data/

# The data directory must be writable at runtime (taxonomy + enriched JSON)
RUN chmod -R 777 /app/data

# ── Environment variables ─────────────────────────────────────────────────────
# LLM provider selection (see base_llm.py for full documentation).
# Override any of these at `docker run` time with -e KEY=VALUE.
ENV LLM_PROVIDER=gemini
# ENV LLM_MODEL=gemini-2.5-flash   # default for gemini; set to override

# Server config
ENV PORT=8080

# Google Cloud (Firestore / Storage / Auth) — set at runtime or via mounted SA key:
# ENV GOOGLE_APPLICATION_CREDENTIALS=/secrets/sa-key.json

# ── Expose & entrypoint ───────────────────────────────────────────────────────
EXPOSE 8080

# Run the FastAPI app with uvicorn.  The WORKDIR is /app so Python resolves
# the `src.*` package imports correctly.
CMD uvicorn src.web.app:app \
      --host 0.0.0.0 \
      --port $PORT \
      --workers 1 \
      --log-level info
