from __future__ import annotations

import base64
from typing import Optional
import requests
from core.config import settings
from utils.get_logger import log


class VisionEngine:
    """图像理解引擎（用于图表语义摘要）— HTTP 模式。
    当前支持 qwen2-vl 通过 HTTP 服务调用。
    """

    def __init__(self) -> None:
        self.enabled = bool(getattr(settings, "SM_VISION_ENABLED", True))
        self.endpoint = getattr(settings, "SM_VISION_ENDPOINT", None)
        self.timeout = getattr(settings, "SM_VISION_TIMEOUT_SECS", 60)
        self.max_tokens = getattr(settings, "SM_VISION_MAX_TOKENS", 128)

    def is_available(self) -> bool:
        return self.enabled and bool(self.endpoint)

    def summarize_figure(self, image_bytes: bytes, caption: str | None = None) -> Optional[str]:
        if not self.is_available():
            return None
        try:
            files = {"image": ("crop.png", image_bytes, "image/png")}
            data = {"caption": caption or "", "max_tokens": int(self.max_tokens)}
            r = requests.post(str(self.endpoint), files=files, data=data, timeout=self.timeout)
            if r.status_code != 200:
                log.warning(f"VisionEngine request failed status={r.status_code}")
                return None
            js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"summary": r.text}
            summary = js.get("summary") or js.get("text") or js.get("data")
            if isinstance(summary, str):
                return summary.strip()
            return None
        except Exception as e:
            log.warning(f"VisionEngine error: {e}")
            return None


class DashScopeVisionEngine:
    """图像理解引擎 — 直接调用 DashScope OpenAI-compatible 多模态 API。

    复用 DASHSCOPE_BASE_URL + DASHSCOPE_API_KEY，调用 qwen-vl-max 等多模态模型，
    无需部署独立 HTTP 服务。
    """

    def __init__(self) -> None:
        self.enabled = bool(getattr(settings, "SM_VISION_ENABLED", True))
        self.model = str(getattr(settings, "SM_VISION_MODEL", "qwen-vl-max") or "qwen-vl-max")
        self.max_tokens = int(getattr(settings, "SM_VISION_MAX_TOKENS", 256) or 256)
        self.timeout = float(getattr(settings, "SM_VISION_TIMEOUT_SECS", 60) or 60)
        self.base_url = getattr(settings, "DASHSCOPE_BASE_URL", None)
        self.api_key = getattr(settings, "DASHSCOPE_API_KEY", None)

    def is_available(self) -> bool:
        return self.enabled and bool(self.base_url) and bool(self.api_key)

    def summarize_figure(self, image_bytes: bytes, caption: str | None = None) -> Optional[str]:
        if not self.is_available():
            return None
        try:
            import httpx

            b64 = base64.b64encode(image_bytes).decode("ascii")
            data_uri = f"data:image/png;base64,{b64}"

            prompt_text = (
                "请用一段简洁的中文描述这张学术论文中的图表内容，包括其展示的主要信息和关键发现。"
            )
            if caption:
                prompt_text += f"\n图表原始标题：{caption}"

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": prompt_text},
                    ],
                }
            ]
            url = f"{str(self.base_url).rstrip('/')}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": 0.2,
                "stream": False,
            }
            r = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
            r.raise_for_status()
            obj = r.json()
            choices = obj.get("choices") or []
            if choices:
                text = choices[0].get("message", {}).get("content") or ""
                if isinstance(text, str) and text.strip():
                    return text.strip()
            return None
        except Exception as e:
            log.warning(f"DashScopeVisionEngine error: {e}")
            return None


def get_vision_engine() -> VisionEngine | DashScopeVisionEngine:
    """Factory: return the appropriate vision engine based on config."""
    vision_type = str(getattr(settings, "SM_VISION_TYPE", "dashscope") or "dashscope").strip().lower()
    if vision_type == "dashscope":
        return DashScopeVisionEngine()
    return VisionEngine()