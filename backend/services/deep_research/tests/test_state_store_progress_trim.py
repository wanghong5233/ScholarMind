"""Tests for StateStore progress trimming."""

from datetime import datetime

from config import settings
from service.state_store import StateStore


def test_progress_trim_and_offset_reset(tmp_path, monkeypatch) -> None:
    """Ensure progress logs trim and offset resets when past EOF."""

    monkeypatch.setattr(settings, "PROGRESS_MAX_BYTES", 200)
    monkeypatch.setattr(settings, "PROGRESS_TAIL_LINES", 3)
    monkeypatch.setattr(settings, "PROGRESS_TRIM_CHUNK_SIZE", 64)
    monkeypatch.setattr(settings, "PROGRESS_META_THROTTLE_SECONDS", 0)

    store = StateStore(tmp_path, "dr_trim")
    store.save_meta(
        {
            "research_id": "dr_trim",
            "status": "running",
            "user_id": 1,
        }
    )

    for idx in range(10):
        store.append_progress(
            {
                "research_id": "dr_trim",
                "stage": "test",
                "message": f"event-{idx}",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {"index": idx},
            }
        )

    progress_path = store.root / "progress.jsonl"
    assert progress_path.exists()
    assert progress_path.stat().st_size <= settings.PROGRESS_MAX_BYTES

    items = store.load_progress()
    assert len(items) <= settings.PROGRESS_TAIL_LINES

    over_offset = progress_path.stat().st_size + 1000
    events, new_offset = store.read_progress_since(over_offset)
    assert events
    assert new_offset <= progress_path.stat().st_size
