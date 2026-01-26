"""API tests for evidence export."""

import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from httpx import ASGITransport, AsyncClient

from config import settings
from schemas.common import DeepResearchStatus
from main import app
from service.state_store import StateStore


@pytest.mark.asyncio
async def test_export_block_evidence_zip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = ASGITransport(app=app, lifespan="on")
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = {"X-User-Id": "1"}
    try:
        research_id = "dr_evidence"
        block_id = "B001"
        store = StateStore(tmp_path, research_id)
        store.save_meta(
            {
                "research_id": research_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Evidence",
                "user_id": 1,
            }
        )
        store.save_json(
            "queue.json",
            {
                "blocks": [
                    {
                        "block_id": block_id,
                        "title": "Block",
                        "notes": ["note"],
                        "citations": ["C1"],
                        "tool_traces": [],
                        "decisions": [],
                    }
                ]
            },
        )
        store.save_json(
            "citations.json",
            {
                "citations": [
                    {"citation_id": "C1", "title": "Cite", "url": "http://example.com"}
                ]
            },
        )
        store.append_progress(
            {
                "research_id": research_id,
                "stage": "researching",
                "message": "block",
                "timestamp": "2024-01-01T00:00:00Z",
                "payload": {"block_id": block_id},
            }
        )

        response = await client.get(
            f"/api/deep-research/{research_id}/evidence/{block_id}/export",
            headers=headers,
        )
        assert response.status_code == 200
        archive = ZipFile(BytesIO(response.content))
        assert "evidence.json" in archive.namelist()
        assert "manifest.json" in archive.namelist()
        evidence = json.loads(archive.read("evidence.json").decode("utf-8"))
        assert evidence["block_id"] == block_id
        assert evidence["citations"] == ["C1"]
        assert evidence["progress_events"]
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        assert manifest["block_id"] == block_id
        assert manifest["counts"]["citations"] == 1
        assert manifest["highlights"]
    finally:
        await client.aclose()
