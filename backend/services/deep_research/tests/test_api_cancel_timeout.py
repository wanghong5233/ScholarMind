"""API tests for cancellation and timeout behaviors."""

import asyncio
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from config import settings
from main import app
from router import research_rt
from schemas.common import DeepResearchStatus
from service.pipeline import ResearchPipeline
from service.run_manager import RunManager
from service.state_store import StateStore


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
    monkeypatch.setattr(settings, "RUN_WATCHDOG_INTERVAL_SECONDS", 1)
    monkeypatch.setattr(settings, "RUN_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(settings, "RUN_IDLE_TIMEOUT_SECONDS", 0)

    manager = RunManager(
        rag_service_url="http://example",
        data_root=str(tmp_path),
        request_timeout=10,
    )

    async def noop_start_scheduler():
        return None

    async def fake_run(self, request, user_id, research_id=None, resume=False):  # noqa: ARG001
        await run_blocker.wait()
        return None

    monkeypatch.setattr(manager, "start_scheduler", noop_start_scheduler)
    monkeypatch.setattr(research_rt, "run_manager", manager)
    monkeypatch.setattr(ResearchPipeline, "run", fake_run)

    transport = ASGITransport(app=app, lifespan="on")
    client = AsyncClient(transport=transport, base_url="http://test")
    return client, manager


@pytest.mark.asyncio
async def test_cancel_running_sets_cancel_requested(tmp_path, monkeypatch) -> None:
    run_blocker = asyncio.Event()
    client, manager = await _build_client(tmp_path, monkeypatch, run_blocker)
    headers = {"X-User-Id": "1"}
    try:
        submit = await client.post(
            "/api/deep-research/submit",
            json={"topic": "Topic A", "mode": "queue"},
            headers=headers,
        )
        run_id = submit.json()["research_id"]
        await _wait_until(lambda: manager.is_active(run_id))

        cancel = await client.post(
            f"/api/deep-research/{run_id}/cancel",
            headers=headers,
        )
        assert cancel.status_code == 200
        payload = cancel.json()
        assert payload["message"] == "cancel_requested"

        meta = StateStore(tmp_path, run_id).load_meta() or {}
        assert meta.get("cancel_requested_at")
        assert meta.get("cancel_reason") == "user_cancel"
    finally:
        run_blocker.set()
        await _wait_until(lambda: not manager.is_active(run_id))
        await client.aclose()


@pytest.mark.asyncio
async def test_watchdog_timeout_cancels_run(tmp_path, monkeypatch) -> None:
    run_blocker = asyncio.Event()
    client, manager = await _build_client(tmp_path, monkeypatch, run_blocker)
    headers = {"X-User-Id": "1"}
    monkeypatch.setattr(settings, "RUN_TIMEOUT_SECONDS", 1)
    try:
        submit = await client.post(
            "/api/deep-research/submit",
            json={"topic": "Topic A", "mode": "queue"},
            headers=headers,
        )
        run_id = submit.json()["research_id"]
        await _wait_until(lambda: manager.is_active(run_id))
        await _wait_until(
            lambda: StateStore(tmp_path, run_id).load_meta().get("cancel_reason") == "timeout",
            timeout=2.0,
        )
    finally:
        run_blocker.set()
        await _wait_until(lambda: not manager.is_active(run_id))
        await client.aclose()


@pytest.mark.asyncio
async def test_watchdog_idle_timeout_cancels_run(tmp_path, monkeypatch) -> None:
    run_blocker = asyncio.Event()
    client, manager = await _build_client(tmp_path, monkeypatch, run_blocker)
    headers = {"X-User-Id": "1"}
    monkeypatch.setattr(settings, "RUN_IDLE_TIMEOUT_SECONDS", 1)
    try:
        submit = await client.post(
            "/api/deep-research/submit",
            json={"topic": "Topic A", "mode": "queue"},
            headers=headers,
        )
        run_id = submit.json()["research_id"]
        await _wait_until(lambda: manager.is_active(run_id))

        store = StateStore(tmp_path, run_id)
        store.update_meta({"last_progress_at": datetime.utcnow().isoformat()})
        await _wait_until(
            lambda: StateStore(tmp_path, run_id).load_meta().get("cancel_reason") == "idle_timeout",
            timeout=2.0,
        )
    finally:
        run_blocker.set()
        await _wait_until(lambda: not manager.is_active(run_id))
        await client.aclose()
