"""API tests for run comparison export."""

import pytest
from httpx import AsyncClient

from core.config import settings
from schemas.common import DeepResearchStatus
from main import app
from service.state_store import StateStore
from tests.httpx_compat import create_asgi_transport


@pytest.mark.asyncio
async def test_compare_export_markdown_html(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = create_asgi_transport(app=app)
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
            }
        )
        right_store.save_meta(
            {
                "research_id": right_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Right",
                "user_id": 1,
            }
        )
        left_store.save_json("report.json", {"summary": {"blocks_total": 1, "errors": []}})
        right_store.save_json("report.json", {"summary": {"blocks_total": 2, "errors": []}})

        markdown = await client.get(
            "/api/deep-research/compare/export",
            params={"left_id": left_id, "right_id": right_id, "format": "markdown"},
            headers=headers,
        )
        assert markdown.status_code == 200
        assert markdown.headers["content-type"].startswith("text/markdown")
        assert left_id in markdown.text
        assert right_id in markdown.text

        html = await client.get(
            "/api/deep-research/compare/export",
            params={"left_id": left_id, "right_id": right_id, "format": "html"},
            headers=headers,
        )
        assert html.status_code == 200
        assert html.headers["content-type"].startswith("text/html")
        assert "<html" in html.text.lower()
        assert "Top Tools" in html.text

        json_resp = await client.get(
            "/api/deep-research/compare/export",
            params={"left_id": left_id, "right_id": right_id, "format": "json"},
            headers=headers,
        )
        assert json_resp.status_code == 200
        assert json_resp.headers["content-type"].startswith("application/json")
        payload = json_resp.json()
        assert payload["left"]["research_id"] == left_id
        assert "blocks_by_status_top" in payload["diff"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_compare_export_pdf(tmp_path, monkeypatch) -> None:
    pytest.importorskip("reportlab")
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = create_asgi_transport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = {"X-User-Id": "1"}
    try:
        left_id = "dr_left_pdf"
        right_id = "dr_right_pdf"
        left_store = StateStore(tmp_path, left_id)
        right_store = StateStore(tmp_path, right_id)
        left_store.save_meta(
            {
                "research_id": left_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Left",
                "user_id": 1,
            }
        )
        right_store.save_meta(
            {
                "research_id": right_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Right",
                "user_id": 1,
            }
        )
        left_store.save_json("report.json", {"summary": {"blocks_total": 1, "errors": []}})
        right_store.save_json("report.json", {"summary": {"blocks_total": 2, "errors": []}})

        pdf = await client.get(
            "/api/deep-research/compare/export",
            params={"left_id": left_id, "right_id": right_id, "format": "pdf"},
            headers=headers,
        )
        assert pdf.status_code == 200
        assert pdf.headers["content-type"].startswith("application/pdf")
        assert len(pdf.content) > 10
    finally:
        await client.aclose()
