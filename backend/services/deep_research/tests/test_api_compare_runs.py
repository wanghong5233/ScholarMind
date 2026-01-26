"""API tests for DeepResearch run comparison."""

import pytest
from httpx import ASGITransport, AsyncClient

from config import settings
from schemas.common import DeepResearchStatus
from main import app
from service.state_store import StateStore


@pytest.mark.asyncio
async def test_compare_runs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = ASGITransport(app=app, lifespan="on")
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = {"X-User-Id": "1"}
    try:
        left_id = "dr_left"
        right_id = "dr_right"
        left_store = StateStore(tmp_path, left_id)
        right_store = StateStore(tmp_path, right_id)
        left_store.save_meta(
            {
                "research_id": left_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Left",
                "user_id": 1,
                "duration_seconds": 10.0,
            }
        )
        right_store.save_meta(
            {
                "research_id": right_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Right",
                "user_id": 1,
                "duration_seconds": 15.0,
            }
        )
        left_store.save_json(
            "report.json",
            {
                "summary": {
                    "blocks_total": 3,
                    "blocks_by_status": {"completed": 2, "failed": 1},
                    "citations_total": 5,
                    "tool_traces_total": 7,
                    "tool_traces_by_type": {"rag.ask": 4, "web.search": 3},
                    "decisions_total": 1,
                    "errors": [],
                }
            },
        )
        right_store.save_json(
            "report.json",
            {
                "summary": {
                    "blocks_total": 4,
                    "blocks_by_status": {"completed": 3, "failed": 1},
                    "citations_total": 2,
                    "tool_traces_total": 8,
                    "tool_traces_by_type": {"rag.ask": 5, "web.search": 3},
                    "decisions_total": 2,
                    "errors": ["e1"],
                }
            },
        )

        response = await client.post(
            "/api/deep-research/compare",
            json={"left_id": left_id, "right_id": right_id},
            headers=headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["left"]["research_id"] == left_id
        assert payload["right"]["research_id"] == right_id
        assert payload["diff"]["duration_seconds"]["delta"] == 5.0
        assert payload["diff"]["citations_total"]["left"] == 5.0
        assert payload["diff"]["citations_total"]["right"] == 2.0
        assert payload["diff"]["errors_total"]["right"] == 1.0
        assert payload["diff"]["blocks_by_status"]["completed"]["delta"] == 1.0
        assert payload["diff"]["tool_traces_by_type"]["rag.ask"]["delta"] == 1.0
    finally:
        await client.aclose()
