"""Tests for IdeaGenerationPipeline prompt bundles."""

from utils.prompt_loader import load_prompt_bundle


def test_ideagen_prompt_bundle_keys() -> None:
    """Ensure prompt bundles contain required keys."""

    zh_prompts = load_prompt_bundle("ideagen", "zh")
    en_prompts = load_prompt_bundle("ideagen", "en")
    assert "extract_knowledge_system" in zh_prompts
    assert "explore_ideas_system" in zh_prompts
    assert "extract_knowledge_system" in en_prompts
    assert "explore_ideas_system" in en_prompts
