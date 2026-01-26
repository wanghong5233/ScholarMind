"""Archive export utilities for DeepResearch runs."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Iterable, Optional
from zipfile import ZIP_DEFLATED, ZipFile


def build_run_archive_zip(
    root_dir: Path,
    files: Iterable[str],
    *,
    manifest: Optional[dict] = None,
) -> bytes:
    """Build a zip archive from a list of file names under root_dir."""

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        if manifest:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for filename in files:
            path = root_dir / filename
            if not path.exists():
                continue
            archive.write(path, arcname=filename)
    buffer.seek(0)
    return buffer.read()


def build_block_evidence_zip(
    payload: dict,
    *,
    filename: Optional[str] = None,
    manifest: Optional[dict] = None,
) -> bytes:
    """Build a zip archive for block evidence."""

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        json_name = filename or "evidence.json"
        if manifest:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(json_name, json.dumps(payload, ensure_ascii=False, indent=2))
    buffer.seek(0)
    return buffer.read()


def compute_file_sha256(path: Path) -> Optional[str]:
    """Compute SHA256 hash for a file if it exists."""

    if not path.exists():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
