"""Helpers for normalizing DeepResearch requests."""

from __future__ import annotations

from typing import Dict

from schemas.common import DeepResearchRequest

_PRESET_PARAMS: Dict[str, Dict[str, int]] = {
    "quick": {"depth": 1, "breadth": 2, "max_parallel": 1, "max_iterations": 2},
    "medium": {"depth": 2, "breadth": 5, "max_parallel": 1, "max_iterations": 4},
    "deep": {"depth": 2, "breadth": 8, "max_parallel": 1, "max_iterations": 7},
}

# Keep in sync with DeepResearchRequest defaults.
_DEFAULT_PARAMS = {"depth": 2, "breadth": 5, "max_parallel": 1, "max_iterations": 4}


def apply_deep_research_preset(payload: DeepResearchRequest) -> DeepResearchRequest:
    """Apply preset params when metadata requests it.

    This only overrides defaults to avoid clobbering explicit custom values.
    """
    metadata = payload.metadata or {}
    preset_key = str(metadata.get("deep_research_preset") or "").strip().lower()
    if not preset_key:
        return payload
    preset = _PRESET_PARAMS.get(preset_key)
    if not preset:
        return payload
    force = bool(metadata.get("deep_research_preset_force"))
    if not force and not _is_default_params(payload):
        return payload
    return payload.model_copy(update=preset)


def _is_default_params(payload: DeepResearchRequest) -> bool:
    return (
        payload.depth == _DEFAULT_PARAMS["depth"]
        and payload.breadth == _DEFAULT_PARAMS["breadth"]
        and payload.max_parallel == _DEFAULT_PARAMS["max_parallel"]
        and payload.max_iterations == _DEFAULT_PARAMS["max_iterations"]
    )
