from service.intent_classifier import IntentType
from service.plan_builder import build_plan


def test_build_plan_edit_with_selection_prefers_rewrite():
    plan = build_plan(
        IntentType.EDIT,
        context_info={"has_selection": True, "selection_length": 120},
    )
    assert "rewrite_selection_tool" in plan.steps
    assert "insert_text_tool" not in plan.steps


def test_build_plan_edit_without_selection_uses_insert():
    plan = build_plan(
        IntentType.EDIT,
        context_info={"has_selection": False, "selection_length": 0},
    )
    assert "insert_text_tool" in plan.steps

