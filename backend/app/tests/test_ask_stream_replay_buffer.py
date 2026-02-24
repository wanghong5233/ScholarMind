from __future__ import annotations

from service.core.conversation.ask_stream_replay_buffer import AskStreamReplayBuffer


def test_replay_buffer_streams_all_events_after_completion() -> None:
    buffer = AskStreamReplayBuffer(ttl_seconds=120, max_runs=8, max_events_per_run=32)
    run = buffer.create_run(run_id="r1", session_id="s1", user_id=1)
    assert run.run_id == "r1"

    buffer.append_event(run_id="r1", seq=1, frame="event: progress\ndata: {\"seq\":1}\n\n")
    buffer.append_event(run_id="r1", seq=2, frame="event: delta\ndata: {\"seq\":2}\n\n")
    buffer.mark_completed("r1")

    frames = list(buffer.stream_from(run_id="r1", since_seq=-1, wait_timeout_seconds=1))
    assert len(frames) == 2
    assert '"seq":1' in frames[0]
    assert '"seq":2' in frames[1]


def test_replay_buffer_respects_since_seq() -> None:
    buffer = AskStreamReplayBuffer(ttl_seconds=120, max_runs=8, max_events_per_run=32)
    buffer.create_run(run_id="r2", session_id="s1", user_id=1)
    buffer.append_event(run_id="r2", seq=1, frame="event: progress\ndata: {\"seq\":1}\n\n")
    buffer.append_event(run_id="r2", seq=2, frame="event: delta\ndata: {\"seq\":2}\n\n")
    buffer.append_event(run_id="r2", seq=3, frame="event: completion\ndata: {\"seq\":3}\n\n")
    buffer.mark_completed("r2")

    frames = list(buffer.stream_from(run_id="r2", since_seq=1, wait_timeout_seconds=1))
    assert len(frames) == 2
    assert '"seq":2' in frames[0]
    assert '"seq":3' in frames[1]


def test_replay_buffer_stats_snapshot_from_memory() -> None:
    buffer = AskStreamReplayBuffer(
        ttl_seconds=120,
        max_runs=8,
        max_events_per_run=32,
        enable_redis=False,
    )
    buffer.create_run(run_id="r3", session_id="s9", user_id=9)
    buffer.append_event(run_id="r3", seq=1, frame="event: progress\ndata: {\"seq\":1}\n\n")
    snapshot = buffer.stats_snapshot()
    assert snapshot["memory"]["runs"] == 1
    assert snapshot["memory"]["events"] == 1
    assert snapshot["redis"]["enabled"] is False
