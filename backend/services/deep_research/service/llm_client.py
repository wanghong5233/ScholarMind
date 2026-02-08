"""Lightweight LLM client wrapper for report generation."""

from typing import Any, Callable, Dict, Optional

import logging

from openai import AsyncOpenAI

from core.config import settings


def resolve_llm_config(*, model_name_override: Optional[str] = None) -> tuple[Optional[str], str, str]:
    """Resolve OpenAI-compatible LLM config with DashScope fallback."""

    def is_placeholder(value: Optional[str]) -> bool:
        if not value:
            return True
        raw = value.strip()
        if raw in {"sk-...", "sk-xxxx", "sk-XXXXX"}:
            return True
        return raw.endswith("...")

    openai_key = settings.OPENAI_API_KEY
    dash_key = settings.DASHSCOPE_API_KEY
    prefer = (settings.PREFERRED_LLM_PROVIDER or "dashscope").strip().lower()

    has_openai = bool(openai_key) and not is_placeholder(openai_key)
    has_dash = bool(dash_key) and not is_placeholder(dash_key)

    if prefer == "openai" and has_openai:
        return (
            openai_key,
            settings.OPENAI_BASE_URL,
            model_name_override or settings.OPENAI_MODEL_NAME,
        )
    if has_dash:
        return (
            dash_key,
            settings.DASHSCOPE_BASE_URL,
            model_name_override or settings.DASHSCOPE_MODEL_NAME,
        )
    if has_openai:
        return (
            openai_key,
            settings.OPENAI_BASE_URL,
            model_name_override or settings.OPENAI_MODEL_NAME,
        )
    if prefer == "openai":
        return (
            openai_key,
            settings.OPENAI_BASE_URL,
            model_name_override or settings.OPENAI_MODEL_NAME,
        )
    return (
        dash_key,
        settings.DASHSCOPE_BASE_URL,
        model_name_override or settings.DASHSCOPE_MODEL_NAME,
    )


class LLMClient:
    """Call an external LLM for report refinement."""

    def __init__(
        self,
        api_key: Optional[str],
        base_url: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        usage_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        usage_label: Optional[str] = None,
    ) -> None:
        """Initialize the LLM client.

        Args:
            api_key (Optional[str]): API key for the LLM provider.
            base_url (str): API base URL.
            model_name (str): Model identifier.
            temperature (float): Sampling temperature.
            max_tokens (int): Max output tokens.
            timeout (int): Request timeout in seconds.
        """

        self._api_key = api_key
        self._base_url = base_url
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._logger = logging.getLogger("deep_research.llm_client")
        self._client = None
        self._usage_callback = usage_callback
        self._usage_label = usage_label or "llm"
        self._last_usage: Optional[Dict[str, Any]] = None

    def get_last_usage(self) -> Optional[Dict[str, Any]]:
        """Return the latest usage payload from the LLM call."""

        return self._last_usage

    def is_configured(self) -> bool:
        """Check whether the client has the required credentials."""

        return bool(self._api_key)

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Optional[str]:
        """Generate a completion for the given prompt.

        Args:
            prompt (str): Prompt text.

        Returns:
            Optional[str]: Model output or None on failure.
        """

        if not self.is_configured():
            self._logger.warning("LLM client not configured; missing API key.")
            return None

        if self._client is None:
            self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)

        try:
            response = await self._client.chat.completions.create(
                model=self._model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self._temperature if temperature is None else temperature,
                max_tokens=self._max_tokens if max_tokens is None else max_tokens,
                timeout=self._timeout,
            )
            self._record_usage(response)
            content = response.choices[0].message.content if response.choices else None
            return content
        except Exception as exc:  # noqa: BLE001 - surface error for logging
            self._logger.warning("LLM generation failed: %s", exc)
            return None

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        prompt_tokens = self._read_usage_value(usage, "prompt_tokens")
        completion_tokens = self._read_usage_value(usage, "completion_tokens")
        total_tokens = self._read_usage_value(usage, "total_tokens")
        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            return
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        total_tokens = int(total_tokens or 0)
        payload = {
            "label": self._usage_label,
            "model": getattr(response, "model", None) or self._model_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        self._last_usage = payload
        if self._usage_callback:
            self._usage_callback(payload)

    @staticmethod
    def _read_usage_value(usage: Any, key: str) -> Optional[int]:
        if hasattr(usage, key):
            return getattr(usage, key)
        if isinstance(usage, dict):
            return usage.get(key)
        return None
