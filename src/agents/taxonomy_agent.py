import json
import os
from typing import List, Optional, Dict, Any
from src.models.incident import RawIncident, TechStackSignature, EnrichedIncident
from src.models.taxonomy import DynamicTaxonomyTree

class DynamicTaxonomyAgent:
    """
    Autonomous agent responsible for constructing, evolving, and maintaining
    the multi-tiered incident taxonomy tree with custom ontology seeding support.
    """
    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = storage_path or "data/dynamic_taxonomy.json"
        self.tree = DynamicTaxonomyTree()
        self.load()

    def load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                    self.tree = DynamicTaxonomyTree.model_validate(data)
            except Exception as e:
                print(f"[TaxonomyAgent] Warning: could not load existing taxonomy: {e}")
                self.tree = DynamicTaxonomyTree()
        else:
            self.tree.initialize_root()

    def save(self):
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        with open(self.storage_path, "w") as f:
            f.write(self.tree.model_dump_json(indent=2))

    def import_custom_ontology(self, ontology_def: Dict[str, Any] | List[Dict[str, Any]]) -> int:
        """Seeds or extends the taxonomy tree with custom nodes."""
        added = self.tree.import_custom_ontology(ontology_def)
        self.save()
        return added

    def evolve_taxonomy(self, incident: RawIncident, signature: TechStackSignature) -> List[str]:
        """
        Dynamically inserts the incident's signature into the hierarchical tree:
        Domain -> Primary Technology -> Component -> Failure Mechanism
        """
        path_tuples = [
            ("Domain", signature.root_cause_domain),
            ("Technology", signature.primary_technology),
            ("Component", signature.component),
            ("FailureMode", signature.failure_mechanism)
        ]
        assigned_path = self.tree.register_incident(path_tuples, incident.incident_id)
        return assigned_path
