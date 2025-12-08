import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from workspace_cache import WorkspaceContextCache, WorkspaceSnapshot


def _make_snapshot(signature: str = "sig") -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        file_list=["main.tex"],
        citation_mappings={"1": "ref1"},
        workspace_config={"main_file": "main.tex"},
        original_file_contents={"main.tex": "content"},
        signature=signature,
    )


def test_workspace_cache_hit_and_signature_match():
    cache = WorkspaceContextCache(max_entries=2, ttl_seconds=60)
    key = (1, "ws")
    cache.set(key, _make_snapshot())

    snapshot = cache.get(key, "sig")
    assert snapshot is not None
    assert snapshot.file_list == ["main.tex"]


def test_workspace_cache_signature_mismatch_evicted():
    cache = WorkspaceContextCache(max_entries=2, ttl_seconds=60)
    key = (1, "ws")
    cache.set(key, _make_snapshot("sig"))

    assert cache.get(key, "another") is None


def test_workspace_cache_ttl_expiration():
    cache = WorkspaceContextCache(max_entries=2, ttl_seconds=0.1)
    key = (1, "ws")
    cache.set(key, _make_snapshot("sig"))
    time.sleep(0.2)

    assert cache.get(key, "sig") is None


def test_workspace_cache_eviction_order():
    cache = WorkspaceContextCache(max_entries=1, ttl_seconds=60)
    first = (1, "ws")
    second = (2, "ws2")

    cache.set(first, _make_snapshot("sig1"))
    cache.set(second, _make_snapshot("sig2"))

    assert cache.get(first, "sig1") is None
    assert cache.get(second, "sig2") is not None

