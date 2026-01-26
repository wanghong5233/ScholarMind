"""Schemas for the idea generation workflow."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.common import CitationOut


class IdeaGenerationRequest(BaseModel):
    """Request payload for idea generation."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(..., min_length=1)
    idea_count: int = Field(default=5, ge=1, le=20)
    session_id: Optional[str] = None
    language: Optional[str] = None
    constraints: List[str] = Field(default_factory=list)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    index_mode: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IdeaGenerationResponse(BaseModel):
    """Response payload for idea generation."""

    model_config = ConfigDict(extra="forbid")

    idea_id: str
    ideas_markdown: str
    citations: List[CitationOut]
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
