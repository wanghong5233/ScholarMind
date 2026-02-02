"""Retrieval dashboard wrapper service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from models.user import User
from schemas.retrieval_debug import (
    RetrievalCompareRequest,
    RetrievalDashboardRequest,
    RetrievalDashboardResponse,
)
from service.core.conversation.retrieval_compare_service import RetrievalCompareService


class RetrievalDashboardService:
    """Build retrieval dashboard summary."""

    def __init__(self, *, db: Session, current_user: User) -> None:
        self.db = db
        self.current_user = current_user
        self.compare_service = RetrievalCompareService(db=db, current_user=current_user)

    def handle(self, *, payload: RetrievalDashboardRequest) -> RetrievalDashboardResponse:
        compare_payload = RetrievalCompareRequest(**payload.model_dump())
        compare_resp = self.compare_service.handle(payload=compare_payload)
        return RetrievalDashboardResponse(
            kb_id=compare_resp.kb_id,
            query=compare_resp.query,
            top_k=compare_resp.top_k,
            provider_a=compare_resp.provider_a,
            provider_b=compare_resp.provider_b,
            panel=compare_resp.panel,
        )
