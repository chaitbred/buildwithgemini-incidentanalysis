from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

class TaxonomyNode(BaseModel):
    node_id: str
    name: str
    level: str = Field(description="Level in tree: Domain, Technology, Component, FailureMode, or Custom Level")
    parent_id: Optional[str] = None
    incident_count: int = 0
    incident_ids: List[str] = Field(default_factory=list)
    children: List[str] = Field(default_factory=list, description="IDs of child nodes")
    is_custom: bool = Field(default=False, description="True if seeded/defined from a custom ontology")
    metadata: Dict[str, Any] = Field(default_factory=dict)

class DynamicTaxonomyTree(BaseModel):
    root_id: str = "root"
    nodes: Dict[str, TaxonomyNode] = Field(default_factory=dict)

    def initialize_root(self):
        if "root" not in self.nodes:
            self.nodes["root"] = TaxonomyNode(
                node_id="root",
                name="Incident Taxonomy",
                level="Root",
                parent_id=None
            )

    def add_or_get_node(self, name: str, level: str, parent_id: str = "root", is_custom: bool = False) -> TaxonomyNode:
        self.initialize_root()
        clean_name = name.strip()
        node_id = f"{parent_id}::{clean_name.lower().replace(' ', '_').replace('&', 'and')}"

        if node_id not in self.nodes:
            new_node = TaxonomyNode(
                node_id=node_id,
                name=clean_name,
                level=level,
                parent_id=parent_id,
                is_custom=is_custom
            )
            self.nodes[node_id] = new_node
            
            # Register in parent's children list
            if parent_id in self.nodes and node_id not in self.nodes[parent_id].children:
                self.nodes[parent_id].children.append(node_id)
        elif is_custom:
            self.nodes[node_id].is_custom = True

        return self.nodes[node_id]

    def import_custom_ontology(self, ontology_def: Dict[str, Any] | List[Dict[str, Any]]) -> int:
        """
        Accepts hierarchical custom ontology definitions.
        Format 1 (Nested):
        {
           "Domain Name": {
               "Technology": {
                   "Component": ["FailureMode1", "FailureMode2"]
               }
           }
        }
        Format 2 (List of Path Tuples):
        [
           {"domain": "Data", "technology": "ClickHouse", "component": "MergeTree", "failure": "Mutation Lock"}
        ]
        """
        self.initialize_root()
        added_count = 0

        if isinstance(ontology_def, list):
            for item in ontology_def:
                curr_parent = "root"
                levels = [
                    ("Domain", item.get("domain") or item.get("Domain")),
                    ("Technology", item.get("technology") or item.get("Technology")),
                    ("Component", item.get("component") or item.get("Component")),
                    ("FailureMode", item.get("failure") or item.get("failure_mode") or item.get("FailureMode"))
                ]
                for lvl, name in levels:
                    if name:
                        node = self.add_or_get_node(name=name, level=lvl, parent_id=curr_parent, is_custom=True)
                        curr_parent = node.node_id
                        added_count += 1
        elif isinstance(ontology_def, dict):
            def recurse(sub_dict: Any, parent_id: str, depth: int):
                nonlocal added_count
                level_names = ["Domain", "Technology", "Component", "FailureMode"]
                lvl = level_names[min(depth, len(level_names) - 1)]

                if isinstance(sub_dict, dict):
                    for k, v in sub_dict.items():
                        node = self.add_or_get_node(name=k, level=lvl, parent_id=parent_id, is_custom=True)
                        added_count += 1
                        recurse(v, node.node_id, depth + 1)
                elif isinstance(sub_dict, list):
                    for item in sub_dict:
                        if isinstance(item, str):
                            self.add_or_get_node(name=item, level=lvl, parent_id=parent_id, is_custom=True)
                            added_count += 1
                        elif isinstance(item, dict):
                            recurse(item, parent_id, depth)

            recurse(ontology_def, "root", 0)

        return added_count

    def register_incident(self, path_tuples: List[tuple[str, str]], incident_id: str) -> List[str]:
        self.initialize_root()
        current_parent = "root"
        full_path_names = []

        for level, name in path_tuples:
            node = self.add_or_get_node(name=name, level=level, parent_id=current_parent)
            node.incident_count += 1
            if incident_id not in node.incident_ids:
                node.incident_ids.append(incident_id)
            current_parent = node.node_id
            full_path_names.append(name)

        return full_path_names

    def to_hierarchical_dict(self, current_id: str = "root") -> Dict[str, Any]:
        if current_id not in self.nodes:
            return {}
        node = self.nodes[current_id]
        return {
            "id": node.node_id,
            "name": node.name,
            "level": node.level,
            "count": node.incident_count,
            "is_custom": node.is_custom,
            "children": [
                self.to_hierarchical_dict(child_id) 
                for child_id in node.children
            ]
        }
