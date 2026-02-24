"""API tests for progress, resume, and replay endpoints."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.config import settings
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


async def _build_client(tmp_path, monkeypatch) -> AsyncClient:
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

    async def noop_schedule_once():
        return None

    async def fake_run(self, request, user_id, research_id=None, resume=False):  # noqa: ARG001
        store = StateStore(Path(settings.DATA_ROOT), research_id)
        store.update_meta(
            {
                "status": "completed",
                "finished_at": datetime.utcnow().isoformat(),
            }
        )
        return None

    monkeypatch.setattr(manager, "start_scheduler", noop_start_scheduler)
    monkeypatch.setattr(manager, "_schedule_once", noop_schedule_once)
    monkeypatch.setattr(research_rt, "run_manager", manager)
    monkeypatch.setattr(ResearchPipeline, "run", fake_run)

    try:
        transport = ASGITransport(app=app, lifespan="on")
    except TypeError:
        transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_progress_endpoints(tmp_path, monkeypatch) -> None:
    client = await _build_client(tmp_path, monkeypatch)
    headers = {"X-User-Id": "1"}
    try:
        research_id = "dr_progress"
        store = StateStore(tmp_path, research_id)
        store.save_meta(
            {
                "research_id": research_id,
                "status": DeepResearchStatus.RUNNING.value,
                "user_id": 1,
                "request": {"topic": "Topic", "mode": "queue"},
            }
        )
        store.append_progress(
            {
                "research_id": research_id,
                "stage": "planning",
                "message": "plan",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {},
            }
        )
        store.append_progress(
            {
                "research_id": research_id,
                "stage": "researching",
                "message": "research",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {},
            }
        )

        tail = await client.get(
            f"/api/deep-research/{research_id}/progress",
            params={"tail": 1},
            headers=headers,
        )
        assert tail.status_code == 200
        tail_payload = tail.json()
        assert len(tail_payload["items"]) == 1
        assert tail_payload["next_offset"] > 0

        since = await client.get(
            f"/api/deep-research/{research_id}/progress/since",
            params={"offset": 0, "limit": 10},
            headers=headers,
        )
        assert since.status_code == 200
        since_payload = since.json()
        assert len(since_payload["items"]) == 2
        next_offset = since_payload["next_offset"]

        empty = await client.get(
            f"/api/deep-research/{research_id}/progress/since",
            params={"offset": next_offset, "limit": 10},
            headers=headers,
        )
        assert empty.status_code == 200
        assert empty.json()["items"] == []

        reset = await client.get(
            f"/api/deep-research/{research_id}/progress/since",
            params={"offset": next_offset + 10_000, "limit": 10},
            headers=headers,
        )
        assert reset.status_code == 200
        assert len(reset.json()["items"]) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_progress_endpoints_forbidden(tmp_path, monkeypatch) -> None:
    client = await _build_client(tmp_path, monkeypatch)
    headers = {"X-User-Id": "1"}
    try:
        research_id = "dr_progress_forbidden"
        store = StateStore(tmp_path, research_id)
        store.save_meta(
            {
                "research_id": research_id,
                "status": DeepResearchStatus.RUNNING.value,
                "user_id": 2,
                "request": {"topic": "Topic", "mode": "queue"},
            }
        )
        tail = await client.get(
            f"/api/deep-research/{research_id}/progress",
            params={"tail": 1},
            headers=headers,
        )
        assert tail.status_code == 403

        since = await client.get(
            f"/api/deep-research/{research_id}/progress/since",
            params={"offset": 0, "limit": 10},
            headers=headers,
        )
        assert since.status_code == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_progress_endpoints_not_found(tmp_path, monkeypatch) -> None:
    client = await _build_client(tmp_path, monkeypatch)
    headers = {"X-User-Id": "1"}
    try:
        research_id = "dr_missing"
        tail = await client.get(
            f"/api/deep-research/{research_id}/progress",
            params={"tail": 1},
            headers=headers,
        )
        assert tail.status_code == 404

        since = await client.get(
            f"/api/deep-research/{research_id}/progress/since",
            params={"offset": 0, "limit": 10},
            headers=headers,
        )
        assert since.status_code == 404
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_replay_and_resume(tmp_path, monkeypatch) -> None:
    client = await _build_client(tmp_path, monkeypatch)
    headers = {"X-User-Id": "1"}
    try:
        base_id = "dr_base"
        store = StateStore(tmp_path, base_id)
        store.save_meta(
            {
                "research_id": base_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Base",
                "user_id": 1,
                "request": {"topic": "Base", "mode": "queue"},
            }
        )

        replay = await client.post(
            f"/api/deep-research/{base_id}/replay",
            headers=headers,
        )
        assert replay.status_code == 200
        replay_payload = replay.json()
        assert replay_payload["message"] == "replay_submitted"
        replay_id = replay_payload["research_id"]
        replay_meta = StateStore(tmp_path, replay_id).load_meta() or {}
        assert replay_meta.get("request", {}).get("metadata", {}).get("replay_from") == base_id

        resume_id = "dr_resume"
        resume_store = StateStore(tmp_path, resume_id)
        resume_store.save_meta(
            {
                "research_id": resume_id,
                "status": DeepResearchStatus.FAILED.value,
                "topic": "Resume",
                "user_id": 1,
                "request": {"topic": "Resume", "mode": "queue"},
            }
        )
        resume_store.save_json("queue.json", {"blocks": []})

        resume = await client.post(
            f"/api/deep-research/{resume_id}/resume",
            headers=headers,
        )
        assert resume.status_code == 200
        resume_payload = resume.json()
        assert resume_payload["message"] == "resumed"

        await _wait_until(
            lambda: StateStore(tmp_path, resume_id).load_meta().get("resume_pending") is True
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_snapshot_endpoint_requires_owner(tmp_path, monkeypatch) -> None:
    client = await _build_client(tmp_path, monkeypatch)
    try:
        research_id = "dr_snapshot_forbidden"
        store = StateStore(tmp_path, research_id)
        store.save_meta(
            {
                "research_id": research_id,
                "status": DeepResearchStatus.RUNNING.value,
                "user_id": 2,
                "request": {"topic": "Topic", "mode": "queue"},
            }
        )
        store.save_json("report.json", {"report_markdown": "hello"})

        res = await client.get(
            f"/api/deep-research/{research_id}/snapshot",
            headers={"X-User-Id": "1"},
        )
        assert res.status_code == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_snapshot_endpoint_returns_payload_for_owner(tmp_path, monkeypatch) -> None:
    client = await _build_client(tmp_path, monkeypatch)
    try:
        research_id = "dr_snapshot_owner"
        store = StateStore(tmp_path, research_id)
        store.save_meta(
            {
                "research_id": research_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "user_id": 1,
                "request": {"topic": "Topic", "mode": "queue"},
            }
        )
        store.save_json("outline.json", {"items": []})
        store.save_json("queue.json", {"blocks": []})
        store.save_json("citations.json", {"citations": []})
        store.save_json("report.json", {"report_markdown": "# report"})

        res = await client.get(
            f"/api/deep-research/{research_id}/snapshot",
            headers={"X-User-Id": "1"},
        )
        assert res.status_code == 200
        payload = res.json()
        assert payload["research_id"] == research_id
        assert payload["report"]["report_markdown"] == "# report"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_session_runs_and_context_endpoints(tmp_path, monkeypatch) -> None:
    client = await _build_client(tmp_path, monkeypatch)
    headers = {"X-User-Id": "1"}
    try:
        session_id = "sess_ctx_001"
        completed_id = "dr_ctx_completed"
        running_id = "dr_ctx_running"
        foreign_id = "dr_ctx_foreign"
        other_session_id = "dr_ctx_other_session"
        other_session_run = "dr_ctx_other"

        completed_store = StateStore(tmp_path, completed_id)
        completed_store.save_meta(
            {
                "research_id": completed_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Completed topic",
                "user_id": 1,
                "submitted_at": "2026-02-21T10:00:00",
                "finished_at": "2026-02-21T10:10:00",
                "request": {
                    "topic": "Completed topic",
                    "mode": "queue",
                    "session_id": session_id,
                },
            }
        )
        completed_store.save_json(
            "report.json",
            {"report_markdown": "# 结论\n这是已完成深度研究报告摘要。"},
        )
        completed_store.save_json(
            "citations.json",
            {"citations": [{"citation_id": "c1"}]},
        )
        StateStore.register_session_run(
            tmp_path,
            session_id=session_id,
            research_id=completed_id,
            user_id=1,
            topic="Completed topic",
            submitted_at="2026-02-21T10:00:00",
        )

        running_store = StateStore(tmp_path, running_id)
        running_store.save_meta(
            {
                "research_id": running_id,
                "status": DeepResearchStatus.RUNNING.value,
                "topic": "Running topic",
                "user_id": 1,
                "submitted_at": "2026-02-21T11:00:00",
                "request": {
                    "topic": "Running topic",
                    "mode": "queue",
                    "session_id": session_id,
                },
            }
        )
        StateStore.register_session_run(
            tmp_path,
            session_id=session_id,
            research_id=running_id,
            user_id=1,
            topic="Running topic",
            submitted_at="2026-02-21T11:00:00",
        )

        foreign_store = StateStore(tmp_path, foreign_id)
        foreign_store.save_meta(
            {
                "research_id": foreign_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Foreign topic",
                "user_id": 2,
                "submitted_at": "2026-02-21T12:00:00",
                "request": {
                    "topic": "Foreign topic",
                    "mode": "queue",
                    "session_id": session_id,
                },
            }
        )
        StateStore.register_session_run(
            tmp_path,
            session_id=session_id,
            research_id=foreign_id,
            user_id=2,
            topic="Foreign topic",
            submitted_at="2026-02-21T12:00:00",
        )

        other_store = StateStore(tmp_path, other_session_run)
        other_store.save_meta(
            {
                "research_id": other_session_run,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Other session topic",
                "user_id": 1,
                "submitted_at": "2026-02-21T13:00:00",
                "request": {
                    "topic": "Other session topic",
                    "mode": "queue",
                    "session_id": other_session_id,
                },
            }
        )
        StateStore.register_session_run(
            tmp_path,
            session_id=other_session_id,
            research_id=other_session_run,
            user_id=1,
            topic="Other session topic",
            submitted_at="2026-02-21T13:00:00",
        )

        runs_res = await client.get(
            f"/api/deep-research/session/{session_id}/runs",
            params={"limit": 20},
            headers=headers,
        )
        assert runs_res.status_code == 200
        run_ids = {item["research_id"] for item in runs_res.json()["items"]}
        assert completed_id in run_ids
        assert running_id in run_ids
        assert foreign_id not in run_ids
        assert other_session_run not in run_ids

        ctx_res = await client.get(
            f"/api/deep-research/session/{session_id}/context",
            params={"limit": 5, "max_summary_chars": 200},
            headers=headers,
        )
        assert ctx_res.status_code == 200
        ctx_items = ctx_res.json()["items"]
        assert len(ctx_items) == 1
        assert ctx_items[0]["research_id"] == completed_id
        assert ctx_items[0]["citations_total"] == 1
        assert "深度研究报告摘要" in ctx_items[0]["summary"]
    finally:
        await client.aclose()
