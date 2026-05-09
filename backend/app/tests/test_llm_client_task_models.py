from __future__ import annotations

from core.config import settings
from service.core.rag.llm.client import LLMClient


# 这里的模型名字仅作为字符串路由 key 使用：测试覆盖的是 task -> 配置项 -> 模型名
# 的派发链路。统一改用 qwen3-max / qwen-max 是为了和"全局禁用 qwen-plus / qwen-turbo"
# 的策略保持一致，避免读到老名字误以为这些模型仍在白名单内。
def test_llm_client_task_model_routing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SM_LLM_TYPE", "dashscope")
    monkeypatch.setattr(settings, "DASHSCOPE_MODEL_NAME", "qwen3-max")
    monkeypatch.setattr(settings, "SM_LLM_MODEL_ANSWER", "qwen3-max")
    monkeypatch.setattr(settings, "SM_LLM_MODEL_AUX", "qwen-max")
    monkeypatch.setattr(settings, "SM_LLM_MODEL_GRAPH", "qwen-max")
    monkeypatch.setattr(settings, "SM_LLM_MODEL_SUMMARY", "qwen-max")

    answer = LLMClient(task="answer")
    summary = LLMClient(task="summary")
    aux = LLMClient(task="aux")
    graph = LLMClient(task="graph")
    rewrite = LLMClient(task="rewrite")

    assert answer.model == "qwen3-max"
    assert summary.model == "qwen-max"
    assert aux.model == "qwen-max"
    assert graph.model == "qwen-max"
    assert rewrite.model == "qwen-max"


def test_llm_client_task_model_fallback(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SM_LLM_TYPE", "dashscope")
    monkeypatch.setattr(settings, "DASHSCOPE_MODEL_NAME", "qwen3-max")
    monkeypatch.setattr(settings, "SM_LLM_MODEL_ANSWER", None)
    monkeypatch.setattr(settings, "SM_LLM_MODEL_AUX", None)
    monkeypatch.setattr(settings, "SM_LLM_MODEL_GRAPH", None)
    monkeypatch.setattr(settings, "SM_LLM_MODEL_SUMMARY", None)

    answer = LLMClient(task="answer")
    summary = LLMClient(task="summary")
    graph = LLMClient(task="graph")

    assert answer.model == "qwen3-max"
    assert summary.model == "qwen3-max"
    assert graph.model == "qwen3-max"
