import asyncio
from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

PYDANTIC_SETTINGS_AVAILABLE = importlib.util.find_spec("pydantic_settings") is not None


def _load_symbols():
    if not PYDANTIC_SETTINGS_AVAILABLE:
        pytest.skip("pydantic_settings is not installed in this test environment")
    from core.config import settings
    from service.tools.file_ops_tools import (
        CreateDirectoryTool,
        CreateFileTool,
        DeletePathTool,
        ListWorkspaceTreeTool,
        RenameMovePathTool,
    )

    return (
        settings,
        CreateDirectoryTool,
        CreateFileTool,
        ListWorkspaceTreeTool,
        RenameMovePathTool,
        DeletePathTool,
    )


def test_list_workspace_tree_tool_filters_hidden(tmp_path, monkeypatch):
    settings, _, _, list_workspace_tree_tool, _, _ = _load_symbols()

    workspace_root = tmp_path / "workspaces"
    workspace_dir = workspace_root / "1" / "ws"
    (workspace_dir / "docs").mkdir(parents=True)
    (workspace_dir / "docs" / "report.md").write_text("# report", encoding="utf-8")
    (workspace_dir / ".cache").mkdir(parents=True)
    (workspace_dir / ".cache" / "ignored.txt").write_text("x", encoding="utf-8")

    monkeypatch.setattr(settings, "WORKSPACES_ROOT", str(workspace_root))
    state = SimpleNamespace(workspace_id="ws", user_id=1, modified_files=set())

    tool = list_workspace_tree_tool()
    result = asyncio.run(
        tool.execute(
            state,
            {
                "target_path": ".",
                "max_depth": 3,
                "max_entries": 200,
                "include_hidden": False,
            },
        )
    )

    assert result.success
    entries = result.data.get("entries") or []
    paths = [str(item.get("path") or "") for item in entries]
    assert "docs" in paths
    assert "docs/report.md" in paths
    assert ".cache" not in paths
    assert all(not path.startswith(".cache/") for path in paths)


def test_create_directory_and_file_tools(tmp_path, monkeypatch):
    settings, create_directory_tool, create_file_tool, _, _, _ = _load_symbols()

    workspace_root = tmp_path / "workspaces"
    workspace_dir = workspace_root / "1" / "ws"
    workspace_dir.mkdir(parents=True)

    monkeypatch.setattr(settings, "WORKSPACES_ROOT", str(workspace_root))
    state = SimpleNamespace(workspace_id="ws", user_id=1, modified_files=set())

    dir_tool = create_directory_tool()
    dir_result = asyncio.run(
        dir_tool.execute(
            state,
            {"directory_path": "notes/daily", "exist_ok": True},
        )
    )
    assert dir_result.success
    assert (workspace_dir / "notes" / "daily").is_dir()

    file_tool = create_file_tool()
    create_result = asyncio.run(
        file_tool.execute(
            state,
            {
                "file_path": "notes/daily/summary.md",
                "content": "# Day Summary\n- item",
            },
        )
    )
    assert create_result.success
    assert "notes/daily/summary.md" in state.modified_files
    assert (workspace_dir / "notes" / "daily" / "summary.md").exists()

    duplicate_result = asyncio.run(
        file_tool.execute(
            state,
            {
                "file_path": "notes/daily/summary.md",
                "content": "new content",
                "overwrite": False,
            },
        )
    )
    assert not duplicate_result.success

    overwrite_result = asyncio.run(
        file_tool.execute(
            state,
            {
                "file_path": "notes/daily/summary.md",
                "content": "updated content",
                "overwrite": True,
            },
        )
    )
    assert overwrite_result.success
    assert (workspace_dir / "notes" / "daily" / "summary.md").read_text(encoding="utf-8") == "updated content"


def test_rename_move_and_delete_tools(tmp_path, monkeypatch):
    (
        settings,
        _create_directory_tool,
        create_file_tool,
        _list_workspace_tree_tool,
        rename_move_path_tool,
        delete_path_tool,
    ) = _load_symbols()

    workspace_root = tmp_path / "workspaces"
    workspace_dir = workspace_root / "1" / "ws"
    workspace_dir.mkdir(parents=True)

    monkeypatch.setattr(settings, "WORKSPACES_ROOT", str(workspace_root))
    state = SimpleNamespace(workspace_id="ws", user_id=1, modified_files=set())

    file_tool = create_file_tool()
    create_result = asyncio.run(
        file_tool.execute(
            state,
            {
                "file_path": "docs/data.json",
                "content": "not json payload",
            },
        )
    )
    assert create_result.success
    assert create_result.data.get("validation_passed") is False
    assert create_result.data.get("validation_warnings")

    move_tool = rename_move_path_tool()
    move_result = asyncio.run(
        move_tool.execute(
            state,
            {
                "source_path": "docs/data.json",
                "target_path": "archive/data.json",
                "create_parent_dirs": True,
            },
        )
    )
    assert move_result.success
    assert (workspace_dir / "archive" / "data.json").exists()
    assert not (workspace_dir / "docs" / "data.json").exists()

    delete_tool = delete_path_tool()
    interaction_result = asyncio.run(
        delete_tool.execute(
            state,
            {
                "target_path": "archive/data.json",
            },
        )
    )
    assert interaction_result.success
    assert interaction_result.data.get("interaction_required") is True
    approval_token = str(interaction_result.data.get("approval_token") or "")
    assert approval_token
    assert (workspace_dir / "archive" / "data.json").exists()

    delete_result = asyncio.run(
        delete_tool.execute(
            state,
            {
                "target_path": "archive/data.json",
                "_approval_token": approval_token,
            },
        )
    )
    assert delete_result.success
    assert not (workspace_dir / "archive" / "data.json").exists()


def test_delete_tool_requires_recursive_and_confirmation(tmp_path, monkeypatch):
    (
        settings,
        _create_directory_tool,
        _create_file_tool,
        _list_workspace_tree_tool,
        _rename_move_path_tool,
        delete_path_tool,
    ) = _load_symbols()

    workspace_root = tmp_path / "workspaces"
    workspace_dir = workspace_root / "1" / "ws"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "logs").mkdir(parents=True)
    (workspace_dir / "logs" / "run.log").write_text("hello", encoding="utf-8")

    monkeypatch.setattr(settings, "WORKSPACES_ROOT", str(workspace_root))
    state = SimpleNamespace(workspace_id="ws", user_id=1, modified_files=set())

    # 非空目录且 recursive=false：无法执行删除，不应返回审批令牌。
    delete_tool = delete_path_tool()
    blocked_interaction = asyncio.run(
        delete_tool.execute(
            state,
            {
                "target_path": "logs",
                "recursive": False,
            },
        )
    )
    assert not blocked_interaction.success
    assert blocked_interaction.data.get("can_execute") is False

    # recursive=true：交互准备阶段会生成审批令牌，确认后才能删除。
    executable_interaction = asyncio.run(
        delete_tool.execute(
            state,
            {
                "target_path": "logs",
                "recursive": True,
            },
        )
    )
    assert executable_interaction.success
    approval_token = str(executable_interaction.data.get("approval_token") or "")
    assert approval_token

    missing_token_result = asyncio.run(
        delete_tool.execute(
            state,
            {
                "target_path": "logs",
                "recursive": True,
            },
        )
    )
    assert missing_token_result.success
    assert missing_token_result.data.get("interaction_required") is True

    delete_result = asyncio.run(
        delete_tool.execute(
            state,
            {
                "target_path": "logs",
                "recursive": True,
                "_approval_token": approval_token,
            },
        )
    )
    assert delete_result.success
    assert not (workspace_dir / "logs").exists()
