"""DeepResearch API endpoints."""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from core.config import settings
from schemas.common import (
    BlockEvidence,
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

router = APIRouter()
run_manager = RunManager(
    rag_service_url=settings.RAG_SERVICE_URL,
    data_root=settings.DATA_ROOT,
    request_timeout=settings.REQUEST_TIMEOUT,
)


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

    planner = PlannerAgent(
        depth=payload.depth,
        breadth=payload.breadth,
        language=payload.language,
    )
    if not payload.session_id:
        items = planner.plan(payload.topic)
    else:
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
        except Exception:
            items = planner.plan(payload.topic)
    return DeepResearchPlan(
        items=[
            {
                "title": item.title,
                "question": item.question,
                "depth": item.depth,
                "parent_title": item.parent_title,
            }
            for item in items
        ]
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


@router.get(
    "/deep-research/queue",
    response_model=DeepResearchQueueStatus,
    summary="Fetch DeepResearch queue status",
)
async def get_deep_research_queue_status(
    user_id: int = Depends(get_user_id),
) -> DeepResearchQueueStatus:
    """Fetch queue status for DeepResearch runs."""

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
    user_id: int = Depends(get_user_id),
) -> Dict[str, Any]:
    """Fetch stored queue/citation/report data for a run."""

    _ = user_id  # kept for future authorization checks
    store = StateStore(Path(settings.DATA_ROOT), research_id)
    return {
        "research_id": research_id,
        "outline": store.load_json("outline.json"),
        "queue": store.load_json("queue.json"),
        "citations": store.load_json("citations.json"),
        "report": store.load_json("report.json"),
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


@router.get("/deep-research/runs", response_model=DeepResearchRunList, summary="List DeepResearch runs")
async def list_deep_research_runs(
    user_id: int = Depends(get_user_id),
) -> DeepResearchRunList:
    """List stored DeepResearch runs."""

    items = StateStore.list_runs(Path(settings.DATA_ROOT))
    if user_id:
        items = [item for item in items if str(item.get("user_id")) == str(user_id)]
    return DeepResearchRunList(items=items)


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
    return DeepResearchRunMeta(**meta)


@router.get(
    "/deep-research/{research_id}/progress/stream",
    summary="Stream research progress events",
)
async def stream_research_progress(
    research_id: str,
    request: Request,
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
        while True:
            if await request.is_disconnected():
                break
            events, offset = store.read_progress_since(offset)
            for event, event_offset in events:
                payload = json.dumps(event, ensure_ascii=False)
                yield f"id: {event_offset}\nevent: progress\ndata: {payload}\n\n"
            now = time.monotonic()
            if now - last_heartbeat > 15:
                last_heartbeat = now
                yield "event: heartbeat\ndata: {}\n\n"
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
