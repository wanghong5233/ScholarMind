"""DeepResearch API endpoints."""

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi_jwt import JwtAccessBearerCookie, JwtAuthorizationCredentials
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from core.config import settings
from schemas.common import (
    BlockEvidence,
    DeepResearchMode,
    DeepResearchRequest,
    DeepResearchPlan,
    DeepResearchResponse,
    DeepResearchArchive,
    DeepResearchCompareRequest,
    DeepResearchCompareResponse,
    DeepResearchCompareSide,
    DeepResearchProgressResponse,
    DeepResearchQueueItem,
    DeepResearchQueueStatus,
    DeepResearchPriorityUpdateRequest,
    DeepResearchRunList,
    DeepResearchRunMeta,
    DeepResearchSessionContextResponse,
    DeepResearchSessionContextItem,
    DeepResearchStatus,
    DeepResearchSubmitResponse,
)
from schemas.idea_generation import (
    IdeaGenerationRequest,
    IdeaGenerationResponse,
    IdeaGenerationRunDetail,
    IdeaGenerationRunList,
    IdeaGenerationRunMeta,
    IdeaGenerationStatus,
)
from schemas.notebook import NotebookNoteRequest, NotebookNoteResponse
from agents.planner_agent import PlannerAgent
from service.archive_exporter import (
    build_block_evidence_zip,
    build_run_archive_zip,
    compute_file_sha256,
)
from service.compare_exporter import render_compare_markdown
from service.idea_generation_pipeline import IdeaGenerationPipeline
from service.notebook_pipeline import NotebookPipeline
from service.pipeline import ResearchPipeline
from service.rag_client import RAGClient
from service.report_exporter import render_html, render_pdf
from service.run_manager import RunManager
from service.state_store import StateStore
from utils.request_normalizer import apply_deep_research_preset
from utils.plan_override import extract_plan_override_items, to_plan_item_payload

router = APIRouter()
run_manager = RunManager(
    rag_service_url=settings.RAG_SERVICE_URL,
    data_root=settings.DATA_ROOT,
    request_timeout=settings.REQUEST_TIMEOUT,
)
internal_service_security = JwtAccessBearerCookie(
    secret_key=settings.JWT_SECRET_KEY or "__missing_internal_jwt_secret__",
    auto_error=False,
    access_expires_delta=timedelta(days=settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS),
)


def _parse_csv_values(raw_value: Optional[str]) -> set[str]:
    if not raw_value:
        return set()
    return {item.strip().lower() for item in raw_value.split(",") if item and item.strip()}


def _internal_service_allowlist() -> set[str]:
    return _parse_csv_values(settings.INTERNAL_SERVICE_ALLOWLIST)


def _extract_timestamp(payload: Dict[str, Any]) -> str:
    return (
        str(payload.get("submitted_at") or payload.get("started_at") or payload.get("finished_at") or "")
        .strip()
    )


