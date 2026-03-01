from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from core.config import settings
from models.admin_audit_log import AdminAuditLog
from models.demo_access_log import DemoAccessLog
from models.document import Document
from models.job import Job, JobStatus
from models.knowledgebase import KnowledgeBase
from models.session import Session as SessionModel
from models.user import User
from schemas.document import DocumentParsePreviewResponse
from schemas.job import JobInDB
from service.auth import (
    AdminConsolePrincipal,
    create_admin_console_token,
    create_internal_service_token,
    get_current_admin_console_user,
    get_user_role,
    verify_admin_console_credentials,
)
from service.core.conversation.ask_stream_replay_buffer import (
    get_ask_stream_replay_buffer,
)
from service.core.ingestion.parse_preview_service import ParsePreviewService
from service.core.system.runtime_metrics import runtime_metrics
from service.job_handler.local_upload_handler import LocalUploadHandler
from service.job_handler.online_ingestion_handler import OnlineIngestionHandler
from service.job_handler.parse_index_handler import ParseIndexHandler
from service.job_runner_service import execute_job
from service.job_service import job_service
from utils.database import get_db

router = APIRouter(prefix="/admin", tags=["Admin"])
_service_started_at = time.time()
_logger = logging.getLogger(__name__)
_RETRYABLE_JOB_STATUS = {
    JobStatus.FAILED.value,
    JobStatus.PARTIAL.value,
    JobStatus.CANCELLED.value,
}


class UpdateUserRoleRequest(BaseModel):
    role: str


class UpdateUserStatusRequest(BaseModel):
    is_active: bool
    reason: Optional[str] = None


class JobActionRequest(BaseModel):
    reason: Optional[str] = None


class AdminLoginRequest(BaseModel):
    username: str
    password: str


def _admin_name(current_admin: User | AdminConsolePrincipal) -> str:
    return str(getattr(current_admin, "username", "") or "admin-console")


def _admin_actor_id(current_admin: User | AdminConsolePrincipal) -> Optional[int]:
    raw_id = getattr(current_admin, "id", None)
    try:
        value = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        value = None
    if value is None or value <= 0:
        return None
    return value


def _serialize_user(user: User) -> dict:
    role = get_user_role(user)
    return {
        "id": user.id,
        "username": user.username,
        "role": role,
        "is_admin": role in {"admin", "super_admin"},
        "is_super_admin": role == "super_admin",
        "is_active": bool(getattr(user, "is_active", True)),
    }


def _append_audit_log(
    db: Session,
    *,
    admin_user_id: Optional[int],
    action: str,
    target_type: str,
    target_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    db.add(
        AdminAuditLog(
            admin_user_id=admin_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail_json=detail or {},
        )
    )


def _resolve_job_handler(job_type: str):
    normalized = (job_type or "").strip().lower()
    if normalized == "upload_local":
        return LocalUploadHandler
    if normalized == "ingest_online":
        return OnlineIngestionHandler
    if normalized == "parse_index":
        return ParseIndexHandler
    return None


def _build_job_ops_metrics(db: Session) -> dict:
    rows = db.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    status_map = {(status or "unknown").strip().lower(): count for status, count in rows}
    pending = int(status_map.get("pending", 0))
    running = int(status_map.get("running", 0))
    success = int(status_map.get("success", 0))
    failed = int(status_map.get("failed", 0))
    partial = int(status_map.get("partial", 0))
    cancelled = int(status_map.get("cancelled", 0))
    terminal_total = success + failed + partial + cancelled
    success_rate = (success / terminal_total) if terminal_total > 0 else None
    failure_rate = (failed / terminal_total) if terminal_total > 0 else None
    return {
        "queue_backlog": pending + running,
        "pending_jobs": pending,
        "running_jobs": running,
        "terminal_jobs": terminal_total,
        "success_rate": round(success_rate, 6) if success_rate is not None else None,
        "failure_rate": round(failure_rate, 6) if failure_rate is not None else None,
    }


def _deep_research_admin_request(
    *,
    method: str,
    path: str,
    current_user: User | AdminConsolePrincipal,
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
) -> dict:
    base_url = (settings.DEEP_RESEARCH_SERVICE_URL or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DeepResearch service url is not configured",
        )
    token = create_internal_service_token(
        service_name="scholarmind_api",
        acting_user_id=getattr(current_user, "id", None),
    )
    url = f"{base_url}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-User-Id": str(getattr(current_user, "id", 0) or 0),
    }
    normalized_params = {
        key: value
        for key, value in (params or {}).items()
        if value is not None and value != ""
    }
    normalized_json = json_body if isinstance(json_body, dict) else {}
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.request(
                method=method.upper(),
                url=url,
                params=normalized_params,
                json=normalized_json,
                headers=headers,
            )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("DeepResearch admin request failed: %s %s %s", method, path, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="DeepResearch service unavailable",
        ) from exc
    if response.status_code >= 400:
        try:
            payload = response.json()
            detail = payload.get("detail") or payload
        except ValueError:
            detail = response.text
        passthrough = {400, 401, 403, 404, 409, 429}
        if response.status_code in passthrough:
            raise HTTPException(status_code=response.status_code, detail=detail)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "DeepResearch admin api error",
                "upstream_status": response.status_code,
                "upstream_detail": detail,
            },
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="DeepResearch admin api returned non-json payload",
        ) from exc
    if isinstance(payload, dict):
        return payload
    return {"data": payload}


