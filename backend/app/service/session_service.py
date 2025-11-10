from typing import Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from models.session import Session as SessionModel
from models.message import Message
from models.knowledgebase import KnowledgeBase
from service import knowledgebase_service
from utils.get_logger import logger


class SessionService:
    def __init__(self, db: Session):
        self.db = db

    def create_session(
        self,
        *,
        session_id: str,
        user_id: int | str,
        knowledge_base_id: Optional[int],
        session_name: str,
        defaults_json: Optional[str] = None,
    ) -> SessionModel:
        """Create and persist a chat session bound to an optional knowledge base."""
        session_record = SessionModel(
            session_id=session_id,
            session_name=session_name,
            user_id=str(user_id),
            knowledge_base_id=knowledge_base_id,
            defaults_json=defaults_json,
        )
        self.db.add(session_record)
        self.db.commit()
        self.db.refresh(session_record)
        return session_record

    def get_session_by_id(self, *, session_id: str) -> Optional[SessionModel]:
        """Fetch a session by its id."""
        return (
            self.db.query(SessionModel)
            .filter(SessionModel.session_id == session_id)
            .first()
        )

    def _resolve_ephemeral_kb(self, session_obj: SessionModel) -> Tuple[Optional[int], bool]:
        kb_id = session_obj.knowledge_base_id
        if not kb_id:
            return None, False
        kb = (
            self.db.query(KnowledgeBase)
            .filter(KnowledgeBase.id == kb_id)
            .first()
        )
        if not kb:
            return kb_id, False
        return kb_id, bool(kb.is_ephemeral)

    def delete_session(self, *, session_id: str) -> Dict[str, Any]:
        """Delete a session, its messages and associated ephemeral knowledge base."""
        session_obj = self.get_session_by_id(session_id=session_id)
        if not session_obj:
            return {"deleted": False, "messages_deleted": 0, "kb_deleted": False}

        kb_id, kb_is_ephemeral = self._resolve_ephemeral_kb(session_obj)
        owner_id = session_obj.user_id

        deleted_messages = (
            self.db.query(Message)
            .filter(Message.session_id == session_id)
            .delete(synchronize_session=False)
        )
        self.db.delete(session_obj)
        self.db.commit()

        kb_deleted = False
        if kb_id and kb_is_ephemeral:
            try:
                user_id_int = int(owner_id) if owner_id is not None else None
            except (TypeError, ValueError):
                user_id_int = None

            if user_id_int is not None:
                try:
                    knowledgebase_service.delete_kb(
                        db=self.db,
                        kb_id=kb_id,
                        user_id=user_id_int,
                    )
                    kb_deleted = True
                    logger.info(
                        "Deleted ephemeral knowledge base %(kb_id)s after removing session %(session_id)s",
                        {"kb_id": kb_id, "session_id": session_id},
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to delete ephemeral knowledge base %s for session %s: %s",
                        kb_id,
                        session_id,
                        exc,
                    )

        return {
            "deleted": True,
            "messages_deleted": deleted_messages,
            "kb_deleted": kb_deleted,
            "kb_id": kb_id if kb_deleted else None,
        }

    def update_defaults_json(self, *, session_id: str, defaults_json: Optional[str]) -> None:
        s = self.get_session_by_id(session_id=session_id)
        if not s:
            return
        s.defaults_json = defaults_json
        self.db.commit()

    def update_rolling_summary(self, *, session_id: str, rolling_summary: Optional[str]) -> None:
        s = self.get_session_by_id(session_id=session_id)
        if not s:
            return
        s.rolling_summary = rolling_summary
        self.db.commit()

    def reset_memory_guide(self, *, session_id: str) -> None:
        s = self.get_session_by_id(session_id=session_id)
        if not s:
            return
        if (s.memory_guide_fail_count or 0) == 0 and not s.memory_guide_disabled:
            return
        s.memory_guide_fail_count = 0
        s.memory_guide_disabled = False
        self.db.commit()

    def increment_memory_guide_fail(self, *, session_id: str, threshold: int) -> bool:
        s = self.get_session_by_id(session_id=session_id)
        if not s:
            return False
        s.memory_guide_fail_count = (s.memory_guide_fail_count or 0) + 1
        if s.memory_guide_fail_count >= threshold:
            s.memory_guide_disabled = True
        self.db.commit()
        return bool(s.memory_guide_disabled)