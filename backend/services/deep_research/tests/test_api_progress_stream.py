"""API tests for DeepResearch progress SSE stream."""

import asyncio
import json
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from core.config import settings
from main import app
from schemas.common import DeepResearchStatus
from service.state_store import StateStore


async def _read_first_data_line(response) -> str:
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            return line
    return ""


@pytest.mark.asyncio
async def test_progress_stream_last_event_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = ASGITransport(app=app, lifespan="on")
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = {"X-User-Id": "1"}

    try:
        research_id = "dr_stream"
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
                "message": "first",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {},
            }
        )
        first_events, first_offset = store.read_progress_since(0, limit=1)
        assert first_events
        store.append_progress(
            {
                "research_id": research_id,
                "stage": "researching",
                "message": "second",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {},
            }
        )

        async with client.stream(
            "GET",
            f"/api/deep-research/{research_id}/progress/stream",
            headers={**headers, "Last-Event-ID": str(first_offset)},
        ) as response:
            assert response.status_code == 200
            line = await asyncio.wait_for(_read_first_data_line(response), timeout=1.0)
            assert line
            payload = json.loads(line.replace("data: ", "", 1))
            assert payload["message"] == "second"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_progress_stream_last_event_id_query_user(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = ASGITransport(app=app, lifespan="on")
    client = AsyncClient(transport=transport, base_url="http://test")

    try:
        research_id = "dr_stream_query"
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
                "message": "first",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {},
            }
        )
        first_events, first_offset = store.read_progress_since(0, limit=1)
        assert first_events
        store.append_progress(
            {
                "research_id": research_id,
                "stage": "researching",
                "message": "second",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {},
            }
        )

        async with client.stream(
            "GET",
            f"/api/deep-research/{research_id}/progress/stream",
            params={"user_id": "1", "last_event_id": str(first_offset)},
        ) as response:
            assert response.status_code == 200
            line = await asyncio.wait_for(_read_first_data_line(response), timeout=1.0)
            assert line
            payload = json.loads(line.replace("data: ", "", 1))
            assert payload["message"] == "second"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_progress_stream_forbidden(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = ASGITransport(app=app, lifespan="on")
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        research_id = "dr_stream_forbidden"
        store = StateStore(tmp_path, research_id)
        store.save_meta(
            {
                "research_id": research_id,
                "status": DeepResearchStatus.RUNNING.value,
                "user_id": 2,
                "request": {"topic": "Topic", "mode": "queue"},
            }
        )
        async with client.stream(
            "GET",
            f"/api/deep-research/{research_id}/progress/stream",
            headers={"X-User-Id": "1"},
        ) as response:
            assert response.status_code == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_progress_stream_last_event_id_forbidden(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = ASGITransport(app=app, lifespan="on")
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        research_id = "dr_stream_forbidden_last_event"
        store = StateStore(tmp_path, research_id)
        store.save_meta(
            {
                "research_id": research_id,
                "status": DeepResearchStatus.RUNNING.value,
                "user_id": 2,
                "request": {"topic": "Topic", "mode": "queue"},
            }
        )
        store.append_progress(
            {
                "research_id": research_id,
                "stage": "planning",
                "message": "first",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {},
            }
        )
        async with client.stream(
            "GET",
            f"/api/deep-research/{research_id}/progress/stream",
            params={"user_id": "1", "last_event_id": "0"},
        ) as response:
            assert response.status_code == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_progress_stream_not_found(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = ASGITransport(app=app, lifespan="on")
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        async with client.stream(
            "GET",
            "/api/deep-research/dr_missing/progress/stream",
            headers={"X-User-Id": "1"},
        ) as response:
            assert response.status_code == 404
    finally:
        await client.aclose()
