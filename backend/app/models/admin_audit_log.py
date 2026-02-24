from sqlalchemy import Column, ForeignKey, Integer, JSON, String, TIMESTAMP, Index
from sqlalchemy.sql import func

from models.base import Base


class AdminAuditLog(Base):
    """管理员关键操作审计日志。"""

    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action = Column(String(64), nullable=False, comment="操作类型")
    target_type = Column(String(64), nullable=False, comment="目标类型")
    target_id = Column(String(128), nullable=True, comment="目标标识")
    detail_json = Column(JSON, nullable=True, comment="操作详情")
    created_at = Column(
        TIMESTAMP,
        nullable=False,
        server_default=func.now(),
        comment="创建时间",
    )

    __table_args__ = (
        Index("idx_admin_audit_logs_created_at", "created_at"),
        Index("idx_admin_audit_logs_admin_user_id", "admin_user_id"),
        Index("idx_admin_audit_logs_action", "action"),
    )
