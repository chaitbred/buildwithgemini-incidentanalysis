import os
import unittest
from src.models.incident import RawIncident, TechStackSignature
from src.models.taxonomy import DynamicTaxonomyTree
from src.agents.classifier_agent import MultiTechClassifierAgent
from src.agents.taxonomy_agent import DynamicTaxonomyAgent
from src.pipeline.batch_processor import IncidentBatchProcessor
from src.tools.runbook_service import search_runbooks
from src.tools.postmortem_generator import create_postmortem_report

class TestIncidentPipeline(unittest.TestCase):
    def setUp(self):
        self.test_taxonomy_path = "data/test_taxonomy.json"
        self.test_enriched_path = "data/test_enriched.json"
        if os.path.exists(self.test_taxonomy_path):
            os.remove(self.test_taxonomy_path)
        if os.path.exists(self.test_enriched_path):
            os.remove(self.test_enriched_path)

    def tearDown(self):
        if os.path.exists(self.test_taxonomy_path):
            os.remove(self.test_taxonomy_path)
        if os.path.exists(self.test_enriched_path):
            os.remove(self.test_enriched_path)

    def test_classifier_fallback_extraction(self):
        classifier = MultiTechClassifierAgent()
        sample = RawIncident(
            incident_id="INC-TEST-1",
            title="K8s pod OOMKilled",
            description="JVM heap spiked past 600Mi, pod was terminated with Exit Code 137 OOMKilled.",
            resolution="Increased pod memory limits."
        )
        sig = classifier.classify_incident(sample)
        self.assertEqual(sig.primary_technology, "Kubernetes")
        self.assertEqual(sig.failure_mechanism, "OOMKilled")
        self.assertIn("Kubernetes", sig.technologies)

    def test_dynamic_taxonomy_evolution(self):
        taxonomy_agent = DynamicTaxonomyAgent(storage_path=self.test_taxonomy_path)
        sample = RawIncident(
            incident_id="INC-TEST-2",
            title="Kafka consumer lag",
            description="Consumer group rebalance storm caused 400k message lag.",
            resolution="Scaled replicas."
        )
        sig = TechStackSignature(
            technologies=["Apache Kafka"],
            primary_technology="Apache Kafka",
            component="Consumer Group",
            failure_mechanism="Consumer Group Rebalance Storm",
            root_cause_domain="Messaging & Streaming",
            resolution_pattern="Replica Scaling"
        )
        path = taxonomy_agent.evolve_taxonomy(sample, sig)
        self.assertEqual(len(path), 4)
        self.assertEqual(path[0], "Messaging & Streaming")
        self.assertEqual(path[1], "Apache Kafka")

        taxonomy_agent.save()
        self.assertTrue(os.path.exists(self.test_taxonomy_path))

    def test_batch_processor_execution(self):
        classifier = MultiTechClassifierAgent()
        taxonomy_agent = DynamicTaxonomyAgent(storage_path=self.test_taxonomy_path)
        processor = IncidentBatchProcessor(
            classifier=classifier,
            taxonomy_agent=taxonomy_agent,
            output_enriched_path=self.test_enriched_path
        )
        results = processor.process_csv("data/sample_incidents.csv")
        self.assertGreaterEqual(len(results), 10)
        self.assertTrue(os.path.exists(self.test_enriched_path))

    def test_custom_ontology_import(self):
        taxonomy_agent = DynamicTaxonomyAgent(storage_path=self.test_taxonomy_path)
        sample_ontology = {
            "Observability & Tracing": {
                "OpenTelemetry": {
                    "Collector": ["OOM Crash", "gRPC Buffer Full"]
                }
            }
        }
        added = taxonomy_agent.import_custom_ontology(sample_ontology)
        self.assertGreaterEqual(added, 3)
        self.assertIn("root::observability_and_tracing", taxonomy_agent.tree.nodes)
        self.assertTrue(taxonomy_agent.tree.nodes["root::observability_and_tracing"].is_custom)

    def test_excel_file_streaming(self):
        classifier = MultiTechClassifierAgent()
        taxonomy_agent = DynamicTaxonomyAgent(storage_path=self.test_taxonomy_path)
        processor = IncidentBatchProcessor(
            classifier=classifier,
            taxonomy_agent=taxonomy_agent,
            output_enriched_path=self.test_enriched_path
        )
        results = processor.process_file_stream("data/sample_incidents.xlsx")
        self.assertEqual(len(results), 3)
        self.assertTrue(any(r.incident.title.startswith("ClickHouse") for r in results))

    def test_runbook_service_search(self):
        matches = search_runbooks("Kubernetes", "OOMKilled")
        self.assertGreaterEqual(len(matches), 1)
        self.assertEqual(matches[0]["technology"], "Kubernetes")
        self.assertIn("kubectl describe pod", " ".join(matches[0]["diagnostic_steps"]))

    def test_postmortem_generation(self):
        res = create_postmortem_report("INC-1001")
        self.assertEqual(res["status"], "success")
        self.assertTrue(os.path.exists(res["filepath"]))
        with open(res["filepath"]) as f:
            content = f.read()
            self.assertIn("Post-Mortem RCA Report", content)
            self.assertIn("INC-1001", content)

if __name__ == "__main__":
    unittest.main()

    def test_external_status_tool(self):
        from src.tools.external_status import check_external_service_status
        res = check_external_service_status("github")
        self.assertIn("service", res)
        self.assertIn("overall_status", res)
        self.assertIn("key_components", res)
