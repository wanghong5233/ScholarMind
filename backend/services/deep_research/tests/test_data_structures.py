"""Tests for DynamicTopicQueue and TopicBlock."""

from service.data_structures import DynamicTopicQueue, TopicBlock, TopicStatus


def test_queue_add_and_status() -> None:
    """Ensure queue operations update status correctly."""

    queue = DynamicTopicQueue("research_test")
    block = queue.add_block(title="Topic A", question="What is Topic A?")

    assert block.block_id == "B001"
    assert block.status == TopicStatus.PENDING
    assert queue.get_next_pending_block() == block

    queue.mark_block_status(block.block_id, TopicStatus.RESEARCHING)
    assert queue.get_next_pending_block() is None


def test_queue_iterations() -> None:
    """Ensure iteration tracking enforces max_iterations."""

    queue = DynamicTopicQueue("research_test")
    block = queue.add_block(title="Topic B", question="Explain Topic B", max_iterations=2)

    assert queue.increment_iteration(block.block_id) == 1
    assert queue.increment_iteration(block.block_id) == 2
    assert queue.get_block(block.block_id).status == TopicStatus.FAILED


def test_block_decision_roundtrip() -> None:
    """Ensure decision metadata is persisted."""

    block = TopicBlock(block_id="B001", title="Topic", question="Q", depth=1)
    block.add_decision({"sufficient": True, "rationale": "ok"})
    restored = TopicBlock.from_dict(block.to_dict())
    assert restored.decisions == [{"sufficient": True, "rationale": "ok"}]
