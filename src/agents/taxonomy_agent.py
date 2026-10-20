import json
import os
from typing import List, Optional, Dict, Any
from src.models.incident import RawIncident, TechStackSignature, EnrichedIncident
from src.models.taxonomy import DynamicTaxonomyTree
from src.logging_config import get_logger

logger = get_logger("pipeline.taxonomy")

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
                logger.info("Loaded taxonomy from %s", self.storage_path)
            except Exception:
                # Corrupt or incompatible file: start fresh rather than crashing the pipeline.
                logger.exception("Could not load existing taxonomy from %s; starting with an empty tree",
                                 self.storage_path)
                self.tree = DynamicTaxonomyTree()
        else:
            logger.info("No taxonomy file at %s; initialising new tree", self.storage_path)
            self.tree.initialize_root()

    def save(self):
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        with open(self.storage_path, "w") as f:
            f.write(self.tree.model_dump_json(indent=2))
        logger.debug("Saved taxonomy to %s", self.storage_path)

    def import_custom_ontology(self, ontology_def: Dict[str, Any] | List[Dict[str, Any]]) -> int:
        added = self.tree.import_custom_ontology(ontology_def)
        self.save()
        logger.info("Imported custom ontology: %d nodes added", added)
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
        logger.debug("Taxonomy evolved: incident=%s path=%s", incident.incident_id, assigned_path)
        return assigned_path
