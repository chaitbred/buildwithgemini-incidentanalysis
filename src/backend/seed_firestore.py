"""
Seed script: writes the five canonical incident fixtures into Firestore.

Run once (or re-run safely — batch.set uses merge=True so it is idempotent):
    python -m src.backend.seed_firestore

Requires GOOGLE_APPLICATION_CREDENTIALS or Application Default Credentials
pointing to the target GCP project.
"""
import json
import os
import sys
from datetime import datetime, timezone
from google.cloud import firestore

# Hardcoded GCP Project ID strictly as requested
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-04-7df709bbadfe"
COLLECTION_NAME = "incidents"

SEEDED_INCIDENTS = [
    {
        "incident_id": "INC-1001",
        "title": "K8s payments-api pod crashlooping with OOMKilled",
        "description": "Payments-api pods on production cluster prod-us-east-1 started continuously restarting. kubectl describe pod reported Exit Code 137 (OOMKilled) during high volume checkout traffic. Memory limit was set to 512Mi, but JVM heap spiked past 600Mi due to unbounded cache allocation in order processing service.",
        "resolution": "Increased pod memory limits from 512Mi to 1.5Gi in deployment spec and configured JVM -XX:MaxRAMPercentage=75. Applied memory alerts in Prometheus.",
        "severity": "SEV-1",
        "primary_technology": "Kubernetes",
        "technologies": ["Kubernetes", "JVM", "Prometheus"],
        "component": "Pod Memory & Cgroups",
        "failure_mechanism": "OOMKilled",
        "root_cause_domain": "Infrastructure & Runtime",
        "resolution_pattern": "Resource Limit Tuning",
        "confidence": 0.95,
        "summary_insight": "JVM heap unbounded growth exceeded container cgroup memory limits triggering kernel OOM killer.",
        "taxonomy_path": ["Infrastructure & Runtime", "Kubernetes", "Pod Memory & Cgroups", "OOMKilled"],
        "updated_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "incident_id": "INC-1002",
        "title": "PostgreSQL connection pool exhaustion on user-db",
        "description": "Web application began returning HTTP 500 errors to customers attempting to log in. Application logs showed 'FATAL: remaining connection slots are reserved for non-replication superuser connections'. pgbouncer pool connections reached max_client_conn (1000) due to unindexed sequential scan query holding open transactions.",
        "resolution": "Terminated long-running query PID 44102 via pg_terminate_backend. Added composite index on (tenant_id, created_at) to avoid table lock. Configured pgbouncer transaction pooling mode and reduced idle transaction timeout.",
        "severity": "SEV-1",
        "primary_technology": "PostgreSQL",
        "technologies": ["PostgreSQL", "pgbouncer"],
        "component": "Connection Pool",
        "failure_mechanism": "Connection Pool Starvation",
        "root_cause_domain": "Database & Storage",
        "resolution_pattern": "Index Creation & Pool Tuning",
        "confidence": 0.96,
        "summary_insight": "Unindexed transaction saturated max_client_conn in pgbouncer, rejecting all client auth attempts.",
        "taxonomy_path": ["Database & Storage", "PostgreSQL", "Connection Pool", "Connection Pool Starvation"],
        "updated_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "incident_id": "INC-1003",
        "title": "Kafka order-events topic consumer lag surge and rebalance storm",
        "description": "Order fulfillment pipeline stalled. Grafana dashboard indicated consumer lag exceeding 450k messages on partition 3 and 7. Consumers in consumer-group 'fulfillment-worker' repeatedly dropped out causing constant group rebalances. Heartbeat timeouts were triggered because worker thread blocked on synchronous payment gateway HTTP call.",
        "resolution": "Temporarily scaled consumer group instances from 6 to 18 replicas. Wrapped third-party payment gateway call with a 2.5s circuit breaker and timeout, preventing consumer heartbeat thread starvation.",
        "severity": "SEV-2",
        "primary_technology": "Apache Kafka",
        "technologies": ["Apache Kafka", "Grafana"],
        "component": "Consumer Group",
        "failure_mechanism": "Consumer Group Rebalance Storm",
        "root_cause_domain": "Messaging & Streaming",
        "resolution_pattern": "Consumer Scaling & Downstream Timeout Tuning",
        "confidence": 0.94,
        "summary_insight": "Synchronous HTTP blocker exceeded max.poll.interval.ms causing consumer eviction and rebalance storm.",
        "taxonomy_path": ["Messaging & Streaming", "Apache Kafka", "Consumer Group", "Consumer Group Rebalance Storm"],
        "updated_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "incident_id": "INC-1004",
        "title": "Envoy proxy returning 504 Gateway Timeout across microservice mesh",
        "description": "Ingress envoy proxies started throwing 504 Gateway Timeout on /api/v2/catalog endpoints. Envoy access logs showed response_flags='UT' (upstream request timeout). Downstream catalog service was unresponsive due to goroutine leakage after recent release.",
        "resolution": "Executed instant canary rollback of catalog service to v1.14.2. Increased Envoy upstream retry count to 2 with exponential backoff and patched catalog service leak.",
        "severity": "SEV-2",
        "primary_technology": "Envoy Proxy",
        "technologies": ["Envoy Proxy", "Go Microservice"],
        "component": "Upstream Service Mesh",
        "failure_mechanism": "504 Upstream Gateway Timeout",
        "root_cause_domain": "Networking & Ingress",
        "resolution_pattern": "Canary Rollback & Upstream Retries",
        "confidence": 0.93,
        "summary_insight": "Goroutine leak froze catalog backend causing Envoy upstream timeouts.",
        "taxonomy_path": ["Networking & Ingress", "Envoy Proxy", "Upstream Service Mesh", "504 Upstream Gateway Timeout"],
        "updated_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "incident_id": "INC-1005",
        "title": "Redis primary node high CPU and latency spike due to KEYS pattern command",
        "description": "Redis cluster cache-session node hit 100% CPU utilization. Application latency degraded by 800ms across all authenticated endpoints. Redis slowlog revealed execution of KEYS 'user:session:*' command run by an unoptimized cron job.",
        "resolution": "Killed running slow script on Redis primary using SCRIPT KILL. Refactored cron worker to use non-blocking SCAN with COUNT 500 instead of KEYS command. Added client command blacklist on KEYS.",
        "severity": "SEV-2",
        "primary_technology": "Redis",
        "technologies": ["Redis"],
        "component": "In-Memory KeyStore",
        "failure_mechanism": "Single-Thread CPU Block (KEYS command)",
        "root_cause_domain": "Database & Storage",
        "resolution_pattern": "Command Blacklist & SCAN Migration",
        "confidence": 0.98,
        "summary_insight": "Blocking KEYS wildcard query locked single-threaded event loop on cache primary.",
        "taxonomy_path": ["Database & Storage", "Redis", "In-Memory KeyStore", "Single-Thread CPU Block (KEYS command)"],
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
]

def seed_firestore():
    print(f"Connecting to Firestore with hardcoded project: '{FIRESTORE_PROJECT_ID}'...")
    db = firestore.Client(project=FIRESTORE_PROJECT_ID)
    batch = db.batch()

    for inc in SEEDED_INCIDENTS:
        doc_ref = db.collection(COLLECTION_NAME).document(inc["incident_id"])
        batch.set(doc_ref, inc, merge=True)

    batch.commit()
    print(f"Successfully seeded {len(SEEDED_INCIDENTS)} incidents into Firestore collection '{COLLECTION_NAME}'.")

if __name__ == "__main__":
    seed_firestore()
