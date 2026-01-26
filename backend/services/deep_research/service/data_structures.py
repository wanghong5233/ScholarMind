"""Core data structures for DeepResearch orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    """Return the current UTC timestamp in ISO format."""

    return datetime.utcnow().isoformat()


class TopicStatus(str, Enum):
    """Lifecycle status for a topic block."""

    PENDING = "pending"
    RESEARCHING = "researching"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ToolType(str, Enum):
    """Supported tool categories for trace logging."""

    RAG = "rag"
    SEARCH = "search"
    COMPARE = "compare"
    CODE = "code"
    NOTE = "note"
    REPORT = "report"


@dataclass
class ToolTrace:
    """Trace metadata for a single tool call."""

    tool_id: str
    citation_id: str
    tool_type: ToolType
    query: str
    raw_answer: str
    summary: str
    timestamp: str = field(default_factory=_now_iso)
    raw_answer_truncated: bool = False
    raw_answer_original_size: int = 0

    def truncate_raw_answer(self, max_chars: int = 2000) -> None:
        """Truncate the raw answer for storage safety."""

        if len(self.raw_answer) <= max_chars:
            return
        self.raw_answer_original_size = len(self.raw_answer)
        self.raw_answer = f"{self.raw_answer[:max_chars].rstrip()}..."
        self.raw_answer_truncated = True

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the trace into a JSON-friendly dict."""

        return {
            "tool_id": self.tool_id,
            "citation_id": self.citation_id,
            "tool_type": self.tool_type.value,
            "query": self.query,
            "raw_answer": self.raw_answer,
            "summary": self.summary,
            "timestamp": self.timestamp,
            "raw_answer_truncated": self.raw_answer_truncated,
            "raw_answer_original_size": self.raw_answer_original_size,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ToolTrace":
        """Create a ToolTrace from a serialized dict."""

        return cls(
            tool_id=payload["tool_id"],
            citation_id=payload["citation_id"],
            tool_type=ToolType(payload["tool_type"]),
            query=payload.get("query", ""),
            raw_answer=payload.get("raw_answer", ""),
            summary=payload.get("summary", ""),
            timestamp=payload.get("timestamp", _now_iso()),
            raw_answer_truncated=payload.get("raw_answer_truncated", False),
            raw_answer_original_size=payload.get("raw_answer_original_size", 0),
        )


@dataclass
class ScholarCitation:
    """Normalized citation metadata for reports."""

    citation_id: str
    title: Optional[str]
    url: Optional[str]
    snippet: Optional[str]
    source_type: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    ref_number: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the citation into a JSON-friendly dict."""

        return {
            "citation_id": self.citation_id,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source_type": self.source_type,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "ref_number": self.ref_number,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ScholarCitation":
        """Create a citation instance from serialized data."""

        return cls(
            citation_id=payload["citation_id"],
            title=payload.get("title"),
            url=payload.get("url"),
            snippet=payload.get("snippet"),
            source_type=payload.get("source_type"),
            metadata=payload.get("metadata", {}),
            created_at=payload.get("created_at", _now_iso()),
            ref_number=payload.get("ref_number"),
        )


@dataclass
class TopicBlock:
    """Unit of work for DeepResearch planning and execution."""

    block_id: str
    title: str
    question: str
    status: TopicStatus = TopicStatus.PENDING
    depth: int = 0
    parent_id: Optional[str] = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    iterations: int = 0
    max_iterations: int = 3
    followups_generated: bool = False
    notes: List[str] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)
    tool_traces: List[ToolTrace] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    child_ids: List[str] = field(default_factory=list)

    def touch(self) -> None:
        """Update the modified timestamp."""

        self.updated_at = _now_iso()

    def add_citation(self, citation_id: str) -> None:
        """Attach a citation id to this topic block."""

        if citation_id not in self.citations:
            self.citations.append(citation_id)
            self.touch()

    def add_trace(self, trace: ToolTrace) -> None:
        """Attach a tool trace to this block."""

        self.tool_traces.append(trace)
        self.touch()

    def add_decision(self, decision: Dict[str, Any]) -> None:
        """Attach a decision record to this block."""

        if decision:
            self.decisions.append(decision)
            self.touch()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the block into a JSON-friendly dict."""

        return {
            "block_id": self.block_id,
            "title": self.title,
            "question": self.question,
            "status": self.status.value,
            "depth": self.depth,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "iterations": self.iterations,
            "max_iterations": self.max_iterations,
            "followups_generated": self.followups_generated,
            "notes": list(self.notes),
            "citations": list(self.citations),
            "tool_traces": [trace.to_dict() for trace in self.tool_traces],
            "decisions": list(self.decisions),
            "child_ids": list(self.child_ids),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TopicBlock":
        """Rehydrate a topic block from persisted data."""

        return cls(
            block_id=payload["block_id"],
            title=payload.get("title", ""),
            question=payload.get("question", ""),
            status=TopicStatus(payload.get("status", TopicStatus.PENDING.value)),
            depth=payload.get("depth", 0),
            parent_id=payload.get("parent_id"),
            created_at=payload.get("created_at", _now_iso()),
            updated_at=payload.get("updated_at", _now_iso()),
            iterations=payload.get("iterations", 0),
            max_iterations=payload.get("max_iterations", 3),
            followups_generated=payload.get("followups_generated", False),
            notes=list(payload.get("notes", [])),
            citations=list(payload.get("citations", [])),
            tool_traces=[ToolTrace.from_dict(t) for t in payload.get("tool_traces", [])],
            decisions=list(payload.get("decisions", [])),
            child_ids=list(payload.get("child_ids", [])),
        )


class DynamicTopicQueue:
    """Queue manager for DeepResearch planning and execution."""

    def __init__(self, research_id: str, max_length: Optional[int] = None) -> None:
        """Initialize a queue for a DeepResearch session."""

        self.research_id = research_id
        self.blocks: List[TopicBlock] = []
        self.max_length = max_length
        self._block_counter = 0

    def _next_block_id(self) -> str:
        """Generate the next block id."""

        self._block_counter += 1
        return f"B{self._block_counter:03d}"

    def add_block(
        self,
        title: str,
        question: str,
        depth: int = 0,
        parent_id: Optional[str] = None,
        max_iterations: int = 3,
    ) -> TopicBlock:
        """Add a new topic block to the queue."""

        if self.max_length is not None and len(self.blocks) >= self.max_length:
            raise ValueError("Topic queue has reached the configured max length.")
        block = TopicBlock(
            block_id=self._next_block_id(),
            title=title,
            question=question,
            depth=depth,
            parent_id=parent_id,
            max_iterations=max_iterations,
        )
        self.blocks.append(block)
        if parent_id:
            parent = self.get_block(parent_id)
            if parent:
                parent.child_ids.append(block.block_id)
                parent.touch()
        return block

    def get_block(self, block_id: str) -> Optional[TopicBlock]:
        """Fetch a block by id."""

        return next((b for b in self.blocks if b.block_id == block_id), None)

    def get_next_pending_block(self) -> Optional[TopicBlock]:
        """Return the next pending block in FIFO order."""

        return next((b for b in self.blocks if b.status == TopicStatus.PENDING), None)

    def mark_block_status(self, block_id: str, status: TopicStatus) -> None:
        """Update the status of a topic block."""

        block = self.get_block(block_id)
        if not block:
            raise ValueError(f"Unknown block id: {block_id}")
        block.status = status
        block.touch()

    def increment_iteration(self, block_id: str) -> int:
        """Increment iteration count and return the new value."""

        block = self.get_block(block_id)
        if not block:
            raise ValueError(f"Unknown block id: {block_id}")
        block.iterations += 1
        if block.iterations >= block.max_iterations:
            block.status = TopicStatus.FAILED
        block.touch()
        return block.iterations

    def list_blocks(self, status: Optional[TopicStatus] = None) -> List[TopicBlock]:
        """List blocks, optionally filtered by status."""

        if status is None:
            return list(self.blocks)
        return [b for b in self.blocks if b.status == status]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the queue for persistence."""

        return {
            "research_id": self.research_id,
            "max_length": self.max_length,
            "block_counter": self._block_counter,
            "blocks": [b.to_dict() for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "DynamicTopicQueue":
        """Rehydrate a queue from persisted data."""

        queue = cls(
            research_id=payload["research_id"],
            max_length=payload.get("max_length"),
        )
        queue._block_counter = payload.get("block_counter", 0)
        queue.blocks = [TopicBlock.from_dict(b) for b in payload.get("blocks", [])]
        return queue
