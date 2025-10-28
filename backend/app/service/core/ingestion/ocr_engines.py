from __future__ import annotations

from typing import Optional
import requests
from core.config import settings
from utils.get_logger import log


class OCREngine:
    """可插拔 OCR 引擎（用于公式 LaTeX 识别）。
    当前支持 deepseek 与 paddleocr 两种，通过 HTTP 调用外部服务。
    """

    def __init__(self) -> None:
        self.enabled = bool(getattr(settings, "SM_OCR_ENABLED", True))
        self.engine = getattr(settings, "SM_OCR_ENGINE", "deepseek")
        self.timeout = getattr(settings, "SM_OCR_TIMEOUT_SECS", 60)
        self.deepseek = getattr(settings, "SM_OCR_ENDPOINT_DEEPSEEK", None)
        self.paddle = getattr(settings, "SM_OCR_ENDPOINT_PADDLE", None)

    def is_available(self) -> bool:
        if not self.enabled:
            return False
        if self.engine == "deepseek":
            return bool(self.deepseek)
        if self.engine == "paddleocr":
            return bool(self.paddle)
        return False

    def recog_equation_latex(self, image_bytes: bytes) -> Optional[str]:
        if not self.is_available():
            return None
        try:
            url = self.deepseek if self.engine == "deepseek" else self.paddle
            if not url:
                return None
            files = {"file": ("crop.png", image_bytes, "image/png")}
            r = requests.post(url, files=files, timeout=self.timeout)
            if r.status_code != 200:
                log.warning(f"OCREngine {self.engine} failed status={r.status_code}")
                return None
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"text": r.text}
            latex = data.get("latex") or data.get("text")
            return latex.strip() if isinstance(latex, str) else None
        except Exception as e:
            log.warning(f"OCREngine error: {e}")
            return None


