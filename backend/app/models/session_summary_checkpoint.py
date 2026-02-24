"""SessionSummaryCheckpoint model for rolling summary checkpoints."""

from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from models.base import Base


class SessionSummaryCheckpoint(Base):
    """
    会话摘要检查点：记录 rolling_summary 每次更新时的快照。

    用于回溯、调试与可选的历史摘要查询。
    """

    __tablename__ = "session_summary_checkpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(16),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.message_id", ondelete="SET NULL"),
        nullable=True,
    )
    summary = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.now())
