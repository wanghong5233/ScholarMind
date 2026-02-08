"""Tests for RunManager scheduling behavior."""

import asyncio
import time
from datetime import datetime

import pytest

from core.config import settings
from schemas.common import DeepResearchMode, DeepResearchRequest
from service.pipeline import ResearchPipeline
from service.run_manager import RunManager
from service.state_store import StateStore


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met before timeout")


@pytest.mark.asyncio
async def test_run_manager_schedules_and_cleans(tmp_path, monkeypatch) -> None:
    """Ensure queued runs start and clean up queue entry."""

    async def fake_run(self, request, user_id, research_id=None, resume=False):  # noqa: ARG001
        store = StateStore(tmp_path, research_id)
        store.update_meta(
            {
                "status": "completed",
                "finished_at": datetime.utcnow().isoformat(),
            }
        )
        return None

    monkeypatch.setattr(ResearchPipeline, "run", fake_run)
    original_backend = settings.QUEUE_BACKEND
    original_max_active = settings.MAX_ACTIVE_RUNS
    try:
        settings.QUEUE_BACKEND = "sqlite"
        settings.MAX_ACTIVE_RUNS = 1
        manager = RunManager(
            rag_service_url="http://example",
            data_root=str(tmp_path),
            request_timeout=10,
        )
        request = DeepResearchRequest(topic="topic", mode=DeepResearchMode.QUEUE)
        run_id, status, _, _, _ = await manager.submit(request, user_id=1)
        assert status.value in {"queued", "running"}

        await _wait_until(lambda: not manager.is_active(run_id), timeout=1.0)
        assert manager._queue_store.get_status(run_id) is None

        meta = StateStore(tmp_path, run_id).load_meta() or {}
        assert meta.get("status") == "completed"
        assert meta.get("finished_at")
    finally:
        settings.QUEUE_BACKEND = original_backend
        settings.MAX_ACTIVE_RUNS = original_max_active


@pytest.mark.asyncio
async def test_run_manager_cancels_on_lost_lease(tmp_path, monkeypatch) -> None:
    """Ensure local task cancels when ownership is lost."""

    run_blocker = asyncio.Event()

    async def fake_run(self, request, user_id, research_id=None, resume=False):  # noqa: ARG001
        await run_blocker.wait()
        return None

    monkeypatch.setattr(ResearchPipeline, "run", fake_run)
    original_backend = settings.QUEUE_BACKEND
    original_max_active = settings.MAX_ACTIVE_RUNS
    try:
        settings.QUEUE_BACKEND = "sqlite"
        settings.MAX_ACTIVE_RUNS = 1
        manager = RunManager(
            rag_service_url="http://example",
            data_root=str(tmp_path),
            request_timeout=10,
        )
        request = DeepResearchRequest(topic="topic", mode=DeepResearchMode.QUEUE)
        run_id, _, _, _, _ = await manager.submit(request, user_id=1)

        await _wait_until(lambda: manager.is_active(run_id), timeout=1.0)
        meta = StateStore(tmp_path, run_id).load_meta() or {}
        now = datetime.utcnow().isoformat()
        priority = int(meta.get("priority") or 0)

        manager._queue_store.requeue(run_id, priority, now)
        async def _noop_schedule():
            return None
        manager._schedule_once = _noop_schedule  # type: ignore[assignment]
        await manager._cancel_lost_leases()
        await _wait_until(lambda: not manager.is_active(run_id), timeout=1.0)

        meta_after = StateStore(tmp_path, run_id).load_meta() or {}
        assert meta_after.get("cancel_reason") is None
        assert meta_after.get("finished_at") is None
        assert meta_after.get("status") == "running"
    finally:
        settings.QUEUE_BACKEND = original_backend
        settings.MAX_ACTIVE_RUNS = original_max_active
