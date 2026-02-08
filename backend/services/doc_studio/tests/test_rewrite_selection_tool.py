import asyncio
from types import SimpleNamespace

import pytest

from service.tools.editing_tools import RewriteSelectionTool
from core.config import settings


@pytest.mark.asyncio
async def test_rewrite_selection_tool(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    target_dir = workspace_root / "1" / "ws"
    target_dir.mkdir(parents=True)
    target_file = target_dir / "main.tex"
    target_file.write_text("Hello World", encoding="utf-8")

    monkeypatch.setattr(settings, "WORKSPACES_ROOT", str(workspace_root))
    agent_state = SimpleNamespace(workspace_id="ws", user_id=1, modified_files=set())

    tool = RewriteSelectionTool()
    result = await tool.execute(
        agent_state,
        {
            "file_path": "main.tex",
            "start_offset": 0,
            "end_offset": 5,
            "replacement_text": "Hola",
        },
    )

    assert result.success
    assert "main.tex" in agent_state.modified_files
    assert target_file.read_text(encoding="utf-8") == "Hola World"

