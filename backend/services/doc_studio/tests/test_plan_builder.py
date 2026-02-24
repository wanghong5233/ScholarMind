from pathlib import Path
import importlib.util
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

PYDANTIC_SETTINGS_AVAILABLE = importlib.util.find_spec("pydantic_settings") is not None


def _load_symbols():
    if not PYDANTIC_SETTINGS_AVAILABLE:
        pytest.skip("pydantic_settings is not installed in this test environment")
    from service.intent_classifier import IntentType
    from service.plan_builder import build_plan

    return IntentType, build_plan


def test_build_plan_edit_with_selection_prefers_rewrite():
    intent_type, build_plan = _load_symbols()
    plan = build_plan(
        intent_type.EDIT,
        context_info={"has_selection": True, "selection_length": 120},
    )
    assert "rewrite_selection_tool" in plan.steps
    assert "insert_text_tool" not in plan.steps


def test_build_plan_edit_without_selection_uses_insert():
    intent_type, build_plan = _load_symbols()
    plan = build_plan(
        intent_type.EDIT,
        context_info={"has_selection": False, "selection_length": 0},
    )
    assert "insert_text_tool" in plan.steps


def test_build_plan_edit_with_file_mentions_prefers_locate_and_range_rewrite():
    intent_type, build_plan = _load_symbols()
    plan = build_plan(
        intent_type.EDIT,
        context_info={
            "has_selection": False,
            "has_file_mentions": True,
            "selection_length": 0,
        },
    )
    assert "semantic_code_search_tool" in plan.steps
    assert "search_codebase_tool" in plan.steps
    assert "read_file_range_tool" in plan.steps
    assert "rewrite_line_range_tool" in plan.steps
    assert "insert_text_tool" not in plan.steps


def test_build_plan_combined_conditions_include_analyze_once():
    intent_type, build_plan = _load_symbols()
    plan = build_plan(
        intent_type.EDIT,
        context_info={
            "has_selection": True,
            "has_file_mentions": True,
            "selection_length": 400,
        },
    )
    assert plan.steps.count("analyze_document_tool") == 1


def test_build_plan_file_op_with_directory_only():
    intent_type, build_plan = _load_symbols()
    plan = build_plan(
        intent_type.FILE_OP,
        context_info={
            "wants_directory_create": True,
            "wants_file_create": False,
        },
    )
    assert "list_workspace_tree_tool" in plan.steps
    assert "create_directory_tool" in plan.steps
    assert "create_file_tool" not in plan.steps
    assert plan.steps[-1] == "reply_to_user_tool"


def test_build_plan_file_op_with_file_creation():
    intent_type, build_plan = _load_symbols()
    plan = build_plan(
        intent_type.FILE_OP,
        context_info={
            "wants_directory_create": False,
            "wants_file_create": True,
        },
    )
    assert "list_workspace_tree_tool" in plan.steps
    assert "create_file_tool" in plan.steps
    assert plan.steps[-1] == "reply_to_user_tool"


def test_build_plan_file_op_with_move_and_delete():
    intent_type, build_plan = _load_symbols()
    plan = build_plan(
        intent_type.FILE_OP,
        context_info={
            "wants_directory_create": False,
            "wants_file_create": False,
            "wants_move_rename": True,
            "wants_delete_path": True,
        },
    )
    assert "list_workspace_tree_tool" in plan.steps
    assert "rename_move_path_tool" in plan.steps
    assert "delete_path_tool" in plan.steps
    assert plan.steps[-1] == "reply_to_user_tool"


def test_build_plan_file_op_without_create_flags():
    intent_type, build_plan = _load_symbols()
    plan = build_plan(
        intent_type.FILE_OP,
        context_info={
            "wants_directory_create": False,
            "wants_file_create": False,
            "wants_move_rename": False,
            "wants_delete_path": False,
        },
    )
    assert "list_workspace_tree_tool" in plan.steps
    assert "create_directory_tool" not in plan.steps
    assert "create_file_tool" not in plan.steps
    assert plan.steps[-1] == "reply_to_user_tool"

