"""Schemas for notebook note generation."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.common import CitationOut


class NotebookNoteRequest(BaseModel):
    """Request payload for notebook note generation.

    Args:
        selection (str): Selected text to summarize.
        session_id (str): Source session id.
        language (Optional[str]): Output language code.
        title (Optional[str]): Optional title hint.
        tags (List[str]): Optional tag hints.
        top_k (Optional[int]): Retrieval top_k override.
        index_mode (Optional[str]): Retrieval index mode override.
        metadata (Dict[str, Any]): Optional metadata payload.
    """

    model_config = ConfigDict(extra="forbid")

    selection: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    language: Optional[str] = None
    title: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    index_mode: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class NotebookNoteResponse(BaseModel):
    """Response payload for notebook note generation.

    Args:
        note_markdown (str): Rendered markdown content.
        citations (List[CitationOut]): Citation list.
        trace (Dict[str, Any]): Debug metadata.
    """

    model_config = ConfigDict(extra="forbid")

    note_markdown: str
    citations: List[CitationOut] = Field(default_factory=list)
    trace: Dict[str, Any] = Field(default_factory=dict)
