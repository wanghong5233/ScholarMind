"""API tests for archive export."""

import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from httpx import AsyncClient

from core.config import settings
from schemas.common import DeepResearchStatus
from main import app
from service.state_store import StateStore
from tests.httpx_compat import create_asgi_transport


@pytest.mark.asyncio
async def test_export_archive_zip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = create_asgi_transport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = {"X-User-Id": "1"}
    try:
        research_id = "dr_archive"
        store = StateStore(tmp_path, research_id)
        store.save_meta(
            {
                "research_id": research_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Archive",
                "user_id": 1,
            }
        )
        store.save_json("report.json", {"report_markdown": "Report"})
        store.save_json("queue.json", {"blocks": []})
        store.save_json("citations.json", {"citations": []})

        response = await client.get(
            f"/api/deep-research/{research_id}/archive/export",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/zip")

        archive = ZipFile(BytesIO(response.content))
        names = set(archive.namelist())
        assert "meta.json" in names
        assert "report.json" in names
        report = json.loads(archive.read("report.json").decode("utf-8"))
        assert report["report_markdown"] == "Report"
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        assert manifest["research_id"] == research_id
        meta_item = next(item for item in manifest["files"] if item["name"] == "meta.json")
        assert meta_item["sha256"]
    finally:
        await client.aclose()
