"""Shared request/response models for DeepResearch."""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class DeepResearchMode(str, Enum):
    """Supported research orchestration modes."""

    QUEUE = "queue"
    TREE = "tree"


class DeepResearchStatus(str, Enum):
    """Lifecycle status for a DeepResearch run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeepResearchRequest(BaseModel):
    """Input payload for a DeepResearch run."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(..., min_length=1, description="Research topic or question.")
    mode: DeepResearchMode = Field(default=DeepResearchMode.QUEUE)
    depth: int = Field(default=2, ge=1, le=6)
    breadth: int = Field(default=5, ge=1, le=12)
    max_parallel: int = Field(default=1, ge=1, le=10)
    max_iterations: int = Field(default=4, ge=1, le=10)
    iteration_mode: Optional[Literal["fixed", "flexible"]] = Field(default=None)
    use_web_search: bool = Field(default=False)
    use_paper_search: bool = Field(default=False)
    use_code_exec: bool = Field(default=False)
    code_exec_snippets: List[str] = Field(default_factory=list)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    index_mode: Optional[str] = Field(default=None)
    session_id: Optional[str] = Field(default=None, description="Session id for ScholarMind RAG.")
    language: Optional[str] = Field(default=None, description="Preferred output language.")
    report_style: Optional[str] = Field(default=None, description="Style hint for the report.")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeepResearchPlanItem(BaseModel):
    """Plan item for previewing research scope."""

    model_config = ConfigDict(extra="forbid")

    title: str
    question: str
    depth: int
    parent_title: Optional[str] = None


class DeepResearchPlan(BaseModel):
    """Preview payload for a DeepResearch plan."""

    model_config = ConfigDict(extra="forbid")

    items: List[DeepResearchPlanItem] = Field(default_factory=list)


class CitationOut(BaseModel):
    """Citation payload for report output."""

    model_config = ConfigDict(extra="forbid")

    citation_id: str
    ref_number: Optional[int] = None
    title: Optional[str] = None
    url: Optional[str] = None
    snippet: Optional[str] = None
    source_type: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeepResearchResponse(BaseModel):
    """Output payload for a DeepResearch run."""

    model_config = ConfigDict(extra="forbid")

    research_id: str
    status: DeepResearchStatus
    report_markdown: str
    citations: List[CitationOut]
    trace: Dict[str, Any] = Field(default_factory=dict)


class DeepResearchSubmitResponse(BaseModel):
    """Submission payload for async DeepResearch runs."""

    model_config = ConfigDict(extra="forbid")

    research_id: str
    status: DeepResearchStatus
    message: Optional[str] = None
    queue_position: Optional[int] = None
    active_runs: Optional[int] = None
    pending_runs: Optional[int] = None


class DeepResearchPriorityUpdateRequest(BaseModel):
    """Payload for updating run priority."""

    model_config = ConfigDict(extra="forbid")

    priority: int = Field(..., ge=-10, le=10)


class DeepResearchCompareRequest(BaseModel):
    """Payload for comparing two DeepResearch runs."""

    model_config = ConfigDict(extra="forbid")

    left_id: str
    right_id: str


class DeepResearchQueueItem(BaseModel):
    """Queue item snapshot for DeepResearch runs."""

    model_config = ConfigDict(extra="forbid")

    research_id: str
    topic: str
    status: DeepResearchStatus
    priority: Optional[int] = None
    effective_priority: Optional[int] = None
    wait_seconds: Optional[float] = None
    submitted_at: Optional[str] = None
    started_at: Optional[str] = None
    user_id: Optional[int] = None


class DeepResearchQueueStatus(BaseModel):
    """Queue status snapshot for DeepResearch runs."""

    model_config = ConfigDict(extra="forbid")

    active_runs: int
    pending_runs: int
    max_active_runs: int
    active_items: List[DeepResearchQueueItem] = Field(default_factory=list)
    pending_items: List[DeepResearchQueueItem] = Field(default_factory=list)


class DeepResearchCompareSide(BaseModel):
    """Comparison payload for a single run."""

    model_config = ConfigDict(extra="forbid")

    research_id: str
    status: Optional[DeepResearchStatus] = None
    topic: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    summary: Dict[str, Any] = Field(default_factory=dict)


class DeepResearchCompareResponse(BaseModel):
    """Comparison response for two runs."""

    model_config = ConfigDict(extra="forbid")

    left: DeepResearchCompareSide
    right: DeepResearchCompareSide
    diff: Dict[str, Any] = Field(default_factory=dict)


class ProgressEvent(BaseModel):
    """Structured progress events for streaming updates."""

    model_config = ConfigDict(extra="forbid")

    research_id: str
    stage: str
    message: str
    timestamp: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class DeepResearchProgressResponse(BaseModel):
    """Progress response payload for polling."""

    model_config = ConfigDict(extra="forbid")

    research_id: str
    items: List[ProgressEvent]
    next_offset: Optional[int] = None


class DeepResearchRunMeta(BaseModel):
    """Metadata for a DeepResearch run."""

    model_config = ConfigDict(extra="forbid")

    research_id: str
    status: DeepResearchStatus
    topic: str
    mode: DeepResearchMode
    priority: Optional[int] = None
    submitted_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    resumed_at: Optional[str] = None
    resume_count: Optional[int] = None
    resume_requested_at: Optional[str] = None
    resume_pending: Optional[bool] = None
    cancel_requested_at: Optional[str] = None
    last_progress_at: Optional[str] = None
    cancel_reason: Optional[str] = None
    duration_seconds: Optional[float] = None
    user_id: Optional[int] = None
    summary: Optional[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None
    token_usage: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    request: Dict[str, Any] = Field(default_factory=dict)


class DeepResearchRunList(BaseModel):
    """List response for DeepResearch runs."""

    model_config = ConfigDict(extra="forbid")

    items: List[DeepResearchRunMeta]


class DeepResearchArchive(BaseModel):
    """Archive payload for a DeepResearch run."""

    model_config = ConfigDict(extra="forbid")

    research_id: str
    meta: Dict[str, Any]
    snapshot: Dict[str, Any]
    progress: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)


class BlockEvidence(BaseModel):
    """Evidence payload for a single topic block."""

    model_config = ConfigDict(extra="forbid")

    research_id: str
    block_id: str
    block: Dict[str, Any]
    notes: List[str] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    citation_details: List[Dict[str, Any]] = Field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = Field(default_factory=list)
    decisions: List[Dict[str, Any]] = Field(default_factory=list)
    progress_events: List[Dict[str, Any]] = Field(default_factory=list)
