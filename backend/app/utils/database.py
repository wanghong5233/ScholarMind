from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.base import Base
from core.config import settings

DATABASE_URL = settings.DATABASE_URL


def _create_engine():
    # SQLite 不支持下面这组 QueuePool 参数，保持默认构造即可。
    if str(DATABASE_URL or "").startswith("sqlite"):
        return create_engine(DATABASE_URL)
    return create_engine(
        DATABASE_URL,
        pool_size=max(1, int(settings.SM_DB_POOL_SIZE)),
        max_overflow=max(0, int(settings.SM_DB_MAX_OVERFLOW)),
        pool_timeout=max(1, int(settings.SM_DB_POOL_TIMEOUT_SECS)),
        pool_recycle=max(1, int(settings.SM_DB_POOL_RECYCLE_SECS)),
        pool_pre_ping=bool(settings.SM_DB_POOL_PRE_PING),
    )


engine = _create_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库"""
    Base.metadata.create_all(bind=engine)