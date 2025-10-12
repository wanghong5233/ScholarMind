from typing import Optional, Literal
from pydantic import BaseModel, Field


class SessionDefaults(BaseModel):
    """会话级默认参数（可保存/回读）。"""
    retrievalStrategy: Literal["basic", "multi_query", "hyde"] = Field("basic")
    rerankerStrategy: Literal["none", "supervised", "rl"] = Field("none")
    topK: int = Field(5, ge=1, le=50)
    language: Literal["zh", "en"] = Field("zh")
    streaming: bool = Field(True)


class CreateSessionRequest(BaseModel):
    """创建会话请求体。
    - 传入 kbId → 绑定既有 KB；
    - 无 kbId 且 ephemeral=true → 创建临时KB（后续由上传接口落库/或仅会话内使用）。
    """
    kbId: Optional[int] = Field(None, description="绑定的知识库ID")
    ephemeral: bool = Field(False, description="是否创建临时会话（不绑定KB）")
    defaults: Optional[SessionDefaults] = Field(None, description="会话默认检索/生成参数")


class CreateSessionResponse(BaseModel):
    sessionId: str
    kbId: Optional[int] = None
    ephemeral: bool
    defaults: SessionDefaults


class SessionDetail(BaseModel):
    sessionId: str
    kbId: Optional[int] = None
    sessionName: str
