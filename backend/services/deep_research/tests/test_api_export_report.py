"""API tests for report export."""

import pytest
from httpx import ASGITransport, AsyncClient

from core.config import settings
from main import app
from schemas.common import DeepResearchStatus
from service.state_store import StateStore


@pytest.mark.asyncio
async def test_export_markdown_and_html(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = ASGITransport(app=app, lifespan="on")
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = {"X-User-Id": "1"}
    try:
        research_id = "dr_export"
        store = StateStore(tmp_path, research_id)
        store.save_meta(
            {
                "research_id": research_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Export",
                "user_id": 1,
            }
        )
        store.save_json("report.json", {"report_markdown": "# Title\n\nBody"})

        md = await client.get(
            f"/api/deep-research/{research_id}/export",
            params={"format": "markdown"},
            headers=headers,
        )
        assert md.status_code == 200
        assert md.headers["content-type"].startswith("text/markdown")
        assert "Title" in md.text

        html = await client.get(
            f"/api/deep-research/{research_id}/export",
            params={"format": "html"},
            headers=headers,
        )
        assert html.status_code == 200
        assert html.headers["content-type"].startswith("text/html")
        assert "<html" in html.text.lower()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_export_pdf(tmp_path, monkeypatch) -> None:
    pytest.importorskip("reportlab")
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    transport = ASGITransport(app=app, lifespan="on")
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = {"X-User-Id": "1"}
    try:
        research_id = "dr_export_pdf"
        store = StateStore(tmp_path, research_id)
        store.save_meta(
            {
                "research_id": research_id,
                "status": DeepResearchStatus.COMPLETED.value,
                "topic": "Export",
                "user_id": 1,
            }
        )
        store.save_json("report.json", {"report_markdown": "PDF content"})

        pdf = await client.get(
            f"/api/deep-research/{research_id}/export",
            params={"format": "pdf"},
            headers=headers,
        )
        assert pdf.status_code == 200
        assert pdf.headers["content-type"].startswith("application/pdf")
        assert len(pdf.content) > 10
    finally:
        await client.aclose()
