from pathlib import Path
import importlib.util
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

PYDANTIC_SETTINGS_AVAILABLE = importlib.util.find_spec("pydantic_settings") is not None


def _load_symbols():
    if not PYDANTIC_SETTINGS_AVAILABLE:
        pytest.skip("pydantic_settings is not installed in this test environment")
    from service.tools.analysis_tools import SemanticCodeSearchTool, _WorkspaceEmbeddingIndex

    return SemanticCodeSearchTool, _WorkspaceEmbeddingIndex


def test_build_chunks_respects_overlap_and_limit():
    _, workspace_embedding_index = _load_symbols()
    index = workspace_embedding_index()
    index._chunk_lines = 4
    index._chunk_overlap = 1
    index._max_chunks_per_file = 3

    lines = [f"line-{idx}" for idx in range(1, 20)]
    chunks = index._build_chunks("demo.py", lines)

    assert len(chunks) == 3
    assert chunks[0]["start_line"] == 1
    assert chunks[0]["end_line"] == 4
    assert chunks[1]["start_line"] == 4
    assert chunks[1]["end_line"] == 7
    assert chunks[2]["start_line"] == 7
    assert chunks[2]["end_line"] == 10


def test_pick_anchor_line_prefers_token_overlap():
    semantic_code_search_tool, _ = _load_symbols()
    lines = [
        "def helper():",
        "    pass",
        "def rollback_pending_send_to_composer():",
        "    return True",
        "# tail",
    ]
    anchor_line, lexical_score = semantic_code_search_tool._pick_anchor_line(
        query_tokens=["rollback", "composer"],
        query="rollback composer",
        normalized_query="rollback composer",
        lines=lines,
        start_line=1,
        end_line=5,
    )
    assert anchor_line == 3
    assert lexical_score > 0


def test_workspace_index_file_is_stable(tmp_path):
    _, workspace_embedding_index = _load_symbols()
    index = workspace_embedding_index()
    index._index_dir = tmp_path

    first = index._workspace_index_file(Path("/workspace/demo"))
    second = index._workspace_index_file(Path("/workspace/demo"))
    third = index._workspace_index_file(Path("/workspace/other"))

    assert first == second
    assert first != third


def test_build_persist_payload_contains_chunk_vectors():
    _, workspace_embedding_index = _load_symbols()
    index = workspace_embedding_index()
    state = index._new_state()
    state["files"] = {
        "demo.py": {
            "hash": "abc123",
            "updated_at": 12.0,
            "size": 88,
            "mtime_ns": 345,
            "chunks": [
                {
                    "file_path": "demo.py",
                    "start_line": 3,
                    "end_line": 9,
                    "vector": [0.1, 0.2, 0.3],
                }
            ],
        }
    }
    payload = index._build_persist_payload(Path("/workspace/demo"), state)

    assert payload["version"] == index._STATE_VERSION
    assert "demo.py" in payload["files"]
    assert payload["files"]["demo.py"]["chunks"][0]["vector"] == [0.1, 0.2, 0.3]
