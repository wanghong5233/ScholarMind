from __future__ import annotations

import os
from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_routing_eval.db")

from service.core.conversation.routing_decision import (
    classify_query_intent,
    coerce_confidence,
    derive_route_type,
    is_in_rollout,
)


def test_derive_route_type() -> None:
    assert (
        derive_route_type(
            index_mode="hybrid",
            retrieval_disabled=False,
        )
        == "rag_hybrid"
    )
    assert (
        derive_route_type(
            index_mode="session_only",
            retrieval_disabled=False,
        )
        == "rag_session"
    )
    assert (
        derive_route_type(
            index_mode="global_only",
            retrieval_disabled=False,
        )
        == "rag_global"
    )
    assert (
        derive_route_type(
            index_mode="hybrid",
            retrieval_disabled=True,
        )
        == "chat_only"
    )


def test_coerce_confidence_clamp() -> None:
    assert coerce_confidence("0.85", default=0.0) == 0.85
    assert coerce_confidence("abc", default=0.42) == 0.42
    assert coerce_confidence(9, default=0.0) == 1.0
    assert coerce_confidence(-3, default=0.0) == 0.0


def test_intent_without_retrieval_plan_defaults_to_chat() -> None:
    result = classify_query_intent(question="你是什么模型？", retrieval_plan=[])
    assert result["need_retrieval"] is False
    assert result["query_type"] == "chat"
    assert result["confidence"] == 1.0
    assert result["reason"] == "no_retrieval_plan"


def test_rollout_is_stable_and_clamped() -> None:
    selected_a, bucket_a, percent_a = is_in_rollout(
        user_id=42,
        session_id="session_1",
        key="adaptive_retrieval_v1",
        percent=30,
    )
    selected_b, bucket_b, percent_b = is_in_rollout(
        user_id=42,
        session_id="session_1",
        key="adaptive_retrieval_v1",
        percent=30,
    )
    assert bucket_a == bucket_b
    assert selected_a == selected_b
    assert percent_a == 30
    assert percent_b == 30

    selected_none, _, percent_none = is_in_rollout(
        user_id=42,
        session_id="session_1",
        key="adaptive_retrieval_v1",
        percent=-5,
    )
    selected_all, _, percent_all = is_in_rollout(
        user_id=42,
        session_id="session_1",
        key="adaptive_retrieval_v1",
        percent=500,
    )
    assert selected_none is False
    assert percent_none == 0
    assert selected_all is True
    assert percent_all == 100
