"""DeepResearch microservice entrypoint."""

from typing import Optional
import logging
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from router import research_rt
from utils.trace import clear_trace_id, get_trace_id, set_trace_id


class HealthFilter(logging.Filter):
    """Filter out /health access logs to reduce noise."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        """Return False for /health entries to silence access logs."""

        msg = str(record.getMessage())
        return "/health" not in msg


class TraceIdFilter(logging.Filter):
    """Inject trace_id into log records."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        """Attach trace_id if missing."""

        if not hasattr(record, "trace_id"):
            record.trace_id = get_trace_id() or "-"
        return True


class SafeTraceIdFormatter(logging.Formatter):
    """Formatter that tolerates missing trace_id fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log records while ensuring trace_id presence."""

        if not hasattr(record, "trace_id"):
            record.trace_id = get_trace_id() or "-"
        return super().format(record)


handler = logging.StreamHandler()
formatter = SafeTraceIdFormatter(
    fmt="%(asctime)s [%(levelname)s] [trace_id=%(trace_id)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)

root_logger = logging.getLogger()
root_logger.setLevel(settings.LOG_LEVEL)
if not root_logger.handlers:
    root_logger.addHandler(handler)
root_logger.addFilter(TraceIdFilter())

uvicorn_access = logging.getLogger("uvicorn.access")
uvicorn_access.setLevel(settings.LOG_LEVEL)
uvicorn_access.addFilter(HealthFilter())
uvicorn_access.addFilter(TraceIdFilter())

app = FastAPI(
    title="DeepResearch Service",
    version=settings.SERVICE_VERSION,
    description="DeepResearch microservice with queue-based orchestration.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_trace_id(request, call_next):
    """Ensure every request has a trace id for logging."""

    incoming_trace_id: Optional[str] = request.headers.get("X-Trace-Id")
    trace_id = incoming_trace_id or str(uuid.uuid4())
    set_trace_id(trace_id)
    try:
        response = await call_next(request)
    finally:
        clear_trace_id()
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.get("/health")
async def health_check():
    """Health check endpoint."""

    return {
        "status": "healthy",
        "service": settings.SERVICE_NAME,
        "version": settings.SERVICE_VERSION,
    }


app.include_router(research_rt.router, prefix="/api", tags=["DeepResearch"])


@app.on_event("startup")
async def recover_stale_runs() -> None:
    """Mark in-flight runs as failed after a service restart."""

    if settings.AUTO_RECOVER_RUNS:
        marked_failed = await research_rt.run_manager.bootstrap()
        if marked_failed:
            logging.getLogger(__name__).warning(
                "Marked %s stale DeepResearch runs", marked_failed
            )
    await research_rt.run_manager.start_scheduler()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
