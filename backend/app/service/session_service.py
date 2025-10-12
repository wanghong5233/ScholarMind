from typing import Optional
from sqlalchemy.orm import Session
from models.session import Session as SessionModel


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

    def update_defaults_json(self, *, session_id: str, defaults_json: Optional[str]) -> None:
        s = self.get_session_by_id(session_id=session_id)
        if not s:
            return
        s.defaults_json = defaults_json
        self.db.commit()