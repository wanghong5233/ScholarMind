"""Tests for DeepResearch LLM override policy."""

from core.config import settings
from schemas.common import DeepResearchRequest
from service.pipeline import ResearchPipeline


def test_request_llm_override_disabled(monkeypatch) -> None:
    """When disabled, request llm overrides must be ignored."""

    monkeypatch.setattr(settings, "DEEP_RESEARCH_ALLOW_REQUEST_LLM_OVERRIDE", False, raising=False)
    request = DeepResearchRequest(
        topic="topic",
        llm_provider="openai",
        llm_model="gpt-4o",
    )
    provider, model_name = ResearchPipeline._effective_request_llm_overrides(request)
    assert provider is None
    assert model_name is None


def test_request_llm_override_enabled(monkeypatch) -> None:
    """When enabled, request llm overrides should pass through."""

    monkeypatch.setattr(settings, "DEEP_RESEARCH_ALLOW_REQUEST_LLM_OVERRIDE", True, raising=False)
    request = DeepResearchRequest(
        topic="topic",
        llm_provider="openai",
        llm_model="gpt-4o",
    )
    provider, model_name = ResearchPipeline._effective_request_llm_overrides(request)
    assert provider == "openai"
    assert model_name == "gpt-4o"
