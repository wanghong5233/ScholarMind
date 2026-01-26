"""Trace id helpers for request-scoped logging."""

from contextvars import ContextVar


_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def set_trace_id(trace_id: str) -> None:
    """Store a trace id for the current execution context."""

    _trace_id.set(trace_id)


def get_trace_id() -> str | None:
    """Fetch the current trace id if available."""

    return _trace_id.get()


def clear_trace_id() -> None:
    """Clear the trace id for the current context."""

    _trace_id.set(None)
