FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
ENV AGENT_ENGINE_RESOURCE_NAME="projects/1014490447575/locations/us-central1/reasoningEngines/7279443725284016128"
ENV AGENT_DIRECTORY="app"

CMD ["python", "main.py"]
