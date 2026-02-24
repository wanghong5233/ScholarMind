"""API tests for DeepResearch queue endpoints."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from httpx import AsyncClient

from core.config import settings
from main import app
from router import research_rt
from service.pipeline import ResearchPipeline
from service.run_manager import RunManager
from service.state_store import StateStore
from tests.httpx_compat import create_asgi_transport


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met before timeout")


async def _build_client(tmp_path, monkeypatch, run_blocker: asyncio.Event) -> tuple[AsyncClient, RunManager]:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "QUEUE_BACKEND", "sqlite")
    monkeypatch.setattr(settings, "MAX_ACTIVE_RUNS", 1)
    monkeypatch.setattr(settings, "AUTO_RECOVER_RUNS", False)

    manager = RunManager(
        rag_service_url="http://example",
        data_root=str(tmp_path),
        request_timeout=10,
    )

    async def noop_start_scheduler():
        return None

    async def fake_run(self, request, user_id, research_id=None, resume=False):  # noqa: ARG001
        await run_blocker.wait()
        store = StateStore(Path(settings.DATA_ROOT), research_id)
        store.update_meta(
            {
                "status": "completed",
                "finished_at": datetime.utcnow().isoformat(),
            }
        )
        return None

    monkeypatch.setattr(manager, "start_scheduler", noop_start_scheduler)
    monkeypatch.setattr(research_rt, "run_manager", manager)
    monkeypatch.setattr(ResearchPipeline, "run", fake_run)

    transport = create_asgi_transport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return client, manager


@pytest.mark.asyncio
async def test_submit_queue_and_cancel(tmp_path, monkeypatch) -> None:
    run_blocker = asyncio.Event()
    client, manager = await _build_client(tmp_path, monkeypatch, run_blocker)
    headers = {"X-User-Id": "1"}

    try:
        first = await client.post(
            "/api/deep-research/submit",
            json={"topic": "Topic A", "mode": "queue"},
            headers=headers,
        )
        assert first.status_code == 200
        run_id = first.json()["research_id"]
        await _wait_until(lambda: manager.is_active(run_id))

        second = await client.post(
            "/api/deep-research/submit",
            json={"topic": "Topic B", "mode": "queue"},
            headers=headers,
        )
        assert second.status_code == 200
        second_payload = second.json()
        assert second_payload["status"] == "queued"
        queued_id = second_payload["research_id"]

        queue = await client.get("/api/deep-research/queue", headers=headers)
        assert queue.status_code == 200
        queue_payload = queue.json()
        assert queue_payload["active_runs"] == 1
        assert queue_payload["pending_runs"] == 1
        assert queue_payload["active_items"][0]["research_id"] == run_id
        assert queue_payload["pending_items"][0]["research_id"] == queued_id

        cancel = await client.post(
            f"/api/deep-research/{queued_id}/cancel",
            headers=headers,
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        queue_after = await client.get("/api/deep-research/queue", headers=headers)
        assert queue_after.status_code == 200
        assert queue_after.json()["pending_runs"] == 0
    finally:
        run_blocker.set()
        await _wait_until(lambda: not manager.is_active(run_id))
        await client.aclose()


@pytest.mark.asyncio
async def test_update_priority_endpoint(tmp_path, monkeypatch) -> None:
    run_blocker = asyncio.Event()
    client, manager = await _build_client(tmp_path, monkeypatch, run_blocker)
    headers = {"X-User-Id": "1"}

    try:
        first = await client.post(
            "/api/deep-research/submit",
            json={"topic": "Topic A", "mode": "queue"},
            headers=headers,
        )
        run_id = first.json()["research_id"]
        await _wait_until(lambda: manager.is_active(run_id))

        second = await client.post(
            "/api/deep-research/submit",
            json={"topic": "Topic B", "mode": "queue", "metadata": {"priority": -5}},
            headers=headers,
        )
        queued_low = second.json()["research_id"]

        third = await client.post(
            "/api/deep-research/submit",
            json={"topic": "Topic C", "mode": "queue", "metadata": {"priority": 5}},
            headers=headers,
        )
        queued_high = third.json()["research_id"]

        update = await client.patch(
            f"/api/deep-research/{queued_low}/priority",
            json={"priority": 10},
            headers=headers,
        )
        assert update.status_code == 200
        assert update.json()["queue_position"] == 1

        queue = await client.get("/api/deep-research/queue", headers=headers)
        pending_ids = [item["research_id"] for item in queue.json()["pending_items"]]
        assert pending_ids[0] == queued_low
        assert queued_high in pending_ids
    finally:
        run_blocker.set()
        await _wait_until(lambda: not manager.is_active(run_id))
        await client.aclose()
