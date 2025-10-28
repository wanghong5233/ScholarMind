from __future__ import annotations

from typing import Optional
import requests
from core.config import settings
from utils.get_logger import log


class VisionEngine:
    """图像理解引擎（用于图表语义摘要）。
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


