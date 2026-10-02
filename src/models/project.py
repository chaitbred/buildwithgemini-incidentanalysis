from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ProjectSettings(BaseModel):
    """Per-project LLM and taxonomy configuration."""

    classification_hint: str = Field(
        default="",
        description="Extra domain context appended to the LLM classification prompt for every incident in this project",
    )
    default_severity: str = Field(
        default="SEV-3",
        description="Severity pre-filled when classifying new incidents ad-hoc",
    )
    custom_taxonomy_seed: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Ontology dict auto-imported when the project is created. "
            "Accepts the same nested dict shape as POST /api/taxonomy/custom"
        ),
    )


class Project(BaseModel):
    project_id: str = Field(description="URL-safe identifier, e.g. 'acme-corp' or 'project-phoenix'")
    name: str = Field(description="Human-readable display name")
    description: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