def _normalize_run_meta_payload(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize run meta for backward-compatible response validation."""

    normalized = dict(meta or {})
    request_payload = normalized.get("request")
    if not isinstance(request_payload, dict):
        request_payload = {}
        normalized["request"] = request_payload

    topic = (
        str(normalized.get("topic") or "").strip()
        or str(request_payload.get("topic") or "").strip()
        or "DeepResearch"
    )
    normalized["topic"] = topic

    status_value = str(normalized.get("status") or "").strip().lower()
    if status_value not in DeepResearchStatus._value2member_map_:
        status_value = DeepResearchStatus.QUEUED.value
    normalized["status"] = status_value

    mode_value = str(normalized.get("mode") or request_payload.get("mode") or "").strip().lower()
    if mode_value not in DeepResearchMode._value2member_map_:
        mode_value = DeepResearchMode.QUEUE.value
    normalized["mode"] = mode_value
    return normalized


SNAPSHOT_COMPACT_REPORT_MAX_CHARS = 12000
SNAPSHOT_COMPACT_CITATIONS_MAX_ITEMS = 120


def _truncate_tail_text(text: str, max_chars: int) -> str:
    """Return a tail-truncated preview text for large markdown payloads."""

    value = str(text or "")
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return (
        f"[...已截断，仅展示末尾 {max_chars} 字符（总长 {len(value)}）...]\n\n"
        f"{value[-max_chars:]}"
    )


def _compact_report_payload(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Reduce snapshot report payload size for high-frequency polling."""

    if not isinstance(report, dict):
        return report
    compact = dict(report)
    compact["snapshot_compact"] = True
    for field in ("report_markdown", "draft_markdown"):
        raw_text = str(compact.get(field) or "")
        if not raw_text:
            continue
        if len(raw_text) > SNAPSHOT_COMPACT_REPORT_MAX_CHARS:
            compact[f"{field}_truncated"] = True
            compact[f"{field}_full_chars"] = len(raw_text)
            compact[field] = _truncate_tail_text(raw_text, SNAPSHOT_COMPACT_REPORT_MAX_CHARS)
    details = compact.get("report_details")
    if isinstance(details, dict):
        details_compact = dict(details)
        draft_text = str(details_compact.get("draft_markdown") or "")
        if len(draft_text) > SNAPSHOT_COMPACT_REPORT_MAX_CHARS:
            details_compact["draft_markdown_truncated"] = True
            details_compact["draft_markdown_full_chars"] = len(draft_text)
            details_compact["draft_markdown"] = _truncate_tail_text(
                draft_text, SNAPSHOT_COMPACT_REPORT_MAX_CHARS
            )
        compact["report_details"] = details_compact
    return compact


def _compact_citations_payload(citations: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Reduce citations payload for compact snapshot responses."""

    if not isinstance(citations, dict):
        return citations
    compact = dict(citations)
    items = compact.get("citations")
    if not isinstance(items, list):
        return compact
    total = len(items)
    if total > SNAPSHOT_COMPACT_CITATIONS_MAX_ITEMS:
        compact["citations_total"] = total
        compact["citations_truncated"] = True
        compact["citations"] = items[:SNAPSHOT_COMPACT_CITATIONS_MAX_ITEMS]
    return compact


def _extract_report_summary(markdown: str, *, max_chars: int = 800) -> str:
    """Extract a concise report summary from markdown text."""

    if not isinstance(markdown, str):
        return ""
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not lines:
        return ""
    summary_lines: list[str] = []
    for line in lines:
        if line.startswith("#") and summary_lines:
            break
        normalized = line.lstrip("#").strip() if line.startswith("#") else line
        if not normalized:
            continue
        summary_lines.append(normalized)
        if len(" ".join(summary_lines)) >= max_chars:
            break
    summary_text = " ".join(summary_lines).strip()
    if len(summary_text) > max_chars:
        summary_text = summary_text[:max_chars].rstrip() + "..."
    return summary_text


def _build_plan_stream_progress(
    message: str,
    *,
    event_type: str = "plan.progress",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a normalized progress event payload for plan streaming."""

    return {
        "research_id": "plan_preview",
        "stage": "planning",
        "event_type": event_type,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
        "payload": payload or {},
    }


def _is_retrieval_disabled(index_mode: Optional[str]) -> bool:
    """Return True when request explicitly disables session KB retrieval."""

    normalized = str(index_mode or "").strip().lower()
    return normalized in {"disabled", "off", "none", "false", "0"}


class AdminActionRequest(BaseModel):
    reason: Optional[str] = None


async def get_user_id(x_user_id: Optional[str] = Header(None, alias="X-User-Id")) -> int:
    """Resolve the user id from the request header."""

    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")
    try:
        return int(x_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid X-User-Id format") from exc


async def get_user_id_for_stream(
    request: Request,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
) -> int:
    """Resolve user id for streaming endpoints."""

    if x_user_id:
        try:
            return int(x_user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid X-User-Id format") from exc
    query_user_id = request.query_params.get("user_id")
    if not query_user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")
    try:
        return int(query_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user_id format") from exc


def get_internal_service_identity(
    subject: Optional[JwtAuthorizationCredentials] = Depends(internal_service_security),
) -> dict[str, Any]:
    """Validate internal service token for admin-only operations."""

    if not settings.JWT_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail="JWT secret not configured for internal service auth",
        )
    if subject is None:
        raise HTTPException(status_code=401, detail="Missing internal service token")
    payload = subject.subject or {}
    token_use = str(payload.get("token_use", "") or "").strip().lower()
    service_name = str(payload.get("service_name", "") or "").strip().lower()
    if token_use != "internal_service":
        raise HTTPException(status_code=403, detail="Internal service token required")
    if not service_name:
        raise HTTPException(status_code=403, detail="Invalid internal service token")
    allowlist = _internal_service_allowlist()
    if allowlist and service_name not in allowlist:
        raise HTTPException(status_code=403, detail="Service not allowed")
    return {
        "service_name": service_name,
        "user_id": payload.get("user_id"),
    }


@router.post(
    "/deep-research/plan",
    response_model=DeepResearchPlan,
    summary="Preview DeepResearch plan",
)
async def preview_deep_research_plan(
    payload: DeepResearchRequest,
    user_id: int = Depends(get_user_id),
) -> DeepResearchPlan:
    """Generate a preview plan for a DeepResearch run."""
    payload = apply_deep_research_preset(payload)
    override_items = extract_plan_override_items(
        payload.metadata,
        max_depth=payload.depth,
        max_breadth=payload.breadth,
    )
    if override_items:
        return DeepResearchPlan(items=to_plan_item_payload(override_items))

    planner = PlannerAgent(
        depth=payload.depth,
        breadth=payload.breadth,
        language=payload.language,
    )
    if not payload.session_id:
        raise HTTPException(status_code=400, detail="DeepResearch planning requires session_id")
    try:
        async with RAGClient(
            settings.RAG_SERVICE_URL,
            timeout=settings.REQUEST_TIMEOUT,
        ) as rag_client:
            items = await planner.plan_with_rag(
                topic=payload.topic,
                rag_client=rag_client,
                session_id=payload.session_id,
                user_id=user_id,
                top_k=payload.top_k,
                index_mode=payload.index_mode,
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM 规划失败: {str(exc)}") from exc
    return DeepResearchPlan(items=to_plan_item_payload(items))


@router.post(
    "/deep-research/plan/stream",
    summary="Stream DeepResearch plan preview",
)
async def stream_deep_research_plan_preview(
    payload: DeepResearchRequest,
    request: Request,
    user_id: int = Depends(get_user_id),
) -> StreamingResponse:
    """Stream planning progress and final plan via server-sent events."""

    payload = apply_deep_research_preset(payload)
    idle_messages = [
        "正在分析研究目标...",
        "正在拆解一级研究主题...",
        "正在组织子问题依赖关系...",
        "正在校验计划结构完整性...",
    ]

    async def event_generator() -> AsyncGenerator[str, None]:
        seq = 0

        def encode_sse(event_name: str, data: Any) -> str:
            nonlocal seq
            seq += 1
            payload_json = json.dumps(data, ensure_ascii=False)
            return f"id: {seq}\nevent: {event_name}\ndata: {payload_json}\n\n"

        yield encode_sse(
            "progress",
            _build_plan_stream_progress(
                "计划预览已开始",
                event_type="plan.started",
                payload={
                    "topic": payload.topic,
                    "depth": payload.depth,
                    "breadth": payload.breadth,
                },
            ),
        )

        try:
            override_items = extract_plan_override_items(
                payload.metadata,
                max_depth=payload.depth,
                max_breadth=payload.breadth,
            )
            if override_items:
                plan_payload = DeepResearchPlan(items=to_plan_item_payload(override_items))
                yield encode_sse(
                    "progress",
                    _build_plan_stream_progress(
                        "检测到编辑计划，直接使用自定义计划",
                        payload={
                            "items": len(plan_payload.items),
                            "source": "plan_override",
                        },
                    ),
                )
                yield encode_sse(
                    "progress",
                    _build_plan_stream_progress(
                        "计划预览完成",
                        event_type="plan.completed",
                        payload={
                            "items": len(plan_payload.items),
                            "source": "plan_override",
                        },
                    ),
                )
                yield encode_sse("plan", plan_payload.model_dump(mode="json"))
                yield "event: completion\ndata: [DONE]\n\n"
                return

            planner = PlannerAgent(
                depth=payload.depth,
                breadth=payload.breadth,
                language=payload.language,
            )
            yield encode_sse(
                "progress",
                _build_plan_stream_progress(
                    "规划器初始化完成，开始拆解主题",
                    payload={"strategy": "auto"},
                ),
            )

            retrieval_disabled = _is_retrieval_disabled(payload.index_mode)
            if not payload.session_id:
                yield encode_sse(
                    "progress",
                    _build_plan_stream_progress(
                        "计划预览失败: 缺少 session_id，无法执行 LLM 规划",
                        event_type="plan.failed",
                        payload={"reason": "missing_session_id"},
                    ),
                )
                yield "event: completion\ndata: [DONE]\n\n"
                return

            progress_queue: asyncio.Queue[tuple[str, Dict[str, Any]]] = asyncio.Queue()

            def observer(message: str, event_payload: Dict[str, Any]) -> None:
                try:
                    progress_queue.put_nowait((message, event_payload or {}))
                except Exception:
                    return

            yield encode_sse(
                "progress",
                _build_plan_stream_progress(
                    "会话检索已关闭，使用 LLM 规划（不检索知识库）"
                    if retrieval_disabled
                    else "正在连接 RAG 获取规划上下文",
                    payload={
                        "session_id": payload.session_id,
                        "strategy": "llm_only" if retrieval_disabled else "rag",
                    },
                ),
            )
            async with RAGClient(
                settings.RAG_SERVICE_URL,
                timeout=settings.REQUEST_TIMEOUT,
            ) as rag_client:
                plan_task = asyncio.create_task(
                    planner.plan_with_rag(
                        topic=payload.topic,
                        rag_client=rag_client,
                        session_id=payload.session_id,
                        user_id=user_id,
                        top_k=payload.top_k,
                        index_mode=payload.index_mode,
                        progress_observer=observer,
                    )
                )

                started_at = time.monotonic()
                idle_index = 0
                while not plan_task.done():
                    if await request.is_disconnected():
                        plan_task.cancel()
                        try:
                            await plan_task
                        except Exception:
                            pass
                        return

                    drained = False
                    while True:
                        try:
                            msg, info = progress_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        drained = True
                        yield encode_sse(
                            "progress",
                            _build_plan_stream_progress(msg, payload=info),
                        )

                    if not drained:
                        elapsed = round(time.monotonic() - started_at, 1)
                        hint = idle_messages[idle_index % len(idle_messages)]
                        idle_index += 1
                        yield encode_sse(
                            "progress",
                            _build_plan_stream_progress(
                                hint,
                                payload={"elapsed_seconds": elapsed, "waiting": "planner"},
                            ),
                        )

                    await asyncio.sleep(1.2)

                while True:
                    try:
                        msg, info = progress_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    yield encode_sse(
                        "progress",
                        _build_plan_stream_progress(msg, payload=info),
                    )

                try:
                    items = await plan_task
                    strategy = "llm_only" if retrieval_disabled else "rag"
                except Exception as exc:
                    yield encode_sse(
                        "progress",
                        _build_plan_stream_progress(
                            f"计划预览失败: {str(exc)}",
                            event_type="plan.failed",
                            payload={"error": str(exc), "strategy": "llm_only" if retrieval_disabled else "rag"},
                        ),
                    )
                    yield "event: completion\ndata: [DONE]\n\n"
                    return

            plan_payload = DeepResearchPlan(items=to_plan_item_payload(items))
            yield encode_sse(
                "progress",
                _build_plan_stream_progress(
                    "计划预览完成",
                    event_type="plan.completed",
                    payload={"strategy": strategy, "items": len(plan_payload.items)},
                ),
            )
            yield encode_sse("plan", plan_payload.model_dump(mode="json"))
            yield "event: completion\ndata: [DONE]\n\n"
        except Exception as exc:
            yield encode_sse(
                "progress",
                _build_plan_stream_progress(
                    f"计划预览失败: {str(exc)}",
                    event_type="plan.failed",
                    payload={"error": str(exc)},
                ),
            )
            yield "event: completion\ndata: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/deep-research", response_model=DeepResearchResponse, summary="Run DeepResearch")
async def run_deep_research(
    payload: DeepResearchRequest,
    user_id: int = Depends(get_user_id),
) -> DeepResearchResponse:
    """Run a DeepResearch session (planning → research → report)."""

    if not settings.ENABLE_SYNC_RUN:
        raise HTTPException(
            status_code=409,
            detail="Synchronous run disabled; use /deep-research/submit",
        )
    payload = apply_deep_research_preset(payload)
    pipeline = ResearchPipeline(
        rag_service_url=settings.RAG_SERVICE_URL,
        data_root=settings.DATA_ROOT,
        request_timeout=settings.REQUEST_TIMEOUT,
    )
    return await pipeline.run(payload, user_id=user_id)


@router.post(
    "/deep-research/submit",
    response_model=DeepResearchSubmitResponse,
    summary="Submit DeepResearch run",
)
async def submit_deep_research(
    payload: DeepResearchRequest,
    user_id: int = Depends(get_user_id),
) -> DeepResearchSubmitResponse:
    """Submit a DeepResearch run in the background."""

    payload = apply_deep_research_preset(payload)
    try:
        research_id, status, queue_position, active_runs, pending_runs = await run_manager.submit(
            payload, user_id=user_id
        )
    except ValueError as exc:
        detail = str(exc)
        if "Queue" in detail:
            raise HTTPException(status_code=429, detail=detail) from exc
        raise HTTPException(status_code=409, detail=detail) from exc
    return DeepResearchSubmitResponse(
        research_id=research_id,
        status=status,
        message="submitted",
        queue_position=queue_position,
        active_runs=active_runs,
        pending_runs=pending_runs,
    )


@router.post(
    "/deep-research/{research_id}/replay",
    response_model=DeepResearchSubmitResponse,
    summary="Replay DeepResearch run",
)
async def replay_deep_research(
    research_id: str,
    user_id: int = Depends(get_user_id),
) -> DeepResearchSubmitResponse:
    """Replay a DeepResearch session using archived request payload."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    request_payload = meta.get("request")
    if not isinstance(request_payload, dict):
        raise HTTPException(status_code=400, detail="Stored request payload missing")
    metadata = request_payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    request_payload = {
        **request_payload,
        "metadata": {**metadata, "replay_from": research_id},
    }
    try:
        payload = DeepResearchRequest(**request_payload)
    except Exception as exc:  # noqa: BLE001 - bubble invalid stored payloads
        raise HTTPException(status_code=400, detail="Invalid stored request payload") from exc
    try:
        replay_id, status, queue_position, active_runs, pending_runs = await run_manager.submit(
            payload, user_id=user_id
        )
    except ValueError as exc:
        detail = str(exc)
        if "Queue" in detail:
            raise HTTPException(status_code=429, detail=detail) from exc
        raise HTTPException(status_code=409, detail=detail) from exc
    return DeepResearchSubmitResponse(
        research_id=replay_id,
        status=status,
        message="replay_submitted",
        queue_position=queue_position,
        active_runs=active_runs,
        pending_runs=pending_runs,
    )


@router.post(
    "/deep-research/{research_id}/resume",
    response_model=DeepResearchSubmitResponse,
    summary="Resume DeepResearch run",
)
async def resume_deep_research(
    research_id: str,
    user_id: int = Depends(get_user_id),
) -> DeepResearchSubmitResponse:
    """Resume a failed or cancelled run with the same research id."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    status = meta.get("status")
    is_active = run_manager.is_active(research_id)
    if status == DeepResearchStatus.COMPLETED.value:
        raise HTTPException(status_code=409, detail="Research already completed")
    if status == DeepResearchStatus.RUNNING.value and is_active:
        raise HTTPException(status_code=409, detail="Research already running")
    if run_manager.is_active(research_id):
        raise HTTPException(status_code=409, detail="Research task already running")
    if not store.load_json("queue.json"):
        raise HTTPException(status_code=400, detail="Queue snapshot missing for resume")
    request_payload = meta.get("request")
    if not isinstance(request_payload, dict):
        raise HTTPException(status_code=400, detail="Stored request payload missing")
    try:
        payload = DeepResearchRequest(**request_payload)
    except Exception as exc:  # noqa: BLE001 - invalid stored payload
        raise HTTPException(status_code=400, detail="Invalid stored request payload") from exc
    try:
        _, status, queue_position, active_runs, pending_runs = await run_manager.submit(
            payload, user_id=user_id, research_id=research_id, resume=True
        )
    except ValueError as exc:
        detail = str(exc)
        if "Queue" in detail:
            raise HTTPException(status_code=429, detail=detail) from exc
        raise HTTPException(status_code=409, detail=detail) from exc
    return DeepResearchSubmitResponse(
        research_id=research_id,
        status=status,
        message="resumed",
        queue_position=queue_position,
        active_runs=active_runs,
        pending_runs=pending_runs,
    )


@router.post(
    "/deep-research/{research_id}/cancel",
    response_model=DeepResearchSubmitResponse,
    summary="Cancel DeepResearch run",
)
async def cancel_deep_research(
    research_id: str,
    user_id: int = Depends(get_user_id),
) -> DeepResearchSubmitResponse:
    """Cancel an in-flight DeepResearch run."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    now = datetime.utcnow().isoformat()
    cancel_state = await run_manager.cancel(research_id)
    if cancel_state == "cancel_requested":
        store.update_meta(
            {
                "cancel_requested_at": now,
                "cancel_reason": "user_cancel",
            }
        )
        return DeepResearchSubmitResponse(
            research_id=research_id,
            status=DeepResearchStatus.RUNNING,
            message="cancel_requested",
        )
    if cancel_state == "cancelled_queued" or (
        cancel_state == "not_found" and meta.get("status") == DeepResearchStatus.QUEUED.value
    ):
        store.update_meta(
            {
                "status": DeepResearchStatus.CANCELLED.value,
                "finished_at": now,
                "error": "cancelled",
            }
        )
        store.append_progress(
            {
                "research_id": research_id,
                "stage": "control",
                "event_type": "control.event",
                "message": "Run cancelled",
                "timestamp": now,
                "payload": {},
            }
        )
        return DeepResearchSubmitResponse(
            research_id=research_id,
            status=DeepResearchStatus.CANCELLED,
            message="cancelled",
        )
    if meta.get("status") in {
        DeepResearchStatus.QUEUED.value,
        DeepResearchStatus.RUNNING.value,
    }:
        store.update_meta(
            {
                "cancel_requested_at": now,
                "cancel_reason": "user_cancel",
            }
        )
        return DeepResearchSubmitResponse(
            research_id=research_id,
            status=DeepResearchStatus.RUNNING,
            message="cancel_requested",
        )
    return DeepResearchSubmitResponse(
        research_id=research_id,
        status=(
            DeepResearchStatus(meta["status"])
            if meta.get("status") in DeepResearchStatus._value2member_map_
            else DeepResearchStatus.COMPLETED
        ),
        message="already_finished",
    )


async def _build_queue_status(user_id: Optional[int] = None) -> DeepResearchQueueStatus:
    snapshot = await run_manager.get_queue_snapshot()

    def build_item(research_id: str) -> Optional[DeepResearchQueueItem]:
        store = StateStore(Path(settings.DATA_ROOT), research_id)
        meta = store.load_meta() or {}
        if user_id and str(meta.get("user_id")) != str(user_id):
            return None
        status_value = meta.get("status")
        status = (
            DeepResearchStatus(status_value)
            if status_value in DeepResearchStatus._value2member_map_
            else DeepResearchStatus.QUEUED
        )
        metrics = run_manager.compute_queue_metrics(meta)
        return DeepResearchQueueItem(
            research_id=research_id,
            topic=meta.get("topic") or "",
            status=status,
            priority=meta.get("priority"),
            effective_priority=metrics.get("effective_priority"),
            wait_seconds=metrics.get("wait_seconds"),
            submitted_at=meta.get("submitted_at"),
            started_at=meta.get("started_at"),
            user_id=meta.get("user_id"),
        )

    pending_items = []
    for run_id in snapshot["pending_ids"]:
        item = build_item(run_id)
        if item:
            pending_items.append(item)
    active_items = []
    for run_id in snapshot["active_ids"]:
        item = build_item(run_id)
        if item:
            active_items.append(item)
    return DeepResearchQueueStatus(
        active_runs=len(active_items),
        pending_runs=len(pending_items),
        max_active_runs=snapshot["max_active_runs"],
        active_items=active_items,
        pending_items=pending_items,
    )


@router.get(
    "/deep-research/queue",
    response_model=DeepResearchQueueStatus,
    summary="Fetch DeepResearch queue status",
)
async def get_deep_research_queue_status(
    user_id: int = Depends(get_user_id),
) -> DeepResearchQueueStatus:
    """Fetch queue status for DeepResearch runs."""

    return await _build_queue_status(user_id=user_id)


@router.get(
    "/deep-research/admin/queue",
    response_model=DeepResearchQueueStatus,
    summary="Admin queue status for DeepResearch",
)
async def admin_get_deep_research_queue_status(
    _identity: dict[str, Any] = Depends(get_internal_service_identity),
) -> DeepResearchQueueStatus:
    """Fetch global queue status for admin operations."""

    _ = _identity
    return await _build_queue_status(user_id=None)


@router.patch(
    "/deep-research/{research_id}/priority",
    response_model=DeepResearchSubmitResponse,
    summary="Update DeepResearch run priority",
)
async def update_deep_research_priority(
    research_id: str,
    payload: DeepResearchPriorityUpdateRequest,
    user_id: int = Depends(get_user_id),
) -> DeepResearchSubmitResponse:
    """Update the priority of a run."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    status = meta.get("status")
    if status == DeepResearchStatus.COMPLETED.value:
        raise HTTPException(status_code=409, detail="Cannot update completed run")
    result = await run_manager.update_priority(research_id, payload.priority)
    updated_status = (
        DeepResearchStatus(result["status"])
        if result.get("status") in DeepResearchStatus._value2member_map_
        else DeepResearchStatus.QUEUED
    )
    return DeepResearchSubmitResponse(
        research_id=research_id,
        status=updated_status,
        message="priority_updated",
        queue_position=result.get("queue_position"),
        active_runs=result.get("active_runs"),
        pending_runs=result.get("pending_runs"),
    )


@router.post("/idea-generation", response_model=IdeaGenerationResponse, summary="Generate research ideas")
async def run_idea_generation(
    payload: IdeaGenerationRequest,
    user_id: int = Depends(get_user_id),
) -> IdeaGenerationResponse:
    """Generate research ideas grounded in ScholarMind RAG."""

    pipeline = IdeaGenerationPipeline(
        rag_service_url=settings.RAG_SERVICE_URL,
        data_root=settings.DATA_ROOT,
        request_timeout=settings.REQUEST_TIMEOUT,
    )
    return await pipeline.run(payload, user_id=user_id)


@router.post("/notebook", response_model=NotebookNoteResponse, summary="Generate notebook note")
async def generate_notebook_note(
    payload: NotebookNoteRequest,
    user_id: int = Depends(get_user_id),
) -> NotebookNoteResponse:
    """Generate a structured notebook note from a selected excerpt."""

    pipeline = NotebookPipeline(
        rag_service_url=settings.RAG_SERVICE_URL,
        request_timeout=settings.REQUEST_TIMEOUT,
    )
    return await pipeline.run(payload, user_id=user_id)


@router.get(
    "/idea-generation/runs",
    response_model=IdeaGenerationRunList,
    summary="List idea generation runs",
)
async def list_idea_generation_runs(
    user_id: int = Depends(get_user_id),
) -> IdeaGenerationRunList:
    """List stored idea generation runs."""

    items = StateStore.list_runs_by_meta(Path(settings.DATA_ROOT), "idea_meta.json")
    if user_id:
        items = [item for item in items if str(item.get("user_id")) == str(user_id)]
    runs = [IdeaGenerationRunMeta(**item) for item in items]
    return IdeaGenerationRunList(items=runs)


@router.get(
    "/idea-generation/{idea_id}",
    response_model=IdeaGenerationRunDetail,
    summary="Fetch idea generation run detail",
)
async def get_idea_generation_run(
    idea_id: str,
    user_id: int = Depends(get_user_id),
) -> IdeaGenerationRunDetail:
    """Fetch stored idea generation detail payload."""

    store = StateStore(Path(settings.DATA_ROOT), idea_id)
    meta = store.load_json("idea_meta.json")
    if not meta:
        raise HTTPException(status_code=404, detail="Idea meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    payload = store.load_json("ideas.json") or {
        "idea_id": idea_id,
        "ideas_markdown": "",
        "citations": [],
        "trace": {},
    }
    return IdeaGenerationRunDetail(
        meta=IdeaGenerationRunMeta(**meta),
        payload=IdeaGenerationResponse(**payload),
    )


@router.get("/deep-research/{research_id}/snapshot", summary="Fetch stored research snapshot")
async def get_research_snapshot(
    research_id: str,
    compact: bool = Query(
        default=False,
        description="Return compact payload for high-frequency polling",
    ),
    user_id: int = Depends(get_user_id),
) -> Dict[str, Any]:
    """Fetch stored queue/citation/report data for a run."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    citations_payload = store.load_json("citations.json")
    report_payload = store.load_json("report.json")
    if compact:
        citations_payload = _compact_citations_payload(citations_payload)
        report_payload = _compact_report_payload(report_payload)
    return {
        "research_id": research_id,
        "meta": _normalize_run_meta_payload(meta),
        "outline": store.load_json("outline.json"),
        "queue": store.load_json("queue.json"),
        "citations": citations_payload,
        "report": report_payload,
    }


@router.get(
    "/deep-research/{research_id}/archive",
    response_model=DeepResearchArchive,
    summary="Fetch DeepResearch archive payload",
)
async def get_deep_research_archive(
    research_id: str,
    user_id: int = Depends(get_user_id),
) -> DeepResearchArchive:
    """Fetch a complete archive payload for a DeepResearch run."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    snapshot = {
        "research_id": research_id,
        "outline": store.load_json("outline.json"),
        "queue": store.load_json("queue.json"),
        "citations": store.load_json("citations.json"),
        "report": store.load_json("report.json"),
    }
    summary = meta.get("summary") or (snapshot.get("report") or {}).get("summary") or {}
    return DeepResearchArchive(
        research_id=research_id,
        meta=meta,
        snapshot=snapshot,
        progress=store.load_progress(),
        summary=summary,
    )


@router.get(
    "/deep-research/{research_id}/blocks/{block_id}/evidence",
    response_model=BlockEvidence,
    summary="Fetch evidence for a topic block",
)
async def get_deep_research_block_evidence(
    research_id: str,
    block_id: str,
    user_id: int = Depends(get_user_id),
) -> BlockEvidence:
    """Fetch evidence payload for a specific block."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    queue = store.load_json("queue.json") or {}
    blocks = queue.get("blocks", [])
    block = next((item for item in blocks if item.get("block_id") == block_id), None)
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")

    citations_payload = store.load_json("citations.json") or {}
    citation_map = {
        item.get("citation_id"): item
        for item in citations_payload.get("citations", [])
        if item.get("citation_id")
    }
    citation_ids = block.get("citations", []) or []
    citation_details = [
        citation_map.get(citation_id, {"citation_id": citation_id}) for citation_id in citation_ids
    ]

    progress_events = [
        event
        for event in store.load_progress()
        if (event.get("payload") or {}).get("block_id") == block_id
    ]
    return BlockEvidence(
        research_id=research_id,
        block_id=block_id,
        block=block,
        notes=block.get("notes", []) or [],
        citations=citation_ids,
        citation_details=citation_details,
        tool_traces=block.get("tool_traces", []) or [],
        decisions=block.get("decisions", []) or [],
        progress_events=progress_events,
    )


@router.get(
    "/deep-research/{research_id}/progress",
    response_model=DeepResearchProgressResponse,
    summary="Fetch research progress events",
)
async def get_research_progress(
    research_id: str,
    tail: Optional[int] = Query(default=None, ge=1, le=2000),
    user_id: int = Depends(get_user_id),
) -> DeepResearchProgressResponse:
    """Fetch progress events for a run."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    items = store.load_progress_tail(tail) if tail is not None else store.load_progress()
    return DeepResearchProgressResponse(
        research_id=research_id,
        items=items,
        next_offset=store.get_progress_offset(),
    )


@router.get(
    "/deep-research/{research_id}/progress/since",
    response_model=DeepResearchProgressResponse,
    summary="Fetch progress events since offset",
)
async def get_research_progress_since(
    research_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=2000),
    user_id: int = Depends(get_user_id),
) -> DeepResearchProgressResponse:
    """Fetch progress events from a file offset."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    events, next_offset = store.read_progress_since(offset, limit=limit)
    items = [event for event, _ in events]
    return DeepResearchProgressResponse(
        research_id=research_id,
        items=items,
        next_offset=next_offset,
    )


@router.get(
    "/deep-research/session/{session_id}/runs",
    response_model=DeepResearchRunList,
    summary="List DeepResearch runs by session",
)
async def list_deep_research_runs_by_session(
    session_id: str,
    limit: int = Query(default=40, ge=1, le=300),
    user_id: int = Depends(get_user_id),
) -> DeepResearchRunList:
    """List DeepResearch runs that belong to a specific chat session."""

    items = StateStore.list_runs_by_session(
        Path(settings.DATA_ROOT),
        session_id=session_id,
        user_id=user_id,
        limit=limit,
    )
    normalized_items = [_normalize_run_meta_payload(item) for item in items]
    return DeepResearchRunList(items=normalized_items)


@router.get(
    "/deep-research/session/{session_id}/context",
    response_model=DeepResearchSessionContextResponse,
    summary="Fetch session-level DeepResearch summary context",
)
async def get_deep_research_session_context(
    session_id: str,
    limit: int = Query(default=2, ge=1, le=10),
    max_summary_chars: int = Query(default=800, ge=200, le=4000),
    user_id: int = Depends(get_user_id),
) -> DeepResearchSessionContextResponse:
    """Fetch concise report summaries for context reuse in chat."""

    scan_limit = max(limit * 6, limit)
    run_items = StateStore.list_runs_by_session(
        Path(settings.DATA_ROOT),
        session_id=session_id,
        user_id=user_id,
        limit=scan_limit,
    )
    context_items: list[DeepResearchSessionContextItem] = []
    for item in run_items:
        status_value = str(item.get("status") or "").strip().lower()
        if status_value != DeepResearchStatus.COMPLETED.value:
            continue
        research_id = str(item.get("research_id") or "").strip()
        if not research_id:
            continue
        store = StateStore(Path(settings.DATA_ROOT), research_id)
        report_payload = store.load_json("report.json") or {}
        report_markdown = report_payload.get("report_markdown")
        summary_text = _extract_report_summary(
            report_markdown if isinstance(report_markdown, str) else "",
            max_chars=max_summary_chars,
        )
        topic_text = (
            str(item.get("topic") or "").strip()
            or str((item.get("request") or {}).get("topic") or "").strip()
            or "DeepResearch"
        )
        if not summary_text:
            summary_text = topic_text
        citations_payload = store.load_json("citations.json") or {}
        citations = citations_payload.get("citations", []) if isinstance(citations_payload, dict) else []
        status_member = (
            DeepResearchStatus(status_value)
            if status_value in DeepResearchStatus._value2member_map_
            else DeepResearchStatus.COMPLETED
        )
        context_items.append(
            DeepResearchSessionContextItem(
                research_id=research_id,
                topic=topic_text,
                status=status_member,
                submitted_at=item.get("submitted_at"),
                finished_at=item.get("finished_at"),
                citations_total=len(citations or []),
                summary=summary_text,
            )
        )
        if len(context_items) >= limit:
            break
    return DeepResearchSessionContextResponse(session_id=session_id, items=context_items)


@router.get("/deep-research/runs", response_model=DeepResearchRunList, summary="List DeepResearch runs")
async def list_deep_research_runs(
    user_id: int = Depends(get_user_id),
) -> DeepResearchRunList:
    """List stored DeepResearch runs."""

    items = StateStore.list_runs(Path(settings.DATA_ROOT))
    if user_id:
        items = [item for item in items if str(item.get("user_id")) == str(user_id)]
    return DeepResearchRunList(items=items)


@router.get("/deep-research/admin/runs", summary="Admin list DeepResearch runs")
async def admin_list_deep_research_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    _identity: dict[str, Any] = Depends(get_internal_service_identity),
) -> Dict[str, Any]:
    """List global DeepResearch runs for admin operations."""

    _ = _identity
    items = StateStore.list_runs(Path(settings.DATA_ROOT))
    if status:
        normalized_status = status.strip().lower()
        items = [
            item
            for item in items
            if str(item.get("status", "")).strip().lower() == normalized_status
        ]
    if user_id is not None:
        items = [item for item in items if str(item.get("user_id")) == str(user_id)]
    items.sort(key=_extract_timestamp, reverse=True)
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": items[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/deep-research/admin/{research_id}/cancel", summary="Admin cancel DeepResearch run")
async def admin_cancel_deep_research(
    research_id: str,
    payload: Optional[AdminActionRequest] = None,
    _identity: dict[str, Any] = Depends(get_internal_service_identity),
) -> DeepResearchSubmitResponse:
    """Cancel any DeepResearch run from admin control plane."""

    _ = _identity
    cancel_reason = (payload.reason.strip() if payload and payload.reason else "") or "admin_cancel"
    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    now = datetime.utcnow().isoformat()
    cancel_state = await run_manager.cancel(research_id)
    if cancel_state == "cancelled_queued" or (
        cancel_state == "not_found" and meta.get("status") == DeepResearchStatus.QUEUED.value
    ):
        store.update_meta(
            {
                "status": DeepResearchStatus.CANCELLED.value,
                "finished_at": now,
                "cancel_reason": cancel_reason,
                "error": "cancelled",
            }
        )
        store.append_progress(
            {
                "research_id": research_id,
                "stage": "control",
                "event_type": "control.event",
                "message": f"Run cancelled by admin: {cancel_reason}",
                "timestamp": now,
                "payload": {},
            }
        )
        return DeepResearchSubmitResponse(
            research_id=research_id,
            status=DeepResearchStatus.CANCELLED,
            message="cancelled",
        )
    if meta.get("status") in {
        DeepResearchStatus.QUEUED.value,
        DeepResearchStatus.RUNNING.value,
    }:
        store.update_meta(
            {
                "cancel_requested_at": now,
                "cancel_reason": cancel_reason,
            }
        )
        return DeepResearchSubmitResponse(
            research_id=research_id,
            status=DeepResearchStatus.RUNNING,
            message="cancel_requested",
        )
    return DeepResearchSubmitResponse(
        research_id=research_id,
        status=(
            DeepResearchStatus(meta["status"])
            if meta.get("status") in DeepResearchStatus._value2member_map_
            else DeepResearchStatus.COMPLETED
        ),
        message="already_finished",
    )


@router.post("/deep-research/admin/{research_id}/retry", summary="Admin retry DeepResearch run")
async def admin_retry_deep_research(
    research_id: str,
    _identity: dict[str, Any] = Depends(get_internal_service_identity),
) -> Dict[str, Any]:
    """Retry any DeepResearch run from admin control plane."""

    _ = _identity
    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    source_user_id = int(meta.get("user_id") or 0)
    if source_user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid run owner for retry")
    request_payload = meta.get("request")
    if not isinstance(request_payload, dict):
        raise HTTPException(status_code=400, detail="Stored request payload missing")
    metadata = request_payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    request_payload = {
        **request_payload,
        "metadata": {**metadata, "replay_from": research_id, "replay_source": "admin"},
    }
    try:
        payload = DeepResearchRequest(**request_payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid stored request payload") from exc
    try:
        replay_id, replay_status, queue_position, active_runs, pending_runs = await run_manager.submit(
            payload,
            user_id=source_user_id,
        )
    except ValueError as exc:
        detail = str(exc)
        if "Queue" in detail:
            raise HTTPException(status_code=429, detail=detail) from exc
        raise HTTPException(status_code=409, detail=detail) from exc
    return {
        "source_research_id": research_id,
        "retry_research_id": replay_id,
        "status": replay_status.value if hasattr(replay_status, "value") else str(replay_status),
        "queue_position": queue_position,
        "active_runs": active_runs,
        "pending_runs": pending_runs,
    }


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@router.get("/deep-research/admin/metrics", summary="Admin DeepResearch metrics")
async def admin_deep_research_metrics(
    window_hours: int = Query(24, ge=1, le=24 * 30),
    _identity: dict[str, Any] = Depends(get_internal_service_identity),
) -> Dict[str, Any]:
    """Aggregate global DeepResearch metrics for admin dashboards."""

    _ = _identity
    items = StateStore.list_runs(Path(settings.DATA_ROOT))
    queue_status = await _build_queue_status(user_id=None)
    status_counts: Dict[str, int] = {}
    token_prompt = 0
    token_completion = 0
    token_total = 0
    estimated_cost = 0.0
    by_model: Dict[str, Dict[str, float]] = {}
    now = datetime.utcnow()
    window_runs = 0
    for item in items:
        status_key = str(item.get("status") or "unknown").strip().lower() or "unknown"
        status_counts[status_key] = status_counts.get(status_key, 0) + 1
        submitted_at = _parse_iso_datetime(
            str(item.get("submitted_at") or item.get("started_at") or "")
        )
        if submitted_at and (now - submitted_at).total_seconds() <= window_hours * 3600:
            window_runs += 1
        usage = item.get("token_usage") or {}
        if isinstance(usage, dict):
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or 0)
            cost_value = float(usage.get("estimated_cost_usd") or 0.0)
            token_prompt += prompt_tokens
            token_completion += completion_tokens
            token_total += total_tokens
            estimated_cost += cost_value
            model_name = str(usage.get("model_name") or usage.get("model") or "").strip()
            if model_name:
                model_entry = by_model.setdefault(
                    model_name,
                    {
                        "runs": 0,
                        "prompt_tokens": 0.0,
                        "completion_tokens": 0.0,
                        "total_tokens": 0.0,
                        "estimated_cost_usd": 0.0,
                    },
                )
                model_entry["runs"] += 1
                model_entry["prompt_tokens"] += prompt_tokens
                model_entry["completion_tokens"] += completion_tokens
                model_entry["total_tokens"] += total_tokens
                model_entry["estimated_cost_usd"] += cost_value
    return {
        "available": True,
        "runs_total": len(items),
        "runs_last_window": window_runs,
        "window_hours": window_hours,
        "runs_by_status": status_counts,
        "queue": {
            "active_runs": queue_status.active_runs,
            "pending_runs": queue_status.pending_runs,
            "max_active_runs": queue_status.max_active_runs,
        },
        "token_usage": {
            "prompt_tokens": token_prompt,
            "completion_tokens": token_completion,
            "total_tokens": token_total,
            "estimated_cost_usd": round(estimated_cost, 6),
            "by_model": by_model,
        },
    }


def _load_compare_side(research_id: str, user_id: int) -> DeepResearchCompareSide:
    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    report = store.load_json("report.json") or {}
    summary = report.get("summary") or {}
    if summary and "errors_total" not in summary:
        summary["errors_total"] = len(summary.get("errors", []) or [])
    if not summary:
        queue = store.load_json("queue.json") or {}
        blocks = queue.get("blocks", []) or []
        status_counts: Dict[str, int] = {}
        tool_traces_total = 0
        decisions_total = 0
        for block in blocks:
            status = block.get("status")
            if status:
                status_counts[status] = status_counts.get(status, 0) + 1
            tool_traces_total += len(block.get("tool_traces", []) or [])
            decisions_total += len(block.get("decisions", []) or [])
        citations = store.load_json("citations.json") or {}
        summary = {
            "blocks_total": len(blocks),
            "blocks_by_status": status_counts,
            "citations_total": len(citations.get("citations", []) or []),
            "tool_traces_total": tool_traces_total,
            "decisions_total": decisions_total,
            "tool_traces_by_type": {},
            "errors": [],
            "errors_total": 0,
        }
    duration = meta.get("duration_seconds")
    if duration is None and meta.get("started_at") and meta.get("finished_at"):
        try:
            start = datetime.fromisoformat(meta["started_at"])
            end = datetime.fromisoformat(meta["finished_at"])
            duration = (end - start).total_seconds()
        except ValueError:
            duration = None
    status_value = meta.get("status")
    status = (
        DeepResearchStatus(status_value)
        if status_value in DeepResearchStatus._value2member_map_
        else None
    )
    return DeepResearchCompareSide(
        research_id=research_id,
        status=status,
        topic=meta.get("topic"),
        started_at=meta.get("started_at"),
        finished_at=meta.get("finished_at"),
        duration_seconds=duration,
        error=meta.get("error"),
        summary=summary,
    )


def _diff_numeric(left_value: Any, right_value: Any) -> Dict[str, Optional[float]]:
    try:
        left_num = float(left_value)
        right_num = float(right_value)
    except (TypeError, ValueError):
        return {"left": left_value, "right": right_value, "delta": None}
    return {
        "left": left_num,
        "right": right_num,
        "delta": right_num - left_num,
    }


def _diff_mapping(
    left_map: Dict[str, Any], right_map: Dict[str, Any]
) -> Dict[str, Dict[str, Optional[float]]]:
    keys = set(left_map) | set(right_map)
    diff: Dict[str, Dict[str, Optional[float]]] = {}
    for key in keys:
        left_value = left_map.get(key, 0)
        right_value = right_map.get(key, 0)
        diff[key] = _diff_numeric(left_value, right_value)
    return diff


def _extract_top_status_changes(
    status_diff: Dict[str, Dict[str, Optional[float]]], top_n: int = 5
) -> list[Dict[str, Optional[float]]]:
    items: list[Dict[str, Optional[float]]] = []
    for status, entry in status_diff.items():
        delta = entry.get("delta")
        if delta is None:
            continue
        items.append(
            {
                "status": status,
                "left": entry.get("left"),
                "right": entry.get("right"),
                "delta": delta,
            }
        )
    items.sort(key=lambda item: abs(item.get("delta") or 0), reverse=True)
    return items[:top_n]


def _build_compare_response(
    left_id: str, right_id: str, user_id: int
) -> DeepResearchCompareResponse:
    left = _load_compare_side(left_id, user_id)
    right = _load_compare_side(right_id, user_id)

    blocks_by_status_diff = _diff_mapping(
        left.summary.get("blocks_by_status", {}),
        right.summary.get("blocks_by_status", {}),
    )
    tool_traces_by_type_diff = _diff_mapping(
        left.summary.get("tool_traces_by_type", {}),
        right.summary.get("tool_traces_by_type", {}),
    )

    diff = {
        "duration_seconds": _diff_numeric(left.duration_seconds, right.duration_seconds),
        "blocks_total": _diff_numeric(
            left.summary.get("blocks_total"), right.summary.get("blocks_total")
        ),
        "citations_total": _diff_numeric(
            left.summary.get("citations_total"), right.summary.get("citations_total")
        ),
        "tool_traces_total": _diff_numeric(
            left.summary.get("tool_traces_total"), right.summary.get("tool_traces_total")
        ),
        "decisions_total": _diff_numeric(
            left.summary.get("decisions_total"), right.summary.get("decisions_total")
        ),
        "errors_total": _diff_numeric(
            left.summary.get("errors_total"), right.summary.get("errors_total")
        ),
        "blocks_by_status": blocks_by_status_diff,
        "tool_traces_by_type": tool_traces_by_type_diff,
        "blocks_by_status_top": _extract_top_status_changes(blocks_by_status_diff),
        "top_tools": {
            "left": _extract_top_tools(left.summary),
            "right": _extract_top_tools(right.summary),
        },
        "top_errors": {
            "left": _extract_top_errors(left.summary),
            "right": _extract_top_errors(right.summary),
        },
        "status": {"left": left.status, "right": right.status},
    }
    return DeepResearchCompareResponse(left=left, right=right, diff=diff)


def _extract_top_tools(summary: Dict[str, Any], top_n: int = 5) -> Dict[str, int]:
    tool_counts = summary.get("tool_traces_by_type", {}) or {}
    sorted_items = sorted(tool_counts.items(), key=lambda item: item[1], reverse=True)
    return dict(sorted_items[:top_n])


def _extract_top_errors(summary: Dict[str, Any], top_n: int = 5) -> list[Dict[str, Any]]:
    errors = summary.get("errors", []) or []
    return errors[:top_n]


@router.post(
    "/deep-research/compare",
    response_model=DeepResearchCompareResponse,
    summary="Compare DeepResearch runs",
)
async def compare_deep_research_runs(
    payload: DeepResearchCompareRequest,
    user_id: int = Depends(get_user_id),
) -> DeepResearchCompareResponse:
    """Compare two DeepResearch runs by metadata and summary."""

    return _build_compare_response(payload.left_id, payload.right_id, user_id)


@router.get(
    "/deep-research/compare/export",
    summary="Export DeepResearch run comparison",
)
async def export_compare_runs(
    left_id: str,
    right_id: str,
    format: str = Query(default="markdown"),
    user_id: int = Depends(get_user_id),
) -> Response:
    """Export run comparison in markdown, HTML, or PDF."""

    payload = _build_compare_response(left_id, right_id, user_id)
    markdown_text = render_compare_markdown(payload)
    export_format = (format or "").strip().lower()
    if export_format in {"markdown", "md"}:
        filename = f"{left_id}_vs_{right_id}.md"
        return Response(
            content=markdown_text,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if export_format == "json":
        filename = f"{left_id}_vs_{right_id}.json"
        return JSONResponse(
            content=payload.model_dump(mode="json"),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if export_format in {"html", "htm"}:
        html_text = render_html(markdown_text, title="DeepResearch Comparison")
        filename = f"{left_id}_vs_{right_id}.html"
        return Response(
            content=html_text,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if export_format == "pdf":
        try:
            pdf_bytes = render_pdf(markdown_text, title="DeepResearch Comparison")
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        filename = f"{left_id}_vs_{right_id}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    raise HTTPException(status_code=400, detail="Unsupported export format")


@router.get(
    "/deep-research/{research_id}",
    response_model=DeepResearchRunMeta,
    summary="Fetch DeepResearch run metadata",
)
async def get_deep_research_meta(
    research_id: str,
    user_id: int = Depends(get_user_id),
) -> DeepResearchRunMeta:
    """Fetch metadata for a DeepResearch run."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    return DeepResearchRunMeta(**_normalize_run_meta_payload(meta))


@router.get(
    "/deep-research/{research_id}/progress/stream",
    summary="Stream research progress events",
)
async def stream_research_progress(
    research_id: str,
    request: Request,
    once: bool = Query(default=False, description="Return one streaming batch then close."),
    user_id: int = Depends(get_user_id_for_stream),
) -> StreamingResponse:
    """Stream progress events via server-sent events."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    last_event_id = request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id")
    try:
        initial_offset = int(last_event_id) if last_event_id else 0
    except ValueError:
        initial_offset = 0

    async def event_generator() -> AsyncGenerator[str, None]:
        offset = initial_offset
        last_heartbeat = time.monotonic()
        # Emit an immediate SSE comment to flush headers and avoid client-side
        # hangs before the first progress event arrives.
        yield ": connected\n\n"
        while True:
            events, offset = store.read_progress_since(offset)
            for event, event_offset in events:
                payload = json.dumps(event, ensure_ascii=False)
                yield f"id: {event_offset}\nevent: progress\ndata: {payload}\n\n"
            now = time.monotonic()
            if now - last_heartbeat > 15:
                last_heartbeat = now
                yield "event: heartbeat\ndata: {}\n\n"
            if once:
                break
            if await request.is_disconnected():
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get(
    "/deep-research/{research_id}/export",
    summary="Export DeepResearch report",
)
async def export_deep_research_report(
    research_id: str,
    format: str = Query(default="markdown"),
    user_id: int = Depends(get_user_id),
) -> Response:
    """Export a report in markdown, HTML, or PDF."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    report = store.load_json("report.json") or {}
    markdown_text = report.get("report_markdown")
    if not markdown_text:
        raise HTTPException(status_code=404, detail="Report not found")

    export_format = (format or "").strip().lower()
    title = meta.get("topic") or "DeepResearch Report"
    if export_format in {"markdown", "md"}:
        filename = f"{research_id}.md"
        return Response(
            content=markdown_text,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if export_format in {"html", "htm"}:
        html_text = render_html(markdown_text, title=title)
        filename = f"{research_id}.html"
        return Response(
            content=html_text,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if export_format == "pdf":
        try:
            pdf_bytes = render_pdf(markdown_text, title=title)
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        filename = f"{research_id}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    raise HTTPException(status_code=400, detail="Unsupported export format")


@router.get(
    "/deep-research/{research_id}/archive/export",
    summary="Export DeepResearch run archive",
)
async def export_deep_research_archive(
    research_id: str,
    format: str = Query(default="zip"),
    user_id: int = Depends(get_user_id),
) -> Response:
    """Export run archive as zip."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    export_format = (format or "").strip().lower()
    if export_format not in {"zip"}:
        raise HTTPException(status_code=400, detail="Unsupported export format")

    archive_files = [
        "meta.json",
        "report.json",
        "citations.json",
        "queue.json",
        "outline.json",
        "progress.jsonl",
    ]
    file_entries = []
    for name in archive_files:
        path = store.root / name
        file_entries.append(
            {
                "name": name,
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else None,
                "sha256": compute_file_sha256(path) if path.exists() else None,
            }
        )
    manifest = {
        "research_id": research_id,
        "topic": meta.get("topic"),
        "status": meta.get("status"),
        "exported_at": datetime.utcnow().isoformat(),
        "files": file_entries,
    }
    archive_bytes = build_run_archive_zip(
        store.root,
        archive_files,
        manifest=manifest,
    )
    filename = f"{research_id}_archive.zip"
    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/deep-research/{research_id}/evidence/{block_id}/export",
    summary="Export block evidence",
)
async def export_block_evidence(
    research_id: str,
    block_id: str,
    format: str = Query(default="zip"),
    user_id: int = Depends(get_user_id),
) -> Response:
    """Export evidence for a specific block as zip."""

    store = StateStore(Path(settings.DATA_ROOT), research_id)
    meta = store.load_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Research meta not found")
    if user_id and str(meta.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    queue = store.load_json("queue.json") or {}
    blocks = queue.get("blocks", [])
    block = next((item for item in blocks if item.get("block_id") == block_id), None)
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")

    citations_payload = store.load_json("citations.json") or {}
    citation_map = {
        item.get("citation_id"): item
        for item in citations_payload.get("citations", [])
        if item.get("citation_id")
    }
    citation_ids = block.get("citations", []) or []
    citation_details = [
        citation_map.get(citation_id, {"citation_id": citation_id}) for citation_id in citation_ids
    ]

    progress_events = [
        event
        for event in store.load_progress()
        if (event.get("payload") or {}).get("block_id") == block_id
    ]
    evidence = BlockEvidence(
        research_id=research_id,
        block_id=block_id,
        block=block,
        notes=block.get("notes", []) or [],
        citations=citation_ids,
        citation_details=citation_details,
        tool_traces=block.get("tool_traces", []) or [],
        decisions=block.get("decisions", []) or [],
        progress_events=progress_events,
    )

    export_format = (format or "").strip().lower()
    if export_format not in {"zip"}:
        raise HTTPException(status_code=400, detail="Unsupported export format")
    highlight_events = [
        {
            "timestamp": event.get("timestamp"),
            "stage": event.get("stage"),
            "message": event.get("message"),
        }
        for event in progress_events[-5:]
    ]
    manifest = {
        "research_id": research_id,
        "block_id": block_id,
        "exported_at": datetime.utcnow().isoformat(),
        "highlights": highlight_events,
        "counts": {
            "notes": len(block.get("notes", []) or []),
            "citations": len(citation_ids),
            "tool_traces": len(block.get("tool_traces", []) or []),
            "decisions": len(block.get("decisions", []) or []),
            "progress_events": len(progress_events),
        },
    }
    archive_bytes = build_block_evidence_zip(
        evidence.model_dump(mode="json"),
        manifest=manifest,
    )
    filename = f"{research_id}_{block_id}_evidence.zip"
    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
