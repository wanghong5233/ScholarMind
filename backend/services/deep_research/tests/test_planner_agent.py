"""Tests for PlannerAgent."""

from agents.planner_agent import PlannerAgent


def test_plan_depth_one() -> None:
    """Depth=1 should not generate child items."""

    planner = PlannerAgent(depth=1, breadth=3, language="en")
    items = planner.plan("Transformer")
    assert len(items) == 3
    assert all(item.depth == 1 for item in items)


def test_plan_depth_two_has_children() -> None:
    """Depth=2 should generate child items with parent titles."""

    planner = PlannerAgent(depth=2, breadth=2, language="en")
    items = planner.plan("Transformer")
    depth_two = [item for item in items if item.depth == 2]
    assert depth_two
    assert all(item.parent_title for item in depth_two)
