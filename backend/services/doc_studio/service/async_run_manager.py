"""Async run manager for Doc Studio tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import logging
import time

logger = logging.getLogger(__name__)


@dataclass
class AsyncRunState:
    """State of an async run."""

    run_id: str
    workspace_id: str
    user_id: int
    status: str
    created_at: float
    updated_at: float
    run_dir: Path
    events: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        """Return a serializable snapshot."""

        return {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
        }


class AsyncRunManager:
    """Manage async runs for Doc Studio."""

    def __init__(self) -> None:
        self._runs: Dict[str, AsyncRunState] = {}

    def create_run(
        self,
        *,
        run_id: str,
        workspace_id: str,
        user_id: int,
        run_dir: Path,
    ) -> AsyncRunState:
        """Create a new run and persist its initial state."""

        run_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        state = AsyncRunState(
            run_id=run_id,
            workspace_id=workspace_id,
            user_id=user_id,
            status="queued",
            created_at=now,
            updated_at=now,
            run_dir=run_dir,
        )
        self._runs[run_id] = state
        self._persist_status(state)
        return state

    def get_run(self, run_id: str) -> Optional[AsyncRunState]:
        """Get run state from memory."""

        return self._runs.get(run_id)

    def load_run(self, run_dir: Path, run_id: str) -> Optional[Dict[str, Any]]:
        """Load run snapshot from disk."""

        status_path = run_dir / f"{run_id}.json"
        if not status_path.exists():
            return None
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load async run snapshot: %s", exc)
            return None

    def update_status(self, run_id: str, status: str) -> None:
        """Update run status."""

        state = self._runs.get(run_id)
        if not state:
            return
        state.status = status
        state.updated_at = time.time()
        self._persist_status(state)

    def append_event(self, run_id: str, event_type: str, data: Dict[str, Any]) -> None:
        """Append an event and persist it."""

        state = self._runs.get(run_id)
        if not state:
            return
        event = {
            "event": event_type,
            "timestamp": time.time(),
            "data": data,
        }
        state.events.append(event)
        state.updated_at = event["timestamp"]
        self._persist_event(state, event)
        self._persist_status(state)

    def set_result(self, run_id: str, result: Dict[str, Any]) -> None:
        """Mark run as succeeded and save result."""

        state = self._runs.get(run_id)
        if not state:
            return
        state.result = result
        state.status = "succeeded"
        state.updated_at = time.time()
        self.append_event(run_id, "result", {"result": result})

    def set_error(self, run_id: str, error: str) -> None:
        """Mark run as failed and save error."""

        state = self._runs.get(run_id)
        if not state:
            return
        state.error = error
        state.status = "failed"
        state.updated_at = time.time()
        self.append_event(run_id, "run_error", {"error": error})

    def list_events(self, run_id: str) -> List[Dict[str, Any]]:
        """Return current events list."""

        state = self._runs.get(run_id)
        if not state:
            return []
        return list(state.events)

    def _persist_status(self, state: AsyncRunState) -> None:
        status_path = state.run_dir / f"{state.run_id}.json"
        status_path.write_text(
            json.dumps(state.snapshot(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _persist_event(self, state: AsyncRunState, event: Dict[str, Any]) -> None:
        events_path = state.run_dir / f"{state.run_id}.events.jsonl"
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str))
            fh.write("\n")


_async_run_manager: Optional[AsyncRunManager] = None


def get_async_run_manager() -> AsyncRunManager:
    """Get global async run manager instance."""

    global _async_run_manager
    if _async_run_manager is None:
        _async_run_manager = AsyncRunManager()
    return _async_run_manager
