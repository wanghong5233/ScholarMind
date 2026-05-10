"""Run-level cancellation control for session ask streams."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Dict, Optional


@dataclass
class AskRunState:
    run_id: str
    session_id: str
    user_id: int
    cancelled: bool
    created_at: float
    updated_at: float


class AskRunControl:
    """In-memory cancellation state for active ask runs."""

    def __init__(self, *, ttl_seconds: int = 600) -> None:
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._lock = threading.RLock()
        self._runs: Dict[str, AskRunState] = {}
        # A user may cancel before the stream generator actually starts.
        # Keep a pending cancel marker and apply it once the run is registered.
        self._pending_cancels: Dict[str, AskRunState] = {}

    def register_run(self, *, run_id: str, session_id: str, user_id: int) -> None:
        now = time.time()
        with self._lock:
            self._cleanup_locked(now)
            cancelled = False
            pending = self._pending_cancels.get(run_id)
            if pending and str(pending.session_id) == str(session_id) and int(pending.user_id) == int(user_id):
                cancelled = True
                self._pending_cancels.pop(run_id, None)
            self._runs[run_id] = AskRunState(
                run_id=run_id,
                session_id=session_id,
                user_id=int(user_id),
                cancelled=cancelled,
                created_at=now,
                updated_at=now,
            )

    def cancel_run(self, *, run_id: str, session_id: str, user_id: int) -> bool:
        now = time.time()
        with self._lock:
            self._cleanup_locked(now)
            run = self._runs.get(run_id)
            if not run:
                # Run is not registered yet; store pending cancel so registration
                # can inherit cancelled state immediately.
                self._pending_cancels[run_id] = AskRunState(
                    run_id=run_id,
                    session_id=str(session_id),
                    user_id=int(user_id),
                    cancelled=True,
                    created_at=now,
                    updated_at=now,
                )
                return True
            if str(run.session_id) != str(session_id):
                return False
            if int(run.user_id) != int(user_id):
                return False
            run.cancelled = True
            run.updated_at = now
            return True

    def cancel_runs_for_session(self, *, session_id: str, user_id: int) -> int:
        """Cancel all active runs for a session/user pair."""
        now = time.time()
        cancelled = 0
        with self._lock:
            self._cleanup_locked(now)
            for run in self._runs.values():
                if str(run.session_id) != str(session_id):
                    continue
                if int(run.user_id) != int(user_id):
                    continue
                if run.cancelled:
                    continue
                run.cancelled = True
                run.updated_at = now
                cancelled += 1
        return cancelled

    def is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                pending = self._pending_cancels.get(run_id)
                return bool(pending and pending.cancelled)
            return bool(run.cancelled)

    def clear_run(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)
            self._pending_cancels.pop(run_id, None)

    def get_run(self, run_id: str) -> Optional[AskRunState]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                return run
            return self._pending_cancels.get(run_id)

    def _cleanup_locked(self, now: float) -> None:
        expired_runs = [
            run_id
            for run_id, run in self._runs.items()
            if now - float(run.updated_at) > self._ttl_seconds
        ]
        for run_id in expired_runs:
            self._runs.pop(run_id, None)
        expired_pending = [
            run_id
            for run_id, run in self._pending_cancels.items()
            if now - float(run.updated_at) > self._ttl_seconds
        ]
        for run_id in expired_pending:
            self._pending_cancels.pop(run_id, None)


_ask_run_control: Optional[AskRunControl] = None


def get_ask_run_control() -> AskRunControl:
    global _ask_run_control
    if _ask_run_control is None:
        _ask_run_control = AskRunControl()
    return _ask_run_control
