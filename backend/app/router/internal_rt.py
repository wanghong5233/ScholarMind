"""
内部服务 API 路由
专门用于服务间调用（如 LaTeX Agent）
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from models.user import User
from schemas.rag import Chunk as RagChunk
from service.auth import get_current_user
from service import knowledgebase_service
from service.core.rag.service import RAGService
from utils.database import get_db
from core.config import settings
from utils.get_logger import logger


router = APIRouter(prefix="/internal", tags=["Internal Services"])


@router.get("/retrieve", response_model=List[RagChunk], summary="内部服务检索接口")
def internal_retrieve(
    q: str = Query(..., description="查询文本"),
    kb_id: int = Query(..., description="知识库 ID"),
    top_k: int = Query(settings.SM_RAG_TOPK, ge=1, le=50, description="返回数量"),
    focus_doc_ids: Optional[str] = Query(None, description="以逗号分隔的 document_id 列表"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    内部服务专用检索接口（无需 session_id）
    
    用于 LaTeX Agent 等内部服务直接基于 kb_id 进行检索
    不依赖 session，直接使用 global_only 模式
    """
    # 验证知识库归属
    try:
        kb = knowledgebase_service.get_kb_by_id(db=db, kb_id=kb_id, user_id=current_user.id)
    except Exception as e:
        logger.error(f"Knowledge base validation failed: {e}")
        raise HTTPException(status_code=404, detail="知识库不存在或无权访问")
    
    # 解析 focus_doc_ids
    focus_ids_list = None
    if focus_doc_ids:
        try:
            focus_ids_list = [int(x) for x in focus_doc_ids.split(",") if x.strip().isdigit()]
        except Exception:
            focus_ids_list = None
    
    # 执行检索（global_only 模式，不使用 session index）
    rag = RAGService()
    try:
        results = rag.retrieve(
            query=q,
            kb_id=kb_id,
            top_k=top_k,
            focus_doc_ids=focus_ids_list,
            session_index=None,  # 不使用 session index
            index_mode="global_only",  # 只检索全局知识库
        )
        
        logger.info(f"Internal retrieve: kb_id={kb_id}, query={q[:50]}..., results={len(results)}")
        
        # 转换为 RagChunk 格式
        out: List[RagChunk] = []
        for item in results:
            md = item.get("metadata") or {}
            out.append(
                RagChunk(
                    chunk_id=str(item.get("chunk_id", "")),
                    document_id=str(md.get("document_id", "")),
                    content=item.get("text", ""),
                    metadata=md,
                )
            )
        return out
    
    except Exception as e:
        logger.error(f"Internal retrieve failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"检索失败: {str(e)}")

