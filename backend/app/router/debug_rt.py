from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from models.user import User
from schemas.retrieval_debug import (
    RetrievalPreviewRequest,
    RetrievalDebugResponse,
    RetrievalCompareRequest,
    RetrievalCompareResponse,
    RetrievalDashboardRequest,
    RetrievalDashboardResponse,
    RetrievalEvalRunRequest,
    RetrievalEvalRunResponse,
)
from service.auth import get_current_user
from service.core.conversation.retrieval_compare_service import RetrievalCompareService
from service.core.conversation.retrieval_dashboard_service import RetrievalDashboardService
from service.core.conversation.retrieval_eval_service import RetrievalEvalService
from service.core.conversation.retrieval_preview_service import RetrievalPreviewService
from utils.database import get_db


router = APIRouter()


@router.post(
    "/retrieval-preview",
    response_model=RetrievalDebugResponse,
    summary="检索/精排调试预览",
    description="执行多阶段检索并返回各阶段调试信息、候选列表及最终的 LLM 输入片段。",
)
def retrieval_preview(
    payload: RetrievalPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RetrievalDebugResponse:
    service = RetrievalPreviewService(db=db, current_user=current_user)
    return service.handle(payload=payload)


@router.post(
    "/retrieval-compare",
    response_model=RetrievalCompareResponse,
    summary="检索 Provider 对比评估",
    description="对比不同 RAG provider 的检索结果与重叠度。",
)
def retrieval_compare(
    payload: RetrievalCompareRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RetrievalCompareResponse:
    service = RetrievalCompareService(db=db, current_user=current_user)
    return service.handle(payload=payload)


@router.post(
    "/retrieval-dashboard",
    response_model=RetrievalDashboardResponse,
    summary="检索评估仪表盘",
    description="返回用于前端渲染的检索评估面板数据。",
)
def retrieval_dashboard(
    payload: RetrievalDashboardRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RetrievalDashboardResponse:
    service = RetrievalDashboardService(db=db, current_user=current_user)
    return service.handle(payload=payload)


@router.get(
    "/retrieval-eval-sets",
    summary="列出检索评估集",
)
def retrieval_eval_sets(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    service = RetrievalEvalService(db=db, current_user=current_user)
    return {"sets": service.list_eval_sets()}


@router.post(
    "/retrieval-eval",
    response_model=RetrievalEvalRunResponse,
    summary="检索评估回放",
    description="基于评估集对比不同 provider 的检索效果。",
)
def retrieval_eval(
    payload: RetrievalEvalRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RetrievalEvalRunResponse:
    service = RetrievalEvalService(db=db, current_user=current_user)
    return service.run(payload=payload)

