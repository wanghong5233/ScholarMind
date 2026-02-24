"""Tests for PlannerAgent."""

import pytest

from agents.planner_agent import PlannerAgent


def test_template_plan_disabled() -> None:
    """Template planner must be disabled in DeepResearch runtime."""

    planner = PlannerAgent(depth=1, breadth=3, language="en")
    with pytest.raises(RuntimeError, match="Template planner is disabled"):
        planner.plan("Transformer")
