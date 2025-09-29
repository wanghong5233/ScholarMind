from sqlalchemy import Column, Integer, String, TIMESTAMP
from sqlalchemy.sql import func
from models.base import Base

# 注释掉原来不一致的 KnowledgeBase 类定义
# class KnowledgeBase(Base):
#     __tablename__ = "knowledgebase"
#     id = Column(Integer, primary_key=True, index=True)
#     user_id = Column(String, index=True)
#     file_name = Column(String)
#     created_at = Column(DateTime(timezone=True), server_default=func.now())
#     updated_at = Column(DateTime(timezone=True), onupdate=func.now())

# 新的正确定义，与项目中实际使用的表结构一致
class KnowledgeBase(Base):
    """
    SQLAlchemy ORM 模型，用于映射数据库中的 `knowledgebases` 表。

    该表记录了用户成功上传并索引到知识库中的文件元数据。
    它代表了可用于RAG检索的文档集合。
    """
    __tablename__ = 'knowledgebases'
    
    # id: 记录的唯一主键。
    # - Integer: 数据类型为整数。
    # - primary_key=True: 设为主键。
    # - autoincrement=True: 由数据库自动生成和递增。
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # user_id: 该知识库文件所属用户的ID。
    # - String(255): 数据类型为字符串，长度255。
    # - nullable=False: 必须关联到一个用户。
    user_id = Column(String(255), nullable=False)
    
    # file_name: 知识库中的文件名。
    # - String(255): 数据类型为字符串，长度255。
    # - nullable=False: 文件名不能为空。
    file_name = Column(String(255), nullable=False)
    
    # created_at: 文件记录的创建时间戳。
    # - TIMESTAMP: 数据类型为时间戳。
    # - nullable=False: 不能为空。
    # - server_default='CURRENT_TIMESTAMP': 插入时数据库自动填充为当前时间。
    created_at = Column(TIMESTAMP, nullable=False, server_default='CURRENT_TIMESTAMP')
    
    # updated_at: 文件记录的最后更新时间戳。
    updated_at = Column(TIMESTAMP, nullable=False, server_default='CURRENT_TIMESTAMP') 