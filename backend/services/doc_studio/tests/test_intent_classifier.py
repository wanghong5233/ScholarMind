import types

from service.intent_classifier import IntentType, classify_intent


def test_classify_intent_edit_with_selection():
    context = {"selection": {"text": "some abstract"}}
    result = classify_intent("帮我优化这一段摘要", context)
    assert result.intent == IntentType.EDIT
    assert result.confidence > 0.5


def test_classify_intent_qa_without_selection():
    result = classify_intent("What is reinforcement learning?")
    assert result.intent == IntentType.QA
    assert result.confidence > 0.5


def test_classify_intent_suggest():
    result = classify_intent("帮我检查是否可以优化一下？")
    assert result.intent == IntentType.SUGGEST


def test_classify_intent_citation():
    result = classify_intent("这里插入引用 \\cite{}")
    assert result.intent == IntentType.CITATION


def test_classify_intent_handles_negation():
    result = classify_intent("不要修改这段内容")
    assert result.intent != IntentType.EDIT
    assert result.confidence < 0.5

