"""Tests for IdeaGenerationPipeline prompt building."""

from service.idea_generation_pipeline import IdeaGenerationPipeline
from schemas.idea_generation import IdeaGenerationRequest


def test_idea_prompt_language_detection() -> None:
    """Ensure prompt switches language based on topic."""

    pipeline = IdeaGenerationPipeline("http://example", "/tmp", 30)
    request = IdeaGenerationRequest(topic="Transformer", idea_count=3)
    prompt = pipeline._build_prompt(request)
    assert "Generate 3 research ideas" in prompt

    zh_request = IdeaGenerationRequest(topic="注意力机制", idea_count=3)
    zh_prompt = pipeline._build_prompt(zh_request)
    assert "生成 3 个研究想法" in zh_prompt
