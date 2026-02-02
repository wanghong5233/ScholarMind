"""Schemas for the idea generation workflow."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.common import CitationOut


class IdeaGenerationNote(BaseModel):
    """Notebook note input for idea generation context."""

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    content: str = Field(..., min_length=1)
    tags: List[str] = Field(default_factory=list)
    source: Optional[str] = None


class IdeaGenerationRequest(BaseModel):
    """Request payload for idea generation."""

    model_config = ConfigDict(extra="forbid")

    topic: Optional[str] = Field(default=None, min_length=1)
    idea_count: int = Field(default=5, ge=1, le=20)
    session_id: Optional[str] = None
    language: Optional[str] = None
    constraints: List[str] = Field(default_factory=list)
    notes: List[IdeaGenerationNote] = Field(default_factory=list)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    index_mode: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IdeaCandidate(BaseModel):
    """Structured research idea candidate."""

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    dimension: Optional[str] = None
    novelty: Optional[str] = None
    feasibility: Optional[str] = None


class IdeaGenerationItem(BaseModel):
    """Structured output for a knowledge point."""

    model_config = ConfigDict(extra="forbid")

    knowledge_point: str
    description: str
    research_ideas: List[IdeaCandidate] = Field(default_factory=list)
    kept_ideas: List[str] = Field(default_factory=list)
    rejected_ideas: List[str] = Field(default_factory=list)
    reasons: Dict[str, str] = Field(default_factory=dict)
    statement_markdown: Optional[str] = None


class IdeaGenerationResponse(BaseModel):
    """Response payload for idea generation."""

    model_config = ConfigDict(extra="forbid")

    idea_id: str
    ideas_markdown: str
    citations: List[CitationOut]
    ideas: List[IdeaGenerationItem] = Field(default_factory=list)
    trace: Dict[str, Any] = Field(default_factory=dict)


class IdeaGenerationStatus(str, Enum):
    """Lifecycle status for idea generation runs."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class IdeaGenerationRunMeta(BaseModel):
    """Metadata for an idea generation run."""

    model_config = ConfigDict(extra="forbid")

    idea_id: str
    status: IdeaGenerationStatus
    topic: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    user_id: Optional[int] = None
    error: Optional[str] = None
    request: Dict[str, Any] = Field(default_factory=dict)


class IdeaGenerationRunList(BaseModel):
    """List response for idea generation runs."""

    model_config = ConfigDict(extra="forbid")

    items: List[IdeaGenerationRunMeta]


class IdeaGenerationRunDetail(BaseModel):
    """Detail response for idea generation runs."""

    model_config = ConfigDict(extra="forbid")

    meta: IdeaGenerationRunMeta
    payload: IdeaGenerationResponse
