"""Lightweight LLM client wrapper for report generation."""

from typing import Optional

import logging

from openai import AsyncOpenAI


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
            content = response.choices[0].message.content if response.choices else None
            return content
        except Exception as exc:  # noqa: BLE001 - surface error for logging
            self._logger.warning("LLM generation failed: %s", exc)
            return None
