from __future__ import annotations

from core.config import settings
from service.core.rag.llm.client import LLMClient


def test_llm_client_task_model_routing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SM_LLM_TYPE", "dashscope")
    monkeypatch.setattr(settings, "DASHSCOPE_MODEL_NAME", "qwen-plus")
    monkeypatch.setattr(settings, "SM_LLM_MODEL_ANSWER", "qwen-plus")
    monkeypatch.setattr(settings, "SM_LLM_MODEL_AUX", "qwen-turbo")
    monkeypatch.setattr(settings, "SM_LLM_MODEL_GRAPH", "qwen-turbo")
    monkeypatch.setattr(settings, "SM_LLM_MODEL_SUMMARY", "qwen-turbo")

    answer = LLMClient(task="answer")
    summary = LLMClient(task="summary")
    aux = LLMClient(task="aux")
    graph = LLMClient(task="graph")
    rewrite = LLMClient(task="rewrite")

    assert answer.model == "qwen-plus"
    assert summary.model == "qwen-turbo"
    assert aux.model == "qwen-turbo"
    assert graph.model == "qwen-turbo"
    assert rewrite.model == "qwen-turbo"


def test_llm_client_task_model_fallback(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SM_LLM_TYPE", "dashscope")
    monkeypatch.setattr(settings, "DASHSCOPE_MODEL_NAME", "qwen-plus")
    monkeypatch.setattr(settings, "SM_LLM_MODEL_ANSWER", None)
    monkeypatch.setattr(settings, "SM_LLM_MODEL_AUX", None)
    monkeypatch.setattr(settings, "SM_LLM_MODEL_GRAPH", None)
    monkeypatch.setattr(settings, "SM_LLM_MODEL_SUMMARY", None)

    answer = LLMClient(task="answer")
    summary = LLMClient(task="summary")
    graph = LLMClient(task="graph")

    assert answer.model == "qwen-plus"
    assert summary.model == "qwen-plus"
    assert graph.model == "qwen-plus"