@router.post("/auth/login", summary="后台管理员登录")
def admin_login(payload: AdminLoginRequest) -> dict:
    username = (payload.username or "").strip()
    password = payload.password or ""
    if not verify_admin_console_credentials(username, password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )
    access_token = create_admin_console_token(username=username)
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", summary="当前用户后台权限")
def get_admin_me(
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
) -> dict:
    """Return current admin console identity for route-guard."""
    return {
        "user_id": 0,
        "username": current_user.username,
        "role": "super_admin",
        "is_admin": True,
        "is_super_admin": True,
    }


@router.get("/overview", summary="管理后台总览")
def get_admin_overview(
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
) -> dict:
    """Return MVP dashboard metrics for admin console."""
    uptime_secs = int(time.time() - _service_started_at)
    user_count = db.query(func.count(User.id)).scalar() or 0
    kb_count = db.query(func.count(KnowledgeBase.id)).scalar() or 0
    doc_count = db.query(func.count(Document.id)).scalar() or 0
    session_count = db.query(func.count(SessionModel.session_id)).scalar() or 0
    job_count = db.query(func.count(Job.id)).scalar() or 0

    session_surface_rows = (
        db.query(SessionModel.surface, func.count(SessionModel.session_id))
        .group_by(SessionModel.surface)
        .all()
    )
    session_surface_breakdown = {
        (surface or "unknown"): count for surface, count in session_surface_rows
    }

    job_status_rows = db.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    job_status_breakdown = {(status or "unknown"): count for status, count in job_status_rows}
    runtime_ops = runtime_metrics.snapshot(uptime_secs=uptime_secs)
    job_ops = _build_job_ops_metrics(db)
    deep_research_ops: dict[str, Any] = {}
    try:
        deep_research_ops = _deep_research_admin_request(
            method="GET",
            path="/api/deep-research/admin/metrics",
            current_user=current_user,
        )
    except HTTPException:
        deep_research_ops = {"available": False}
    ask_replay_ops = get_ask_stream_replay_buffer().stats_snapshot()

    return {
        "admin_user": {
            "id": _admin_actor_id(current_user) or 0,
            "username": _admin_name(current_user),
        },
        "metrics": {
            "users": user_count,
            "knowledge_bases": kb_count,
            "documents": doc_count,
            "sessions": session_count,
            "jobs": job_count,
        },
        "breakdown": {
            "sessions_by_surface": session_surface_breakdown,
            "jobs_by_status": job_status_breakdown,
        },
        "ops": {
            "runtime": runtime_ops,
            "jobs": job_ops,
            "deep_research": deep_research_ops,
            "ask_replay": ask_replay_ops,
        },
        "phase2_reserved_modules": [
            "user_management",
            "membership_billing",
            "quota_monitoring",
            "audit_log",
            "feature_flags",
        ],
        "uptime_secs": uptime_secs,
    }


