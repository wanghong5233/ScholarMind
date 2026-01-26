"""Persistent state storage for DeepResearch runs."""

from __future__ import annotations

import json
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from config import settings


class StateStore:
    """Persist research state into structured JSON files."""

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
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                events.append((payload, handle.tell()))
                if limit is not None and limit > 0 and len(events) >= limit:
                    break
            new_offset = handle.tell()
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
