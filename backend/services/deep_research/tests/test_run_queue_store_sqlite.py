"""Tests for SQLite-based queue store."""

from datetime import datetime, timedelta

from service.run_queue_store import SqliteRunQueueStore


def test_sqlite_queue_ordering_by_aging(tmp_path) -> None:
    """Ensure priority aging affects queue order."""

    store = SqliteRunQueueStore(tmp_path)
    now = datetime.utcnow()
    older = (now - timedelta(seconds=1200)).isoformat()
    recent = (now - timedelta(seconds=30)).isoformat()

    store.enqueue("run_low", -5, older)
    store.enqueue("run_high", 8, recent)

    entries = store.list_pending(aging_seconds=60)
    assert entries[0].research_id == "run_low"
    assert entries[0].effective_priority >= entries[1].effective_priority


def test_sqlite_claim_and_remove(tmp_path) -> None:
    """Ensure claiming respects max_active_runs and remove frees slot."""

    store = SqliteRunQueueStore(tmp_path)
    now = datetime.utcnow().isoformat()
    store.enqueue("run_a", 1, now)
    store.enqueue("run_b", 0, now)

    first = store.claim_next(
        owner_id="worker_1",
        lease_seconds=30,
        max_active_runs=1,
        aging_seconds=300,
    )
    assert first in {"run_a", "run_b"}
    assert store.list_running_by_owner("worker_1") == [first]

    second = store.claim_next(
        owner_id="worker_1",
        lease_seconds=30,
        max_active_runs=1,
        aging_seconds=300,
    )
    assert second is None

    store.remove(first)
    third = store.claim_next(
        owner_id="worker_1",
        lease_seconds=30,
        max_active_runs=1,
        aging_seconds=300,
    )
    assert third in {"run_a", "run_b"}
    assert third != first


def test_sqlite_requeue(tmp_path) -> None:
    """Ensure requeue moves a running run back to queued."""

    store = SqliteRunQueueStore(tmp_path)
    now = datetime.utcnow().isoformat()
    store.enqueue("run_x", 0, now)
    claimed = store.claim_next(
        owner_id="worker_1",
        lease_seconds=10,
        max_active_runs=1,
        aging_seconds=300,
    )
    assert claimed == "run_x"

    store.requeue("run_x", priority=0, submitted_at=now)
    assert store.get_status("run_x") == "queued"
    pending = store.list_pending(aging_seconds=300)
    assert [entry.research_id for entry in pending] == ["run_x"]
