from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx

from core.config import settings
from service.core.rag.llm.client import LLMClient

logger = logging.getLogger(__name__)


def coerce_confidence(value: Any, *, default: float = 0.0) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = float(default)
    return max(0.0, min(1.0, confidence))


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return default


def _extract_first_json_object(text: str) -> str | None:
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def derive_route_type(*, index_mode: str, retrieval_disabled: bool) -> str:
    if retrieval_disabled:
        return "chat_only"
    if index_mode == "session_only":
        return "rag_session"
    if index_mode == "global_only":
        return "rag_global"
    if index_mode == "hybrid":
        return "rag_hybrid"
    if index_mode == "disabled":
        return "chat_only"
    return "rag_unknown"


def rollout_bucket(
    *,
    user_id: str | int,
    session_id: str,
    key: str,
) -> int:
    base = f"{key}|{user_id}|{session_id}".encode("utf-8")
    digest = hashlib.sha256(base).hexdigest()
    return int(digest[:8], 16) % 100


def is_in_rollout(
    *,
    user_id: str | int,
    session_id: str,
    key: str,
    percent: int,
) -> tuple[bool, int, int]:
    try:
        normalized_percent = int(percent)
    except (TypeError, ValueError):
        normalized_percent = 0
    normalized_percent = max(0, min(100, normalized_percent))
    bucket = rollout_bucket(user_id=user_id, session_id=session_id, key=key)
    return bucket < normalized_percent, bucket, normalized_percent


def classify_query_intent(
    *,
    question: str,
    retrieval_plan: list[tuple[str, int]],
) -> dict[str, Any]:
    """Classify whether a query needs KB retrieval."""
    if not question or not question.strip():
        return {
            "need_retrieval": False,
            "query_type": "chat",
            "confidence": 1.0,
            "reason": "empty_question",
        }
    if not retrieval_plan:
        return {
            "need_retrieval": False,
            "query_type": "chat",
            "confidence": 1.0,
            "reason": "no_retrieval_plan",
        }

    prompt = (
        "你是 RAG 路由器，只做“是否需要检索知识库”的判定。\n"
        "第一性原则：只有当回答依赖用户知识库中的文档证据时，才 need_retrieval=true。\n"
        "若问题可直接由助手自身能力/系统元信息/通用常识回答，则 need_retrieval=false。\n\n"
        "仅输出一个合法 JSON 对象，不要 Markdown，不要解释。\n"
        "字段约束：\n"
        "- need_retrieval: 布尔值 true/false（不能是字符串）\n"
        "- query_type: factual|analytical|comparative|chat\n"
        "- confidence: 0~1 之间数字（不能是字符串）\n"
        "- reason: 不超过 20 字\n"
        "输出示例："
        "{\"need_retrieval\": false, \"query_type\": \"chat\", \"confidence\": 0.95, \"reason\": \"通用问答\"}\n\n"
        "判定样例：\n"
        "Q: 你是什么模型？ -> need_retrieval=false, query_type=chat\n"
        "Q: Cursor Agent 模式下这个需求要改代码还是先提问？ -> need_retrieval=false, query_type=chat\n"
        "Q: 我上传的三篇论文对比实验结果有什么差异？请给出处 -> need_retrieval=true, query_type=comparative\n"
        "Q: 这篇文档第 4 节的核心结论是什么？ -> need_retrieval=true, query_type=factual\n\n"
        f"用户问题：{question[:300]}"
    )
    messages = [{"role": "user", "content": prompt}]

    try:
        classifier_model_raw = (
            getattr(settings, "SM_LLM_MODEL_AUX", None)
            or getattr(settings, "SM_LLM_MODEL_SUMMARY", None)
        )
        classifier_model = (
            str(classifier_model_raw).strip()
            if isinstance(classifier_model_raw, str)
            else None
        ) or None
        llm = LLMClient(task="aux", model=classifier_model)
        result = llm.generate(messages, stream=False)
        if not isinstance(result, str):
            result = "".join(result)

        text = result.strip()
        if not text:
            logger.warning(
                "[ADAPTIVE_RETRIEVAL] empty classifier output; "
                f"runtime={llm.get_last_runtime_model()} usage={llm.get_last_usage()}"
            )
        payload = _extract_first_json_object(text)
        if payload:
            parsed = json.loads(payload)
            need_retrieval = _coerce_bool(
                parsed.get("need_retrieval", True),
                default=True,
            )
            query_type = str(parsed.get("query_type", "factual")).strip().lower()
            if query_type not in {"factual", "analytical", "comparative", "chat"}:
                query_type = "factual" if need_retrieval else "chat"
            reason = str(parsed.get("reason", "llm")).strip() or "llm"
            runtime = llm.get_last_runtime_model() or {}
            return {
                "need_retrieval": need_retrieval,
                "query_type": query_type,
                "confidence": coerce_confidence(parsed.get("confidence", 0.0), default=0.0),
                "reason": reason[:80],
                "policy_version": runtime.get("policy_version"),
            }
        logger.warning(
            "[ADAPTIVE_RETRIEVAL] intent output missing JSON object; "
            f"raw_preview={text[:200]!r}"
        )
    except (
        httpx.HTTPError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        RuntimeError,
    ) as exc:
        logger.warning(f"[ADAPTIVE_RETRIEVAL] intent classification failed: {exc}")

    return {
        "need_retrieval": True,
        "query_type": "factual",
        "confidence": 0.0,
        "reason": "fallback_true",
    }
