"""Report export utilities for DeepResearch."""

from __future__ import annotations

import html
from io import BytesIO
from typing import Optional


def render_html(markdown_text: str, title: Optional[str] = None) -> str:
    """Render markdown to HTML with a minimal template."""

    body = _markdown_to_html(markdown_text)
    safe_title = html.escape(title or "DeepResearch Report")
    return (
        "<!doctype html>"
        "<html><head>"
        '<meta charset="utf-8" />'
        f"<title>{safe_title}</title>"
        "<style>"
        "body{font-family:Arial,Helvetica,sans-serif;line-height:1.6;margin:32px;}"
        "pre,code{background:#f5f5f5;padding:2px 4px;border-radius:3px;}"
        "pre{padding:12px;overflow:auto;}"
        "table{border-collapse:collapse;width:100%;}"
        "th,td{border:1px solid #ddd;padding:6px;}"
        "</style>"
        "</head><body>"
        f"<h1>{safe_title}</h1>"
        f"{body}"
        "</body></html>"
    )


def render_pdf(markdown_text: str, title: Optional[str] = None) -> bytes:
    """Render markdown into a simple PDF document."""

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("reportlab not installed") from exc

    buffer = BytesIO()
    canvas_obj = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    text_object = canvas_obj.beginText(40, height - 50)
    text_object.setLeading(14)
    text_object.textLine(title or "DeepResearch Report")
    text_object.textLine("")
    for line in markdown_text.splitlines():
        text_object.textLine(line)
        if text_object.getY() <= 50:
            canvas_obj.drawText(text_object)
            canvas_obj.showPage()
            text_object = canvas_obj.beginText(40, height - 50)
            text_object.setLeading(14)
    canvas_obj.drawText(text_object)
    canvas_obj.showPage()
    canvas_obj.save()
    buffer.seek(0)
    return buffer.read()


def _markdown_to_html(markdown_text: str) -> str:
    try:
        import markdown

        return markdown.markdown(
            markdown_text,
            extensions=["fenced_code", "tables"],
        )
    except Exception:
        return f"<pre>{html.escape(markdown_text)}</pre>"
