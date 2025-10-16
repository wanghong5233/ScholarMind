from typing import Optional, Literal
from pydantic import BaseModel, Field
from typing import List, Dict, Any


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


# --- Compare API ---
class CompareRequest(BaseModel):
    """跨论文对比请求。
    - docIds: 需要对比的文档 ID 列表（同一会话/知识库下）。
    - dimensions: 对比维度（如 ["Methodology", "Results", "Limitations"]）。
    """
    docIds: List[int] = Field(..., min_items=2, description="待对比的 document_id 列表（至少2篇）")
    dimensions: List[str] = Field(..., min_items=1, description="对比维度列表")


class CompareResponse(BaseModel):
    answer: str = Field(..., description="Markdown 表格形式的对比结果")
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    usage: Dict[str, Any] = Field(default_factory=dict)
    debug: Dict[str, Any] = Field(default_factory=dict)
