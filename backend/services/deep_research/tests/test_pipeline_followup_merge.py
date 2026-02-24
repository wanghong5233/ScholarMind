"""Tests for pipeline follow-up merge guardrails."""

from service.pipeline import ResearchPipeline


def test_merge_followup_questions_filters_clarification_prompts() -> None:
    """User-clarification questions should be filtered from deferred plan."""

    merged = ResearchPipeline._merge_followup_questions_for_plan(
        [
            "你希望聚焦哪个数据集？",
            "What benchmark datasets are best for edge scheduling?",
            "What benchmark datasets are best for edge scheduling?",
        ],
        language="en",
        max_items=4,
    )

    assert len(merged) == 1
    assert "benchmark" in merged[0].lower()


def test_merge_followup_questions_returns_fallback_when_all_filtered() -> None:
    """Fallback plan text should be produced when all follow-ups are clarification-style."""

    merged = ResearchPipeline._merge_followup_questions_for_plan(
        [
            "你希望聚焦哪个数据集？",
            "Can you provide your preference for evaluation metrics?",
        ],
        language="zh",
        max_items=3,
    )

    assert len(merged) == 1
    assert "补充未覆盖子问题" in merged[0]
