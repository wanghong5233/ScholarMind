from pathlib import Path
import importlib.util
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

PYDANTIC_SETTINGS_AVAILABLE = importlib.util.find_spec("pydantic_settings") is not None


def _load_symbols():
    if not PYDANTIC_SETTINGS_AVAILABLE:
        pytest.skip("pydantic_settings is not installed in this test environment")
    from service.intent_classifier import IntentType, classify_intent

    return IntentType, classify_intent


def test_classify_intent_edit_with_selection():
    intent_type, classify_intent = _load_symbols()
    context = {"selection": {"text": "some abstract"}}
    result = classify_intent("帮我优化这一段摘要", context)
    assert result.intent == intent_type.EDIT
    assert result.confidence > 0.5


def test_classify_intent_qa_without_selection():
    intent_type, classify_intent = _load_symbols()
    result = classify_intent("What is reinforcement learning?")
    assert result.intent == intent_type.QA
    assert result.confidence > 0.5


def test_classify_intent_suggest():
    intent_type, classify_intent = _load_symbols()
    result = classify_intent("帮我检查是否可以优化一下？")
    assert result.intent == intent_type.SUGGEST


def test_classify_intent_citation():
    intent_type, classify_intent = _load_symbols()
    result = classify_intent("这里插入引用 \\cite{}")
    assert result.intent == intent_type.CITATION


def test_classify_intent_file_op_for_markdown_generation():
    intent_type, classify_intent = _load_symbols()
    result = classify_intent("请把当前分析总结成 md 文档并保存到文件")
    assert result.intent == intent_type.FILE_OP


def test_classify_intent_file_op_for_rename_and_delete():
    intent_type, classify_intent = _load_symbols()
    rename_result = classify_intent("把 docs/report.md 重命名为 docs/report-v2.md")
    delete_result = classify_intent("删除 archive/old.md")
    assert rename_result.intent == intent_type.FILE_OP
    assert delete_result.intent == intent_type.FILE_OP


def test_classify_intent_handles_negation():
    intent_type, classify_intent = _load_symbols()
    result = classify_intent("不要修改这段内容")
    assert result.intent != intent_type.EDIT
    assert result.confidence < 0.5

