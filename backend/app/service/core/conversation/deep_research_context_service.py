"""Load session-level DeepResearch result summaries for chat context."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import httpx

from core.config import settings
from utils.get_logger import logger


class DeepResearchContextService:
    """Fetch concise DeepResearch summaries from the DeepResearch service."""

    def __init__(self) -> None:
        self._base_url = str(settings.DEEP_RESEARCH_SERVICE_URL or "").rstrip("/")
        timeout = float(getattr(settings, "SM_DEEP_RESEARCH_CONTEXT_TIMEOUT_SECS", 4) or 4)
        self._timeout = max(0.5, timeout)

    def build_context_text(
        self,
        *,
        session_id: str,
        user_id: int,
    ) -> Tuple[str, Dict[str, Any]]:
        """Build a compact result-only DeepResearch context string.

        Args:
            session_id (str): Session id from chat ask route.
            user_id (int): Current user id for ownership checks.

        Returns:
            Tuple[str, Dict[str, Any]]: Context text and debug metadata.
        """

        enabled = bool(getattr(settings, "SM_DEEP_RESEARCH_CONTEXT_ENABLED", True))
        debug: Dict[str, Any] = {
            "enabled": enabled,
            "source": "deep_research.session_context",
            "count": 0,
            "items": [],
        }
        if not enabled:
            debug["reason"] = "disabled"
            return "", debug
        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            debug["reason"] = "missing_session_id"
            return "", debug
        if not self._base_url:
            debug["reason"] = "missing_service_url"
            return "", debug
        max_runs = max(1, int(getattr(settings, "SM_DEEP_RESEARCH_CONTEXT_MAX_RUNS", 2) or 2))
        max_chars = max(
            200,
            int(getattr(settings, "SM_DEEP_RESEARCH_CONTEXT_MAX_CHARS", 1200) or 1200),
        )
        url = f"{self._base_url}/api/deep-research/session/{normalized_session}/context"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(
                    url,
                    headers={"X-User-Id": str(user_id)},
                    params={
                        "limit": max_runs,
                        "max_summary_chars": max_chars,
                    },
                )
            if response.status_code >= 400:
                debug["reason"] = f"http_{response.status_code}"
                return "", debug
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - context enrichment should be best-effort
            debug["reason"] = "request_failed"
            debug["error"] = str(exc)
            logger.warning(
                "DeepResearch context fetch failed: session=%s user=%s error=%s",
                normalized_session,
                user_id,
                exc,
            )
            return "", debug

        items_raw = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items_raw, list) or not items_raw:
            debug["reason"] = "empty"
            return "", debug

        lines: List[str] = [
            "以下为当前会话中已完成的深度研究结果摘要（仅结果，不含过程事件/工具原始输出）。",
            "仅在与当前问题相关时参考这些结论；若冲突，以最新检索证据为准。",
        ]
        item_debug: List[Dict[str, Any]] = []
        for idx, item in enumerate(items_raw, start=1):
            if not isinstance(item, dict):
                continue
            research_id = str(item.get("research_id") or "").strip()
            topic = str(item.get("topic") or "").strip() or "DeepResearch"
            summary = str(item.get("summary") or "").strip()
            if not summary:
                continue
            lines.append(f"[深研摘要 {idx}] 主题：{topic}")
            lines.append(f"结论摘要：{summary}")
            item_debug.append(
                {
                    "research_id": research_id,
                    "topic": topic,
                    "summary_chars": len(summary),
                }
            )
        if len(lines) <= 2:
            debug["reason"] = "no_valid_summary"
            return "", debug

        context_text = "\n".join(lines).strip()
        hard_cap = max_chars * max_runs + 400
        if len(context_text) > hard_cap:
            context_text = context_text[:hard_cap].rstrip() + "..."
        debug["count"] = len(item_debug)
        debug["items"] = item_debug
        return context_text, debug
