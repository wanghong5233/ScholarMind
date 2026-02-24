"""Persistent state storage for DeepResearch runs."""

from __future__ import annotations

import json
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from core.config import settings


class StateStore:
    """Persist research state into structured JSON files."""

    _SESSION_INDEX_FILE = "_session_runs_index.json"
    _SESSION_INDEX_VERSION = 1

    def __init__(self, base_dir: Path, research_id: str) -> None:
        """Create a storage namespace for a research run.

        Args:
            base_dir (Path): Base storage directory.
            research_id (str): Research run id.
        """

        self.root = base_dir / research_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._last_progress_meta_update = 0.0

    def save_json(self, filename: str, payload: Dict[str, Any]) -> Path:
        """Persist a JSON payload under the research directory.

        Args:
            filename (str): File name to write.
            payload (Dict[str, Any]): JSON payload.

        Returns:
            Path: Path to the stored file.
        """

        path = self.root / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_meta(self, payload: Dict[str, Any]) -> Path:
        """Persist run metadata."""

        return self.save_json("meta.json", payload)

    def load_meta(self) -> Optional[Dict[str, Any]]:
        """Load run metadata if available."""

        return self.load_json("meta.json")

    def update_meta(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update run metadata with new fields."""

        current = self.load_meta() or {}
        current.update(payload)
        self.save_meta(current)
        return current

    def load_json(self, filename: str) -> Optional[Dict[str, Any]]:
        """Load a JSON payload if it exists.

        Args:
            filename (str): File name to load.

        Returns:
            Optional[Dict[str, Any]]: JSON payload or None.
        """

        path = self.root / filename
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def append_progress(self, payload: Dict[str, Any]) -> Path:
        """Append a progress event as JSONL for streaming trace.

        Args:
            payload (Dict[str, Any]): Progress payload.

        Returns:
            Path: Path to the progress log.
        """

        path = self.root / "progress.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._trim_progress_file(path)
        self._touch_progress_meta()
        return path

    def load_progress(self) -> List[Dict[str, Any]]:
        """Load progress events from the JSONL file.

        Returns:
            List[Dict[str, Any]]: Progress events.
        """

        path = self.root / "progress.jsonl"
        if not path.exists():
            return []
        events: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            events.append(json.loads(line))
        return events

    def load_progress_tail(self, max_items: int) -> List[Dict[str, Any]]:
        """Load the most recent progress events.

        Args:
            max_items (int): Maximum number of events to return.

        Returns:
            List[Dict[str, Any]]: Recent progress events.
        """

        if max_items <= 0:
            return []
        path = self.root / "progress.jsonl"
        if not path.exists():
            return []
        buffer: Deque[Dict[str, Any]] = deque(maxlen=max_items)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            buffer.append(json.loads(line))
        return list(buffer)

    def _trim_progress_file(self, path: Path) -> None:
        """Trim the progress file when it exceeds the configured size."""

        max_bytes = settings.PROGRESS_MAX_BYTES
        max_lines = settings.PROGRESS_TAIL_LINES
        if max_bytes <= 0:
            return
        if not path.exists():
            return
        file_size = path.stat().st_size
        if file_size <= max_bytes:
            return
        tail_data = self._read_tail_lines(path, max_lines, settings.PROGRESS_TRIM_CHUNK_SIZE)
        tail_data = self._enforce_max_bytes(tail_data, max_bytes)
        temp_path = path.with_suffix(".jsonl.tmp")
        temp_path.write_bytes(tail_data)
        temp_path.replace(path)

    def _touch_progress_meta(self) -> None:
        """Update meta with the latest progress timestamp."""

        throttle = settings.PROGRESS_META_THROTTLE_SECONDS
        now_mono = time.monotonic()
        if throttle > 0 and now_mono - self._last_progress_meta_update < throttle:
            return
        now_iso = datetime.utcnow().isoformat()
        self.update_meta({"last_progress_at": now_iso})
        self._last_progress_meta_update = now_mono

    @staticmethod
    def _read_tail_lines(path: Path, max_lines: int, chunk_size: int) -> bytes:
        """Read the last N lines from a text file as bytes."""

        if max_lines <= 0:
            return b""
        chunk_size = max(1024, chunk_size)
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            if position <= 0:
                return b""
            buffer = b""
            while position > 0 and buffer.count(b"\n") <= max_lines:
                read_size = min(chunk_size, position)
                position -= read_size
                handle.seek(position)
                buffer = handle.read(read_size) + buffer
                if position == 0:
                    break
            lines = buffer.splitlines()
            if position > 0 and lines:
                lines = lines[1:]
            if len(lines) > max_lines:
                lines = lines[-max_lines:]
            if not lines:
                return b""
            return b"\n".join(lines) + b"\n"

    @staticmethod
    def _enforce_max_bytes(payload: bytes, max_bytes: int) -> bytes:
        """Ensure trimmed payload never exceeds the byte budget."""

        if max_bytes <= 0:
            return b""
        if len(payload) <= max_bytes:
            return payload
        lines = [line for line in payload.splitlines() if line.strip()]
        if not lines:
            return b""
        kept_reversed: List[bytes] = []
        used = 0
        for line in reversed(lines):
            line_size = len(line) + 1  # trailing newline
            if line_size > max_bytes:
                # Single oversized line cannot fit budget; drop it.
                continue
            if used + line_size > max_bytes:
                break
            kept_reversed.append(line)
            used += line_size
        if not kept_reversed:
            return b""
        return b"\n".join(reversed(kept_reversed)) + b"\n"

    def get_progress_offset(self) -> int:
        """Return the current byte offset of the progress file."""

        path = self.root / "progress.jsonl"
        if not path.exists():
            return 0
        return path.stat().st_size

    def read_progress_since(
        self,
        offset: int,
        limit: Optional[int] = None,
    ) -> tuple[List[tuple[Dict[str, Any], int]], int]:
        """Read progress events from a given file offset.

        Args:
            offset (int): File offset to start reading.

        Returns:
            tuple[List[tuple[Dict[str, Any], int]], int]: Events with offsets and new offset.
        """

        path = self.root / "progress.jsonl"
        if not path.exists():
            return [], offset
        file_size = path.stat().st_size
        if offset > file_size:
            offset = 0
        events: List[tuple[Dict[str, Any], int]] = []
        with path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            new_offset = offset
            while True:
                line_start = handle.tell()
                line = handle.readline()
                if not line:
                    new_offset = handle.tell()
                    break
                line_end = handle.tell()
                if not line.endswith("\n"):
                    # Guard against partially written lines during concurrent append.
                    handle.seek(line_start)
                    new_offset = line_start
                    break
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    # Skip malformed lines but keep stream alive.
                    continue
                events.append((payload, line_end))
                new_offset = line_end
                if limit is not None and limit > 0 and len(events) >= limit:
                    break
        return events, new_offset

    @staticmethod
    def list_runs(base_dir: Path) -> List[Dict[str, Any]]:
        """List DeepResearch runs from the data directory."""

        if not base_dir.exists():
            return []
        runs: List[Dict[str, Any]] = []
        for entry in base_dir.iterdir():
            if not entry.is_dir():
                continue
            meta_file = entry / "meta.json"
            if not meta_file.exists():
                continue
            try:
                payload = json.loads(meta_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            payload.setdefault("research_id", entry.name)
            runs.append(payload)
        runs.sort(key=lambda item: item.get("started_at", ""), reverse=True)
        return runs

    @staticmethod
    def register_session_run(
        base_dir: Path,
        *,
        session_id: Optional[str],
        research_id: str,
        user_id: Optional[int] = None,
        topic: Optional[str] = None,
        submitted_at: Optional[str] = None,
    ) -> None:
        """Register a run under a session index for fast lookups.

        Args:
            base_dir (Path): Root storage directory.
            session_id (Optional[str]): Session id bound to the run.
            research_id (str): DeepResearch run id.
            user_id (Optional[int]): Run owner id.
            topic (Optional[str]): Run topic.
            submitted_at (Optional[str]): Submission timestamp.
        """

        normalized_session = str(session_id or "").strip()
        normalized_research = str(research_id or "").strip()
        if not normalized_session or not normalized_research:
            return
        now_iso = datetime.utcnow().isoformat()
        index_payload = StateStore._load_session_index(base_dir)
        sessions = index_payload.setdefault("sessions", {})
        current_items = sessions.get(normalized_session)
        if not isinstance(current_items, list):
            current_items = []
        filtered_items = [
            item
            for item in current_items
            if str((item or {}).get("research_id") or "").strip() != normalized_research
        ]
        filtered_items.append(
            {
                "research_id": normalized_research,
                "user_id": user_id,
                "topic": topic or "",
                "submitted_at": submitted_at or "",
                "updated_at": now_iso,
            }
        )
        max_runs = max(20, int(getattr(settings, "SESSION_INDEX_MAX_RUNS_PER_SESSION", 200) or 200))
        if len(filtered_items) > max_runs:
            filtered_items = filtered_items[-max_runs:]
        sessions[normalized_session] = filtered_items
        index_payload["updated_at"] = now_iso
        StateStore._write_json_atomic(
            StateStore._session_index_path(base_dir),
            index_payload,
        )

    @staticmethod
    def list_runs_by_session(
        base_dir: Path,
        *,
        session_id: str,
        user_id: Optional[int] = None,
        limit: Optional[int] = 20,
    ) -> List[Dict[str, Any]]:
        """List DeepResearch runs belonging to a specific chat session.

        Args:
            base_dir (Path): Root storage directory.
            session_id (str): Chat session id.
            user_id (Optional[int]): Optional user ownership filter.
            limit (Optional[int]): Max runs to return.

        Returns:
            List[Dict[str, Any]]: Matched run metadata sorted by recency.
        """

        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            return []

        def match_owner(meta: Dict[str, Any]) -> bool:
            if user_id is None:
                return True
            return str(meta.get("user_id")) == str(user_id)

        def match_session(meta: Dict[str, Any]) -> bool:
            request_payload = meta.get("request")
            if not isinstance(request_payload, dict):
                return False
            request_session = str(request_payload.get("session_id") or "").strip()
            return request_session == normalized_session

        index_payload = StateStore._load_session_index(base_dir)
        sessions = index_payload.get("sessions", {})
        session_items = sessions.get(normalized_session) if isinstance(sessions, dict) else []
        run_ids: List[str] = []
        if isinstance(session_items, list):
            for item in reversed(session_items):
                run_id = str((item or {}).get("research_id") or "").strip()
                if not run_id or run_id in run_ids:
                    continue
                run_ids.append(run_id)
        runs: List[Dict[str, Any]] = []
        if run_ids:
            for run_id in run_ids:
                meta_file = base_dir / run_id / "meta.json"
                if not meta_file.exists():
                    continue
                try:
                    payload = json.loads(meta_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                payload.setdefault("research_id", run_id)
                if not match_owner(payload):
                    continue
                if not match_session(payload):
                    continue
                runs.append(payload)
                if limit and limit > 0 and len(runs) >= limit:
                    return runs

        # Fallback for historical runs before the index existed.
        fallback = StateStore.list_runs(base_dir)
        existing_ids = {
            str(item.get("research_id") or "").strip()
            for item in runs
            if item.get("research_id")
        }
        filtered = [
            item
            for item in fallback
            if match_owner(item)
            and match_session(item)
            and str(item.get("research_id") or "").strip() not in existing_ids
        ]
        merged = runs + filtered
        merged.sort(key=StateStore._run_sort_key, reverse=True)
        if limit and limit > 0:
            return merged[:limit]
        return merged

    @staticmethod
    def list_runs_by_meta(base_dir: Path, meta_filename: str) -> List[Dict[str, Any]]:
        """List runs based on a custom meta file name.

        Args:
            base_dir (Path): Root storage directory.
            meta_filename (str): Meta file name to look for.

        Returns:
            List[Dict[str, Any]]: Parsed meta payloads.
        """

        if not base_dir.exists():
            return []
        runs: List[Dict[str, Any]] = []
        for entry in base_dir.iterdir():
            if not entry.is_dir():
                continue
            meta_file = entry / meta_filename
            if not meta_file.exists():
                continue
            try:
                payload = json.loads(meta_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            runs.append(payload)
        runs.sort(key=lambda item: item.get("started_at", ""), reverse=True)
        return runs

    @staticmethod
    def _run_sort_key(meta: Dict[str, Any]) -> str:
        """Build a stable timestamp key for run ordering."""

        return str(
            meta.get("finished_at")
            or meta.get("started_at")
            or meta.get("submitted_at")
            or ""
        )

    @staticmethod
    def _session_index_path(base_dir: Path) -> Path:
        return base_dir / StateStore._SESSION_INDEX_FILE

    @staticmethod
    def _load_session_index(base_dir: Path) -> Dict[str, Any]:
        """Load session index payload from disk."""

        index_path = StateStore._session_index_path(base_dir)
        if not index_path.exists():
            return {
                "version": StateStore._SESSION_INDEX_VERSION,
                "sessions": {},
                "updated_at": None,
            }
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("version", StateStore._SESSION_INDEX_VERSION)
        payload.setdefault("sessions", {})
        payload.setdefault("updated_at", None)
        if not isinstance(payload.get("sessions"), dict):
            payload["sessions"] = {}
        return payload

    @staticmethod
    def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
        """Write JSON payload atomically."""

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
