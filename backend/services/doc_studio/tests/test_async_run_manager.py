from pathlib import Path
import sys
import asyncio
import importlib.util

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

PYDANTIC_SETTINGS_AVAILABLE = importlib.util.find_spec("pydantic_settings") is not None


def _load_symbol():
    if not PYDANTIC_SETTINGS_AVAILABLE:
        pytest.skip("pydantic_settings is not installed in this test environment")
    from service.async_run_manager import AsyncRunManager

    return AsyncRunManager


def test_interaction_flow_approve(tmp_path):
    async_run_manager = _load_symbol()
    manager = async_run_manager()
    run_dir = tmp_path / "runs"
    state = manager.create_run(
        run_id="run_1",
        workspace_id="ws",
        user_id=1,
        run_dir=run_dir,
    )
    manager.update_status("run_1", "running")
    payload = manager.begin_interaction(
        "run_1",
        {
            "tool_name": "delete_path_tool",
            "target_path": "docs/a.md",
            "recursive": False,
            "timeout_seconds": 60,
        },
    )
    assert payload is not None
    assert state.status == "awaiting_user_interaction"
    interaction_id = str(payload.get("interaction_id") or "")
    assert interaction_id

    async def _wait_and_resolve():
        manager.resolve_interaction(
            "run_1",
            interaction_id,
            decision="approve",
            note="ok",
        )
        return await manager.wait_for_interaction("run_1", interaction_id, timeout_seconds=60)

    result = asyncio.run(_wait_and_resolve())
    assert result.get("decision") == "approve"
    assert state.status == "running"
    assert state.pending_interaction is None


def test_cancel_run_unblocks_interaction_waiter(tmp_path):
    async_run_manager = _load_symbol()
    manager = async_run_manager()
    run_dir = tmp_path / "runs"
    manager.create_run(
        run_id="run_2",
        workspace_id="ws",
        user_id=2,
        run_dir=run_dir,
    )
    manager.update_status("run_2", "running")
    payload = manager.begin_interaction(
        "run_2",
        {
            "tool_name": "delete_path_tool",
            "target_path": "docs/b.md",
            "recursive": True,
        },
    )
    interaction_id = str((payload or {}).get("interaction_id") or "")
    assert interaction_id

    async def _wait_then_cancel():
        manager.cancel_run("run_2", "cancelled_by_user")
        return await manager.wait_for_interaction("run_2", interaction_id, timeout_seconds=60)

    result = asyncio.run(_wait_then_cancel())
    assert result.get("decision") == "cancelled"
