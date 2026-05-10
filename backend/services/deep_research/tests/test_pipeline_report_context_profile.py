"""Tests for report context profile resolution."""

import inspect

from core.config import settings
from service.pipeline import ResearchPipeline


def test_report_context_profile_keeps_standard_limits_without_quick_preset():
    """Non-quick presets should keep incoming limits unchanged."""

    resolved = ResearchPipeline._resolve_report_context_profile(
        metadata={"deep_research_preset": "deep"},
        section_input_budget=11000,
        section_max_blocks=8,
        section_max_notes_per_block=5,
        section_max_notes_total=36,
        section_max_citations=64,
    )
    assert resolved["profile"] == "standard"
    assert resolved["section_input_budget"] == 11000
    assert resolved["section_max_blocks"] == 8
    assert resolved["section_max_notes_per_block"] == 5
    assert resolved["section_max_notes_total"] == 36
    assert resolved["section_max_citations"] == 64


def test_report_context_profile_applies_quick_caps(monkeypatch):
    """Quick preset should cap section context payload deterministically."""

    monkeypatch.setattr(settings, "REPORT_SECTION_QUICK_PROMPT_MAX_INPUT_TOKENS", 9000, raising=False)
    monkeypatch.setattr(settings, "REPORT_SECTION_QUICK_MAX_BLOCKS", 7, raising=False)
    monkeypatch.setattr(settings, "REPORT_SECTION_QUICK_MAX_NOTES_PER_BLOCK", 4, raising=False)
    monkeypatch.setattr(settings, "REPORT_SECTION_QUICK_MAX_NOTES_TOTAL", 28, raising=False)
    monkeypatch.setattr(settings, "REPORT_SECTION_QUICK_MAX_CITATIONS", 48, raising=False)

    resolved = ResearchPipeline._resolve_report_context_profile(
        metadata={"deep_research_preset": "quick"},
        section_input_budget=12000,
        section_max_blocks=8,
        section_max_notes_per_block=5,
        section_max_notes_total=36,
        section_max_citations=64,
    )
    assert resolved["profile"] == "quick"
    assert resolved["section_input_budget"] == 9000
    assert resolved["section_max_blocks"] == 7
    assert resolved["section_max_notes_per_block"] == 4
    assert resolved["section_max_notes_total"] == 28
    assert resolved["section_max_citations"] == 48


def test_refine_report_sectional_accepts_request_metadata():
    """Sectional refiner should keep request_metadata in public signature."""

    params = inspect.signature(ResearchPipeline._refine_report_sectional).parameters
    assert "request_metadata" in params
