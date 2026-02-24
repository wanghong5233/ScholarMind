from service.core.conversation.ask_run_control import AskRunControl


def test_cancel_run_with_owner_matches() -> None:
    control = AskRunControl(ttl_seconds=120)
    control.register_run(run_id="run-1", session_id="s1", user_id=100)
    assert control.cancel_run(run_id="run-1", session_id="s1", user_id=100) is True
    assert control.is_cancelled("run-1") is True


def test_cancel_run_rejects_wrong_owner() -> None:
    control = AskRunControl(ttl_seconds=120)
    control.register_run(run_id="run-2", session_id="s1", user_id=100)
    assert control.cancel_run(run_id="run-2", session_id="s2", user_id=100) is False
    assert control.cancel_run(run_id="run-2", session_id="s1", user_id=101) is False
    assert control.is_cancelled("run-2") is False
