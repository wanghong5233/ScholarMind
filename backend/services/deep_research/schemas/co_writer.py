"""Schemas for the co-writer workflow."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.common import CitationOut


class CoWriterTask(str, Enum):
    """Supported co-writer tasks."""

    REWRITE = "rewrite"
    EXPAND = "expand"
    SHORTEN = "shorten"
    ANNOTATE = "annotate"


class CoWriterRequest(BaseModel):
    """Request payload for co-writer operations."""

    model_config = ConfigDict(extra="forbid")

    task: CoWriterTask
    text: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    language: Optional[str] = None
    instructions: Optional[str] = None
    tone: Optional[str] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    index_mode: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CoWriterResponse(BaseModel):
    """Response payload for co-writer operations."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str
    result_markdown: str
    citations: List[CitationOut]
    trace: Dict[str, Any] = Field(default_factory=dict)


class CoWriterStatus(str, Enum):
    """Lifecycle status for co-writer runs."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CoWriterRunMeta(BaseModel):
    """Metadata for a co-writer run."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str
    status: CoWriterStatus
    task: CoWriterTask
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    user_id: Optional[int] = None
    error: Optional[str] = None
    request: Dict[str, Any] = Field(default_factory=dict)


class CoWriterRunList(BaseModel):
    """List response for co-writer runs."""

    model_config = ConfigDict(extra="forbid")

    items: List[CoWriterRunMeta]


class CoWriterRunDetail(BaseModel):
    """Detail response for co-writer runs."""

    model_config = ConfigDict(extra="forbid")

    meta: CoWriterRunMeta
    payload: CoWriterResponse
