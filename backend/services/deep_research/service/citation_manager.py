"""Citation registry and id generation for DeepResearch."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from service.data_structures import ScholarCitation


class CitationManager:
    """Manage citations and reference numbers for a research session."""

    def __init__(self, research_id: str, cache_dir: Optional[Path] = None) -> None:
        """Initialize the citation registry."""

        self.research_id = research_id
        self._citations: Dict[str, ScholarCitation] = {}
        self._plan_counter = 0
        self._block_counters: Dict[str, int] = {}
        self._ref_number_map: Dict[str, int] = {}
        self._lock = RLock()
        self._cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def generate_plan_citation_id(self) -> str:
        """Generate a unique citation id for planning."""

        with self._lock:
            self._plan_counter += 1
            return f"PLAN-{self._plan_counter:02d}"

    def generate_research_citation_id(self, block_id: str) -> str:
        """Generate a unique citation id for a topic block."""

        safe_block_id = re.sub(r"[^A-Za-z0-9]+", "", block_id) or "X"
        with self._lock:
            self._block_counters.setdefault(safe_block_id, 0)
            self._block_counters[safe_block_id] += 1
            return f"CIT-{safe_block_id}-{self._block_counters[safe_block_id]:02d}"

    def get_next_citation_id(self, stage: str = "research", block_id: str = "") -> str:
        """Dispatch citation id generation based on stage."""

        if stage == "plan":
            return self.generate_plan_citation_id()
        if not block_id:
            raise ValueError("block_id is required for research citations.")
        return self.generate_research_citation_id(block_id)

    def add_citation(self, citation: ScholarCitation) -> int:
        """Register a citation and return its reference number."""

        with self._lock:
            if citation.citation_id in self._citations:
                existing = self._citations[citation.citation_id]
                existing.metadata.update(citation.metadata)
                if citation.title:
                    existing.title = citation.title
                if citation.url:
                    existing.url = citation.url
                if citation.snippet:
                    existing.snippet = citation.snippet
                if citation.source_type:
                    existing.source_type = citation.source_type
                return self.get_ref_number(citation.citation_id)
            self._citations[citation.citation_id] = citation
            self._ref_number_map.clear()
            return self.get_ref_number(citation.citation_id)

    def get_citation(self, citation_id: str) -> Optional[ScholarCitation]:
        """Fetch a citation by id."""

        return self._citations.get(citation_id)

    def build_ref_map(self) -> Dict[str, int]:
        """Rebuild the citation reference map."""

        with self._lock:
            self._ref_number_map = {
                citation_id: index + 1
                for index, citation_id in enumerate(self._citations.keys())
            }
            for citation_id, ref_number in self._ref_number_map.items():
                self._citations[citation_id].ref_number = ref_number
            return dict(self._ref_number_map)

    def build_ref_map_for(self, citation_ids: List[str]) -> Dict[str, int]:
        """Build reference numbers for a specific subset of citations.

        Args:
            citation_ids (List[str]): Citation ids to include in numbering.

        Returns:
            Dict[str, int]: Mapping of citation id to reference number.
        """

        with self._lock:
            ordered_ids = [cid for cid in citation_ids if cid in self._citations]
            self._ref_number_map = {cid: index + 1 for index, cid in enumerate(ordered_ids)}
            for citation in self._citations.values():
                citation.ref_number = self._ref_number_map.get(citation.citation_id)
            return dict(self._ref_number_map)

    def get_ref_number(self, citation_id: str) -> int:
        """Return the reference number for a citation."""

        with self._lock:
            if citation_id not in self._ref_number_map:
                self.build_ref_map()
            if citation_id not in self._ref_number_map:
                raise KeyError(f"Unknown citation id: {citation_id}")
            return self._ref_number_map[citation_id]

    def list_citations(self) -> List[ScholarCitation]:
        """Return all citations in insertion order."""

        return list(self._citations.values())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the citation manager state."""

        with self._lock:
            return {
                "research_id": self.research_id,
                "plan_counter": self._plan_counter,
                "block_counters": dict(self._block_counters),
                "ref_number_map": dict(self._ref_number_map),
                "citations": [c.to_dict() for c in self._citations.values()],
            }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any], cache_dir: Optional[Path] = None) -> "CitationManager":
        """Rehydrate a citation manager from persisted data."""

        manager = cls(payload["research_id"], cache_dir=cache_dir)
        manager._plan_counter = payload.get("plan_counter", 0)
        manager._block_counters = payload.get("block_counters", {})
        manager._ref_number_map = payload.get("ref_number_map", {})
        for item in payload.get("citations", []):
            citation = ScholarCitation.from_dict(item)
            manager._citations[citation.citation_id] = citation
        return manager

    def save_to_file(self, file_path: Path) -> None:
        """Persist citations to a JSON file."""

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load_from_file(cls, file_path: Path) -> "CitationManager":
        """Load citation data from a JSON file."""

        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload, cache_dir=file_path.parent)


class AsyncCitationManagerWrapper:
    """Async-safe wrapper around CitationManager."""

    def __init__(self, manager: CitationManager) -> None:
        """Wrap a CitationManager with async-friendly locking."""

        self._manager = manager
        self._lock = asyncio.Lock()

    async def generate_plan_citation_id(self) -> str:
        """Generate a plan citation id in async contexts."""

        async with self._lock:
            return self._manager.generate_plan_citation_id()

    async def generate_research_citation_id(self, block_id: str) -> str:
        """Generate a research citation id in async contexts."""

        async with self._lock:
            return self._manager.generate_research_citation_id(block_id)

    async def add_citation(self, citation: ScholarCitation) -> int:
        """Register a citation in async contexts."""

        async with self._lock:
            return self._manager.add_citation(citation)

    async def get_ref_number(self, citation_id: str) -> int:
        """Fetch a reference number in async contexts."""

        async with self._lock:
            return self._manager.get_ref_number(citation_id)

    async def snapshot(self) -> Dict[str, Any]:
        """Return a serializable snapshot of the citation state."""

        async with self._lock:
            return self._manager.to_dict()
