from pathlib import Path
import sys
import importlib.util

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


def _load_symbols():
    if not FASTAPI_AVAILABLE:
        pytest.skip("fastapi is not installed in this test environment")
    from router.agent_rt import (
        _build_virtual_selections_from_full_file_mentions,
        _strip_line_number_prefix,
    )

    return _build_virtual_selections_from_full_file_mentions, _strip_line_number_prefix


def test_strip_line_number_prefix_removes_markers_and_line_numbers():
    _, strip_line_number_prefix = _load_symbols()
    excerpt = "[HEAD L1-L2]\nL1: first line\nL2: second line\n...\n[TRUNCATED]"
    result = strip_line_number_prefix(excerpt)
    assert result == "first line\nsecond line"


def test_build_virtual_selection_from_single_full_file_mention():
    build_virtual_selections, _ = _load_symbols()
    mentions = [
        {
            "id": "1",
            "file_path": "main.tex",
            "placeholder": "@file1",
            "strategy": "full",
            "total_chars": 128,
            "content_excerpt": "L1: \\section{Intro}\nL2: Hello",
        }
    ]
    selections = build_virtual_selections(
        file_mentions=mentions,
        user_intent="请重写这个文件并润色语气",
    )
    assert len(selections) == 1
    selection = selections[0]
    assert selection["start"] == 0
    assert selection["end"] == 128
    assert selection["file_path"] == "main.tex"
    assert selection["placeholder"] == "@selection1"
    assert "\\section{Intro}" in selection["text"]


def test_build_virtual_selection_skips_non_edit_intent():
    build_virtual_selections, _ = _load_symbols()
    mentions = [
        {
            "id": "1",
            "file_path": "main.tex",
            "placeholder": "@file1",
            "strategy": "full",
            "total_chars": 64,
            "content_excerpt": "L1: title",
        }
    ]
    selections = build_virtual_selections(
        file_mentions=mentions,
        user_intent="这个文件主要讲了什么？",
    )
    assert selections == []


def test_build_virtual_selection_skips_non_full_or_multi_mentions():
    build_virtual_selections, _ = _load_symbols()
    condensed_mentions = [
        {
            "id": "1",
            "file_path": "main.tex",
            "placeholder": "@file1",
            "strategy": "head_tail_keyword_condensed",
            "total_chars": 6000,
            "content_excerpt": "L1: only preview",
        }
    ]
    selections = build_virtual_selections(
        file_mentions=condensed_mentions,
        user_intent="请修改",
    )
    assert selections == []

    multi_mentions = [
        {
            "id": "1",
            "file_path": "a.tex",
            "placeholder": "@file1",
            "strategy": "full",
            "total_chars": 120,
            "content_excerpt": "L1: A",
        },
        {
            "id": "2",
            "file_path": "b.tex",
            "placeholder": "@file2",
            "strategy": "full",
            "total_chars": 120,
            "content_excerpt": "L1: B",
        },
    ]
    selections = build_virtual_selections(
        file_mentions=multi_mentions,
        user_intent="请修改",
    )
    assert selections == []
