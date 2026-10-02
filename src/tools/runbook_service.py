import os
import json
from typing import List, Dict, Any, Optional

RUNBOOKS_DB = [
    {
        "id": "RBK-K8S-OOM",
        "technology": "Kubernetes",
        "failure_mechanism": "OOMKilled",
        "title": "Kubernetes Pod OOMKilled Remediation Runbook",
        "severity_level": "SEV-1 / SEV-2",
        "symptoms": ["Exit Code 137", "Continuous CrashLoopBackOff", "JVM Heap spike"],
        "diagnostic_steps": [
            "Run 'kubectl describe pod <pod_name> -n <namespace>' and inspect Last State (OOMKilled).",
            "Check pod resource limits vs usage: 'kubectl top pod <pod_name> -n <namespace>'.",
            "Inspect container memory metrics in Prometheus / Grafana."
        ],
        "remediation_actions": [
            "Increase container limits in deployment spec: 'spec.containers[].resources.limits.memory'.",
            "For JVM applications, tune '-XX:MaxRAMPercentage=75' and '-XX:InitialRAMPercentage=50'.",
            "Verify application cache eviction policies to prevent unbounded in-memory collection growth.",
            "Apply horizontal pod autoscaler (HPA) targeting memory utilization: 'kubectl autoscale deployment <dep> --cpu-percent=70 --min=3 --max=10'."
        ],
        "verification": "Confirm pod status is Running and restarts count stabilizes at 0."
    },
    {
        "id": "RBK-PG-POOL",
        "technology": "PostgreSQL",
        "failure_mechanism": "Connection Pool Starvation",
        "title": "PostgreSQL & PgBouncer Connection Pool Starvation Runbook",
        "severity_level": "SEV-1",
        "symptoms": ["FATAL: remaining connection slots reserved", "App HTTP 500 spike", "pgbouncer client pool maxed"],
        "diagnostic_steps": [
            "Identify blocking queries holding idle transactions: SELECT pid, now() - xact_start AS duration, state, query FROM pg_stat_activity WHERE state != 'idle' ORDER BY duration DESC LIMIT 5;",
            "Check pgbouncer pool stats: SHOW POOLS; SHOW CLIENTS;"
        ],
        "remediation_actions": [
            "Terminate blocking query: SELECT pg_terminate_backend(<pid>);",
            "Add missing composite index on queried foreign keys / timestamps.",
            "Switch pgbouncer pool_mode from 'session' to 'transaction'.",
            "Lower idle_in_transaction_session_timeout to prevent leaked connections."
        ],
        "verification": "Check pg_stat_activity for open connection count drop below 70% threshold."
    },
    {
        "id": "RBK-KAFKA-REBALANCE",
        "technology": "Apache Kafka",
        "failure_mechanism": "Consumer Group Rebalance Storm",
        "title": "Kafka Consumer Group Rebalance Storm & Lag Mitigation Runbook",
        "severity_level": "SEV-2",
        "symptoms": ["Consumer lag surge > 100k", "Frequent 'CommitFailedException'", "Partitions revoking repeatedly"],
        "diagnostic_steps": [
            "Inspect consumer group details: 'kafka-consumer-groups.sh --bootstrap-server <broker> --describe --group <group_name>'.",
            "Check consumer logs for 'Heartbeat timeout' or 'poll timeout exceeded'."
        ],
        "remediation_actions": [
            "Increase 'max.poll.interval.ms' if message batch processing takes longer than default 300s.",
            "Wrap slow external dependencies (e.g. payment gateway) with an aggressive circuit breaker and timeout (e.g. 2.5s).",
            "Temporarily scale out consumer replicas to process backlog partition lag."
        ],
        "verification": "Monitor consumer group lag returning to baseline under 1000 messages across all partitions."
    },
    {
        "id": "RBK-ENVOY-504",
        "technology": "Envoy Proxy",
        "failure_mechanism": "504 Upstream Gateway Timeout",
        "title": "Envoy Ingress 504 Gateway Timeout Runbook",
        "severity_level": "SEV-2",
        "symptoms": ["HTTP 504 responses to clients", "response_flags='UT' in Envoy access logs"],
        "diagnostic_steps": [
            "Check Envoy access logs for upstream service cluster destination.",
            "Inspect downstream service health and goroutine / thread dump."
        ],
        "remediation_actions": [
            "Initiate canary rollback of recent downstream service deployment.",
            "Tune Envoy cluster retry policy with exponential backoff.",
            "Verify upstream service circuit breaking thresholds ('max_connections', 'max_pending_requests')."
        ],
        "verification": "Verify ingress error rate returns below 0.05%."
    },
    {
        "id": "RBK-REDIS-KEYS",
        "technology": "Redis",
        "failure_mechanism": "Single-Thread CPU Block (KEYS command)",
        "title": "Redis Single-Thread CPU Saturation & Slowlog Remediation Runbook",
        "severity_level": "SEV-2",
        "symptoms": ["100% CPU on Redis primary node", "Application latency spikes by >500ms"],
        "diagnostic_steps": [
            "Query Redis slowlog: SLOWLOG GET 10",
            "Check running clients: CLIENT LIST"
        ],
        "remediation_actions": [
            "Kill blocking script: SCRIPT KILL or kill offending client connection: CLIENT KILL <ip:port>",
            "Replace blocking 'KEYS pattern' with iterative non-blocking 'SCAN 0 MATCH pattern COUNT 500'.",
            "Configure command renaming or blacklisting in redis.conf: 'rename-command KEYS \"\"'."
        ],
        "verification": "Verify Redis CPU drops below 20% and slowlog has zero new entries."
    }
]

def search_runbooks(technology: str, failure_mechanism: Optional[str] = None) -> List[Dict[str, Any]]:
    tech_query = (technology or "").strip().lower()
    fail_query = (failure_mechanism or "").strip().lower()

    matches = []
    for rbk in RUNBOOKS_DB:
        rbk_tech = rbk["technology"].lower()
        rbk_fail = rbk["failure_mechanism"].lower()
        rbk_title = rbk["title"].lower()

        tech_match = tech_query in rbk_tech or rbk_tech in tech_query
        fail_match = not fail_query or (fail_query in rbk_fail or rbk_fail in fail_query or fail_query in rbk_title)

        if tech_match and fail_match:
            matches.append(rbk)
        elif tech_match and not matches:
            # Best-effort: include the first tech-matching runbook even when the
            # failure mechanism doesn't match, so callers always get something useful.
            matches.append(rbk)

    # Safety net: return a generic triage template when no runbook matches at all.
    return matches or [
        {
            "id": "RBK-GENERIC-TRIAGE",
            "technology": technology,
            "failure_mechanism": failure_mechanism or "General System Degradation",
            "title": f"Standard Incident Diagnostics for {technology}",
            "diagnostic_steps": [
                f"Inspect service error logs and trace correlations for {technology}.",
                "Check compute, memory, and networking saturation metrics.",
                "Review recent CI/CD deployments and configuration changes."
            ],
            "remediation_actions": [
                "Roll back recent releases if degradation coincides with a deployment.",
                "Scale compute replicas to shed traffic load.",
                "Restart affected pods or containers if thread exhaustion is detected."
            ],
            "verification": "Verify error rate returns to normal baseline."
        }
    ]
