"""Session management service."""

from __future__ import annotations

import json
import uuid
from typing import Any, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models.message import Message
from models.user import User
from schemas.knowledge_base import KnowledgeBaseCreate
from schemas.session import (
    CreateSessionRequest,
    CreateSessionResponse,
    SessionDefaults,
    SessionDetail,
)
from service.knowledgebase_service import create_kb_for_user, get_kb_by_id
from service.core.rag.providers.registry import resolve_provider
from service.session_service import SessionService
from utils.get_logger import logger


class SessionManagementService:
    """Handle session CRUD and defaults management."""

    def __init__(self, *, db: Session, current_user: User) -> None:
        """Initialize the session management service.

        Args:
            db (Session): SQLAlchemy session.
            current_user (User): Authenticated user.
        """
        self.db = db
        self.current_user = current_user
        self.session_service = SessionService(db)

    def list_messages(self, *, session_id: str, page: int, page_size: int) -> dict[str, Any]:
        """List paginated messages for a session."""
        s = self._get_session(session_id)
        q = self.db.query(Message).filter(Message.session_id == s.session_id)
        total = q.count()
        items = (
            q.order_by(Message.create_time.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        out = [
            {
                "message_id": str(m.message_id),
                "session_id": m.session_id,
                "user_question": m.user_question,
                "model_answer": m.model_answer,
                "create_time": str(m.create_time),
            }
            for m in items
        ]
        return {"total": total, "page": page, "pageSize": page_size, "items": out}

    def create_session(self, *, req: CreateSessionRequest) -> CreateSessionResponse:
        """Create a new session."""
        session_id = f"session_{uuid.uuid4().hex[:8]}"
        defaults = req.defaults.model_copy() if req.defaults is not None else SessionDefaults()

        if req.ephemeral:
            kb_name = f"temp_kb_for_{session_id}"
            kb = create_kb_for_user(
                db=self.db,
                kb_create=KnowledgeBaseCreate(name=kb_name, description=None, is_ephemeral=True),
                user_id=self.current_user.id,
            )
            kb_id = kb.id
            logger.info("Created ephemeral KB id=%s for session %s", kb_id, session_id)
            if req.defaults is None:
                defaults.useSessionKnowledgeBase = True
                defaults.useUserKnowledgeBase = False
                defaults.userKnowledgeBaseId = None
        elif req.kbId:
            kb = get_kb_by_id(db=self.db, kb_id=req.kbId, user_id=self.current_user.id)
            kb_id = kb.id
            logger.info("Bind session %s to existing KB id=%s", session_id, kb_id)
            if defaults.useUserKnowledgeBase and defaults.userKnowledgeBaseId is None:
                defaults.userKnowledgeBaseId = kb_id
            if req.defaults is None:
                defaults.useSessionKnowledgeBase = False
                defaults.useUserKnowledgeBase = True
                defaults.userKnowledgeBaseId = kb_id
                defaults.retrievalStrategy = resolve_provider(getattr(kb, "rag_provider", None))
        else:
            raise HTTPException(
                status_code=400,
                detail="必须提供 kbId 或将 ephemeral 设为 true。",
            )

        self.session_service.create_session(
            session_id=session_id,
            user_id=self.current_user.id,
            knowledge_base_id=kb_id,
            session_name=f"Session for KB {kb_id}",
            defaults_json=json.dumps(defaults.model_dump(), ensure_ascii=False),
        )

        return CreateSessionResponse(
            sessionId=session_id,
            kbId=kb_id,
            ephemeral=req.ephemeral,
            defaults=defaults,
        )

    def get_session_detail(self, *, session_id: str) -> SessionDetail:
        """Return session detail."""
        s = self._get_session(session_id)
        return SessionDetail(
            sessionId=s.session_id,
            kbId=s.knowledge_base_id,
            sessionName=s.session_name,
        )

    def get_session_defaults(self, *, session_id: str) -> SessionDefaults:
        """Return session defaults."""
        s = self._get_session(session_id)
        if s.defaults_json:
            try:
                data = json.loads(s.defaults_json)
                return SessionDefaults(**data)
            except Exception:
                pass
        return SessionDefaults()

    def update_session_defaults(self, *, session_id: str, payload: SessionDefaults) -> SessionDefaults:
        """Update session defaults."""
        s = self._get_session(session_id)
        data = payload.model_dump()
        if data.get("useSessionKnowledgeBase"):
            if s.knowledge_base_id is None:
                raise HTTPException(status_code=400, detail="当前会话没有可用的临时知识库")
        if data.get("useUserKnowledgeBase"):
            user_kb_id = data.get("userKnowledgeBaseId")
            if user_kb_id is None:
                raise HTTPException(status_code=400, detail="启用本地知识库时必须选择知识库")
            get_kb_by_id(db=self.db, kb_id=user_kb_id, user_id=self.current_user.id)
        else:
            data["userKnowledgeBaseId"] = None

        normalized = SessionDefaults(**data)
        self.session_service.update_defaults_json(
            session_id=session_id,
            defaults_json=json.dumps(normalized.model_dump(), ensure_ascii=False),
        )
        return normalized

    def delete_session(self, *, session_id: str) -> dict[str, Any]:
        """Delete a session."""
        s = self._get_session(session_id)
        return self.session_service.delete_session(session_id=s.session_id)

    def _get_session(self, session_id: str):
        s = self.session_service.get_session_by_id(session_id=session_id)
        if not s:
            raise HTTPException(status_code=404, detail="会话不存在")
        if str(self.current_user.id) != str(s.user_id):
            raise HTTPException(status_code=403, detail="无权访问该会话")
        return s
