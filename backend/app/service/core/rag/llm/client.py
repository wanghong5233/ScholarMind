from __future__ import annotations
from typing import Iterable, Generator, Optional, Dict, Any, List
import time
from core.config import settings
import httpx


class LLMClient:
    """
    Unified LLM client facade:
    - providers: dashscope/openai/local
    - streaming & non-streaming
    - simple retry with backoff
    """

    def __init__(self) -> None:
        # 动态选择：若显式配置为 dashscope/openai 则使用；
        # 若为 local 但存在云端 API Key，则自动切换到对应云端；否则使用本地占位实现。
        prov = settings.SM_LLM_TYPE
        if prov == "local" and settings.DASHSCOPE_API_KEY:
            prov = "dashscope"
        if prov == "local" and settings.OPENAI_API_KEY:
            prov = "openai"
        self.provider = prov
        self.model = (
            settings.OPENAI_MODEL_NAME if self.provider == "openai" else settings.DASHSCOPE_MODEL_NAME
        )
        self.base_url = (
            settings.OPENAI_BASE_URL if self.provider == "openai" else settings.DASHSCOPE_BASE_URL
        )
        self.api_key = (
            settings.OPENAI_API_KEY if self.provider == "openai" else settings.DASHSCOPE_API_KEY
        )

    def generate(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 512,
        stream: bool = True,
        retries: int = 2,
    ) -> Iterable[str] | str:
        if stream:
            return self._generate_stream(messages, temperature, max_tokens, retries)
        return self._generate_once(messages, temperature, max_tokens, retries)

    # --- internals ---
    def _generate_stream(
        self, messages: List[Dict[str, str]], temperature: float, max_tokens: int, retries: int
    ) -> Generator[str, None, None]:
        if self.provider in ("dashscope", "openai"):
            url = f"{self.base_url.rstrip('/')}/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            }
            # 直连 SSE，逐行解析 data: {...}
            with httpx.stream("POST", url, headers=headers, json=payload, timeout=60.0) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="ignore")
                    if line.startswith("data: "):
                        data = line[len("data: "):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = httpx.Response(200, text=data).json()
                        except Exception:
                            # 兜底：直接输出原始片段
                            yield data
                            continue
                        # OpenAI/DashScope 兼容：delta.content 或 choices[0].message.content
                        choices = obj.get("choices") or []
                        if choices:
                            c0 = choices[0]
                            delta = c0.get("delta") or {}
                            content = delta.get("content") or c0.get("message", {}).get("content")
                            if content:
                                yield content
            return
        # 本地占位：仅用于无 Key 的开发场景
        content = self._fake_completion(messages, temperature, max_tokens)
        for part in content.split():
            yield part + " "
        return

    def _generate_once(
        self, messages: List[Dict[str, str]], temperature: float, max_tokens: int, retries: int
    ) -> str:
        if self.provider in ("dashscope", "openai"):
            url = f"{self.base_url.rstrip('/')}/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            r = httpx.post(url, headers=headers, json=payload, timeout=60.0)
            r.raise_for_status()
            obj = r.json()
            choices = obj.get("choices") or []
            if choices:
                c0 = choices[0]
                text = c0.get("message", {}).get("content") or c0.get("text") or ""
                return text
            return ""
        # 占位本地实现
        return self._fake_completion(messages, temperature, max_tokens)

    def _fake_completion(self, messages: List[Dict[str, str]], temperature: float, max_tokens: int) -> str:
        # Deterministic placeholder for now
        # Concatenate last user message with a fixed acknowledgement
        last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
        q = (last_user or {}).get("content", "")
        return f"[model={self.model}] Answer based on context. Q: {q[:128]}"
