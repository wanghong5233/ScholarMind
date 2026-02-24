from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import pytest
import pydantic
from pydantic import ValidationError


def _load_ask_request_model():
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "session.py"
    spec = importlib.util.spec_from_file_location("session_schema_for_test", schema_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)

    # 避免测试环境缺少 pydantic-settings 时触发 core.config 导入失败
    fake_core = types.ModuleType("core")
    fake_core.__path__ = []  # type: ignore[attr-defined]
    fake_config = types.ModuleType("core.config")
    fake_config.settings = types.SimpleNamespace(SM_RAG_TOPK=6)
    fake_core.config = fake_config  # type: ignore[attr-defined]

    prev_core = sys.modules.get("core")
    prev_core_config = sys.modules.get("core.config")
    sys.modules["core"] = fake_core
    sys.modules["core.config"] = fake_config
    try:
        spec.loader.exec_module(module)
    finally:
        if prev_core is not None:
            sys.modules["core"] = prev_core
        else:
            sys.modules.pop("core", None)
        if prev_core_config is not None:
            sys.modules["core.config"] = prev_core_config
        else:
            sys.modules.pop("core.config", None)
    return module.AskRequest


def test_ask_request_rejects_unknown_fields() -> None:
    major = int(str(getattr(pydantic, "VERSION", "1.0")).split(".")[0])
    if major < 2:
        pytest.skip("Pydantic v1 环境下 model_config(extra=forbid) 不生效")
    AskRequest = _load_ask_request_model()
    with pytest.raises(ValidationError):
        AskRequest(
            question="hello",
            stream=True,
            unexpected_field="not-allowed",
        )


def test_ask_request_accepts_image_attachments() -> None:
    AskRequest = _load_ask_request_model()
    req = AskRequest(
        question="请分析图片",
        stream=True,
        llmModel="qwen-vl-max",
        imageAttachments=[
            {
                "id": "img-1",
                "name": "example.png",
                "dataUrl": "data:image/png;base64,AAAA",
                "mimeType": "image/png",
                "size": 4,
            }
        ],
    )
    assert req.imageAttachments is not None
    assert req.imageAttachments[0].name == "example.png"


def test_ask_request_accepts_persist_history_flag() -> None:
    AskRequest = _load_ask_request_model()
    req = AskRequest(
        question="internal call",
        stream=False,
        persistHistory=False,
    )
    assert req.persistHistory is False