def _parse_date(s: str | None) -> date | None:
    """解析 YYYY-MM-DD 格式日期。"""
    if not (s and s.strip()):
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@router.get("/demo-stats", summary="Demo 访问统计")
def get_admin_demo_stats(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    date_from: str | None = Query(None, description="起始日期 YYYY-MM-DD"),
    date_to: str | None = Query(None, description="截止日期 YYYY-MM-DD"),
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    """Demo 展示界面访问记录，用于检查简历/GitHub 等入口的体验情况。"""
    _ = current_user
    base_query = db.query(DemoAccessLog)
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    if df is not None:
        base_query = base_query.filter(
            func.date(DemoAccessLog.visited_at) >= df
        )
    if dt is not None:
        base_query = base_query.filter(
            func.date(DemoAccessLog.visited_at) <= dt
        )

    total = base_query.with_entities(func.count(DemoAccessLog.id)).scalar() or 0
    demo_entry_enabled = bool(getattr(settings, "SM_DEMO_ENTRY_ENABLED", False))

    # 同一 IP 分配稳定访客编号：按首次出现时间排序，先出现的 IP 编号更小
    ip_first_seen = (
        db.query(DemoAccessLog.ip, func.min(DemoAccessLog.visited_at).label("first_at"))
        .group_by(DemoAccessLog.ip)
        .order_by(func.min(DemoAccessLog.visited_at).asc())
        .all()
    )
    ip_to_visitor_id = {ip: i + 1 for i, (ip, _) in enumerate(ip_first_seen)}

    rows = (
        base_query.order_by(DemoAccessLog.visited_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    ip_stats = (
        base_query.with_entities(
            DemoAccessLog.ip, func.count(DemoAccessLog.id).label("cnt")
        )
        .group_by(DemoAccessLog.ip)
        .order_by(func.count(DemoAccessLog.id).desc())
        .limit(50)
        .all()
    )

    # 按天统计（在筛选范围内）
    by_day_query = (
        base_query.with_entities(
            func.date(DemoAccessLog.visited_at).label("day"),
            func.count(DemoAccessLog.id).label("visits"),
            func.count(distinct(DemoAccessLog.ip)).label("unique_ips"),
        )
        .group_by(func.date(DemoAccessLog.visited_at))
        .order_by(func.date(DemoAccessLog.visited_at).desc())
        .limit(62)
    )
    by_day_raw = by_day_query.all()
    by_day = [
        {
            "day": (d.day.isoformat() if hasattr(d.day, "isoformat") else str(d.day)),
            "visits": d.visits,
            "unique_ips": d.unique_ips,
        }
        for d in by_day_raw
    ]

    unique_ip_count = (
        base_query.with_entities(func.count(distinct(DemoAccessLog.ip))).scalar()
        or 0
    )
    today = date.today()
    today_visits = (
        db.query(func.count(DemoAccessLog.id))
        .filter(func.date(DemoAccessLog.visited_at) == today)
        .scalar()
        or 0
    )

    return {
        "items": [
            {
                "id": r.id,
                "visitor_id": ip_to_visitor_id.get(r.ip),
                "ip": r.ip,
                "path": r.path,
                "user_agent": r.user_agent,
                "visited_at": r.visited_at.isoformat() if r.visited_at else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "by_ip": [{"ip": ip, "count": cnt} for ip, cnt in ip_stats],
        "by_day": by_day,
        "summary": {
            "unique_ips": unique_ip_count,
            "today_visits": today_visits,
        },
        "diagnostic": {
            "demo_entry_enabled": demo_entry_enabled,
        },
    }


@router.get(
    "/documents/parse-preview",
    response_model=DocumentParsePreviewResponse,
    summary="文档解析预览（管理员）",
)
def admin_preview_document_parse(
    kb_id: int = Query(..., ge=1),
    doc_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    """Run parse preview under admin namespace."""
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    owner_user = db.query(User).filter(User.id == kb.user_id).first()
    if not owner_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base owner not found")
    service = ParsePreviewService(db=db, current_user=owner_user)
    return service.build_preview(kb_id=kb_id, doc_id=doc_id)


@router.get("/users", summary="管理员用户列表")
def admin_list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    keyword: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    _ = current_user
    query = db.query(User)
    if keyword:
        query = query.filter(User.username.ilike(f"%{keyword.strip()}%"))
    if role:
        query = query.filter(func.lower(User.role) == role.strip().lower())
    total = query.count()
    rows = (
        query.order_by(User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_serialize_user(user) for user in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.patch("/users/{user_id}/role", summary="更新用户角色")
def admin_update_user_role(
    user_id: int,
    payload: UpdateUserRoleRequest,
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    next_role = (payload.role or "").strip().lower()
    if next_role not in {"user", "admin", "super_admin"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Allowed values: user/admin/super_admin",
        )
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found",
        )

    actor_is_super = True
    old_role = get_user_role(target_user)
    existing_super_admin_count = (
        db.query(func.count(User.id))
        .filter(func.lower(User.role) == "super_admin")
        .scalar()
        or 0
    )
    can_bootstrap_super_admin = existing_super_admin_count == 0
    if old_role == "super_admin" and not actor_is_super:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super_admin can modify a super_admin user",
        )
    if next_role == "super_admin" and not (actor_is_super or can_bootstrap_super_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super_admin can grant super_admin role",
        )

    target_user.role = next_role
    db.add(target_user)
    _append_audit_log(
        db,
        admin_user_id=_admin_actor_id(current_user),
        action="user.role.update",
        target_type="user",
        target_id=str(target_user.id),
        detail={
            "admin_username": _admin_name(current_user),
            "username": target_user.username,
            "old_role": old_role,
            "new_role": next_role,
        },
    )
    db.commit()
    db.refresh(target_user)
    return {"user": _serialize_user(target_user)}


@router.patch("/users/{user_id}/status", summary="更新用户启用状态（封禁/解封）")
def admin_update_user_status(
    user_id: int,
    payload: UpdateUserStatusRequest,
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found",
        )
    target_role = get_user_role(target_user)
    actor_is_super = True
    if target_role == "super_admin" and not actor_is_super:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super_admin can update status of a super_admin user",
        )
    old_status = bool(getattr(target_user, "is_active", True))
    new_status = bool(payload.is_active)
    target_user.is_active = new_status
    db.add(target_user)
    _append_audit_log(
        db,
        admin_user_id=_admin_actor_id(current_user),
        action="user.status.update",
        target_type="user",
        target_id=str(target_user.id),
        detail={
            "admin_username": _admin_name(current_user),
            "username": target_user.username,
            "old_is_active": old_status,
            "new_is_active": new_status,
            "reason": (payload.reason or "").strip(),
        },
    )
    db.commit()
    db.refresh(target_user)
    return {"user": _serialize_user(target_user)}


@router.get("/jobs", summary="管理员任务列表")
def admin_list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status_filter: Optional[str] = Query(None, alias="status"),
    type_filter: Optional[str] = Query(None, alias="type"),
    user_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    _ = current_user
    query = db.query(Job)
    if status_filter:
        query = query.filter(func.lower(Job.status) == status_filter.strip().lower())
    if type_filter:
        query = query.filter(func.lower(Job.type) == type_filter.strip().lower())
    if user_id is not None:
        query = query.filter(Job.user_id == user_id)
    total = query.count()
    rows = (
        query.order_by(Job.created_at.desc(), Job.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [JobInDB.model_validate(job).model_dump(mode="json") for job in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/jobs/{job_id}/cancel", summary="管理员取消任务")
def admin_cancel_job(
    job_id: int,
    payload: Optional[JobActionRequest] = None,
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    current_status = (job.status or "").strip().lower()
    if current_status in {
        JobStatus.SUCCESS.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job in status '{job.status}' cannot be cancelled",
        )
    reason = (payload.reason.strip() if payload and payload.reason else "") or "Cancelled by admin"
    old_status = job.status
    job.status = JobStatus.CANCELLED.value
    job.error = reason
    db.add(job)
    _append_audit_log(
        db,
        admin_user_id=_admin_actor_id(current_user),
        action="job.cancel",
        target_type="job",
        target_id=str(job.id),
        detail={
            "admin_username": _admin_name(current_user),
            "old_status": old_status,
            "new_status": job.status,
            "reason": reason,
            "job_type": job.type,
            "job_owner_user_id": job.user_id,
        },
    )
    db.commit()
    db.refresh(job)
    return {"job": JobInDB.model_validate(job).model_dump(mode="json")}


@router.post("/jobs/{job_id}/retry", summary="管理员重试任务")
def admin_retry_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    source_job = db.query(Job).filter(Job.id == job_id).first()
    if not source_job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    source_status = (source_job.status or "").strip().lower()
    if source_status not in _RETRYABLE_JOB_STATUS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Only failed/partial/cancelled jobs can be retried. "
                f"Current status: {source_job.status}"
            ),
        )
    handler_cls = _resolve_job_handler(source_job.type)
    if handler_cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported job type for retry: {source_job.type}",
        )

    retry_job = job_service.create_job(
        db,
        user_id=source_job.user_id,
        kb_id=source_job.knowledge_base_id,
        type=(source_job.type or "").strip().lower(),
        payload=source_job.payload or {},
    )
    background_tasks.add_task(
        execute_job,
        job_id=retry_job.id,
        handler_cls=handler_cls,
    )

    _append_audit_log(
        db,
        admin_user_id=_admin_actor_id(current_user),
        action="job.retry",
        target_type="job",
        target_id=str(source_job.id),
        detail={
            "admin_username": _admin_name(current_user),
            "source_job_id": source_job.id,
            "retry_job_id": retry_job.id,
            "source_status": source_job.status,
            "job_type": source_job.type,
            "job_owner_user_id": source_job.user_id,
        },
    )
    db.commit()
    return {
        "source_job": JobInDB.model_validate(source_job).model_dump(mode="json"),
        "retry_job": JobInDB.model_validate(retry_job).model_dump(mode="json"),
    }


@router.get("/ops-metrics", summary="管理员运行指标")
def get_admin_ops_metrics(
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    uptime_secs = int(time.time() - _service_started_at)
    runtime_ops = runtime_metrics.snapshot(uptime_secs=uptime_secs)
    job_ops = _build_job_ops_metrics(db)
    deep_research_ops: dict[str, Any]
    try:
        deep_research_ops = _deep_research_admin_request(
            method="GET",
            path="/api/deep-research/admin/metrics",
            current_user=current_user,
        )
    except HTTPException as exc:
        deep_research_ops = {
            "available": False,
            "error": str(exc.detail),
        }
    ask_replay_ops = get_ask_stream_replay_buffer().stats_snapshot()
    return {
        "runtime": runtime_ops,
        "jobs": job_ops,
        "deep_research": deep_research_ops,
        "ask_replay": ask_replay_ops,
        "uptime_secs": uptime_secs,
    }


@router.get("/ask-replay/stats", summary="Ask 流回放状态")
def get_admin_ask_replay_stats(
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    _ = current_user
    return get_ask_stream_replay_buffer().stats_snapshot()


@router.get("/deep-research/runs", summary="DeepResearch 全局任务列表")
def admin_list_deep_research_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status_filter: Optional[str] = Query(None, alias="status"),
    user_id: Optional[int] = Query(None),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    return _deep_research_admin_request(
        method="GET",
        path="/api/deep-research/admin/runs",
        current_user=current_user,
        params={
            "page": page,
            "page_size": page_size,
            "status": status_filter,
            "user_id": user_id,
        },
    )


@router.get("/deep-research/queue", summary="DeepResearch 全局队列")
def admin_get_deep_research_queue(
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    return _deep_research_admin_request(
        method="GET",
        path="/api/deep-research/admin/queue",
        current_user=current_user,
    )


@router.post("/deep-research/{research_id}/cancel", summary="管理员取消 DeepResearch 任务")
def admin_cancel_deep_research_run(
    research_id: str,
    payload: Optional[JobActionRequest] = None,
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    response = _deep_research_admin_request(
        method="POST",
        path=f"/api/deep-research/admin/{research_id}/cancel",
        current_user=current_user,
        json_body={"reason": (payload.reason or "").strip()} if payload else {},
    )
    _append_audit_log(
        db,
        admin_user_id=_admin_actor_id(current_user),
        action="deep_research.cancel",
        target_type="deep_research",
        target_id=research_id,
        detail={
            "admin_username": _admin_name(current_user),
            "reason": (payload.reason or "").strip() if payload else "",
            "response": response,
        },
    )
    db.commit()
    return response


@router.post("/deep-research/{research_id}/retry", summary="管理员重试 DeepResearch 任务")
def admin_retry_deep_research_run(
    research_id: str,
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    response = _deep_research_admin_request(
        method="POST",
        path=f"/api/deep-research/admin/{research_id}/retry",
        current_user=current_user,
    )
    _append_audit_log(
        db,
        admin_user_id=_admin_actor_id(current_user),
        action="deep_research.retry",
        target_type="deep_research",
        target_id=research_id,
        detail={
            "admin_username": _admin_name(current_user),
            "response": response,
        },
    )
    db.commit()
    return response


@router.get("/audit-logs", summary="管理员审计日志")
def admin_list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: Optional[str] = Query(None),
    admin_user_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: AdminConsolePrincipal = Depends(get_current_admin_console_user),
):
    _ = current_user
    query = db.query(AdminAuditLog)
    if action:
        query = query.filter(func.lower(AdminAuditLog.action) == action.strip().lower())
    if admin_user_id is not None:
        query = query.filter(AdminAuditLog.admin_user_id == admin_user_id)
    total = query.count()
    rows = (
        query.order_by(AdminAuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    admin_ids = {
        item.admin_user_id for item in rows if item.admin_user_id is not None
    }
    admin_usernames = {}
    if admin_ids:
        users = db.query(User).filter(User.id.in_(admin_ids)).all()
        admin_usernames = {item.id: item.username for item in users}
    items = [
        {
            "id": item.id,
            "admin_user_id": item.admin_user_id,
            "admin_username": admin_usernames.get(item.admin_user_id)
            or (item.detail_json or {}).get("admin_username"),
            "action": item.action,
            "target_type": item.target_type,
            "target_id": item.target_id,
            "detail_json": item.detail_json or {},
            "created_at": item.created_at,
        }
        for item in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
