"""Tests for ManagerAgent follow-up expansion."""

from agents.manager_agent import ManagerAgent
from service.data_structures import DynamicTopicQueue


def test_manager_agent_adds_followups() -> None:
    """Short summaries should trigger follow-up creation."""

    queue = DynamicTopicQueue("research_test")
    block = queue.add_block(title="Topic A", question="Topic A", depth=1)
    manager = ManagerAgent(queue, max_depth=2, max_followups=2, min_summary_chars=200)

    followups = manager.maybe_expand(block, summary="Too short.", language="en")
    assert len(followups) > 0
    assert all(item.depth == 2 for item in followups)


def test_manager_agent_skips_long_summary() -> None:
    """Long summaries should not trigger follow-ups."""

    queue = DynamicTopicQueue("research_test")
    block = queue.add_block(title="Topic B", question="Topic B", depth=1)
    manager = ManagerAgent(queue, max_depth=2, max_followups=2, min_summary_chars=10)

    summary = "This summary is long enough and provides sufficient detail."
    followups = manager.maybe_expand(block, summary=summary, language="en")
    assert followups == []


def test_manager_agent_adds_followups_from_questions() -> None:
    """Explicit follow-up questions should be added."""

    queue = DynamicTopicQueue("research_test")
    block = queue.add_block(title="Topic C", question="Topic C", depth=1)
    manager = ManagerAgent(queue, max_depth=2, max_followups=2, min_summary_chars=200)

    followups = manager.add_followups_from_questions(
        block,
        questions=["What is the evidence?", "What are the limitations?"],
        language="en",
    )
    assert len(followups) == 2
