from sqlalchemy import Column, String, TIMESTAMP, Integer, ForeignKey, Text
from sqlalchemy.sql import func
from models.base import Base

class Session(Base):
    """
    SQLAlchemy ORM 模型，用于映射数据库中的 `sessions` 表。

    该表存储了用户的聊天会话信息，每个会话都是一个独立的聊天上下文。
    """
    __tablename__ = 'sessions'
    
    # session_id: 会话的唯一标识符，作为主键。
    # - String(16): 数据类型为字符串，长度16。
    # - primary_key=True: 将此列设置为主键。
    session_id = Column(String(16), primary_key=True)
    
    # session_name: 会话的名称，用于向用户展示。
    # - String(255): 数据类型为字符串，最大长度255。
    # - nullable=False: 会话名称不能为空。
    session_name = Column(String(255), nullable=False)
    
    # user_id: 该会话所属用户的ID（暂保留为字符串以兼容现有数据）。
    user_id = Column(String(255), nullable=False)

    # knowledge_base_id: 该会话绑定的知识库ID（可为空，向后兼容）。
    knowledge_base_id = Column(Integer, ForeignKey('knowledgebases.id', ondelete='SET NULL'), nullable=True)
    
    # created_at: 会话记录的创建时间戳。
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.now())
    
    # updated_at: 会话记录的最后更新时间戳。
    updated_at = Column(TIMESTAMP, nullable=False, server_default=func.now(), onupdate=func.now()) 

    # 会话级默认参数（JSON 字符串），用于保存检索/生成等默认设置
    defaults_json = Column(Text, nullable=True)