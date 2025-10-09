from fastapi import APIRouter, Body, UploadFile, File, HTTPException, Query, Security, status, Depends
import uuid
from schemas.chat import SessionResponse, ChatRequest
from fastapi.responses import StreamingResponse
import os
from typing import List, Optional
from service.core.api.utils.file_utils import get_project_base_directory
from fastapi_jwt import JwtAuthorizationCredentials
from service.auth import access_security
from utils import logger
from database.knowledgebase_operations import insert_knowledgebase, verify_user_knowledgebase
from sqlalchemy.orm import Session
from sqlalchemy import select
from models.knowledgebase import KnowledgeBase
from utils.database import get_db
from service.quick_parse_service import quick_parse_service
from service.document_upload_service import DocumentUploadService
from schemas.document_upload import DocumentUploadResponse, SessionDocumentsResponse, SessionDocumentSummary
import os
from service.rag_service import RAGService
from dependencies import get_rag_service
from core.config import settings

# 配置日志
logger.info(f"ES_HOST: {settings.ES_HOST}") # 从 settings 读取
logger.info(f"ELASTICSEARCH_URL: {settings.ELASTICSEARCH_URL}") # 从 settings 读取

router = APIRouter()

##################################
# 创建一个新的对话 Session
##################################

@router.post("/create_session", response_model=SessionResponse)
async def create_session(
    credentials: JwtAuthorizationCredentials = Security(access_security),
):
    """
    为认证用户创建一个新的、唯一的对话会话 (Session)。

    此接口是开始一次新聊天的起点。它生成一个唯一的会话ID，
    该ID将用于后续的所有操作，如文档上传、聊天问答等，
    从而将这些操作隔离在独立的上下文中。

    Args:
        credentials (JwtAuthorizationCredentials): 通过依赖注入自动验证JWT Token，
            并提供解码后的用户信息。

    Returns:
        SessionResponse: 一个包含新创建的 `session_id` 和成功状态的响应体。

    Raises:
        HTTPException (401): 如果提供的JWT Token无效或缺失。
        HTTPException (500): 如果服务器在生成session_id时发生内部错误。
    """
    try:
        user_id = credentials.subject.get("user_id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        session_id = str(uuid.uuid4()).replace("-", "")[:16]

        return {
            "session_id": session_id,
            "status": "success",
            "message": "Session created successfully"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

##################################
# 快速文档解析接口
##################################

@router.post("/quick_parse")
async def quick_parse_document(
    session_id: str = Query(..., description="会话ID"),
    file: UploadFile = File(..., description="要解析的文档"),
    credentials: JwtAuthorizationCredentials = Security(access_security),
    db: Session = Depends(get_db),
):
    """
    提供一个轻量级的、即时的文档文本提取功能。

    此接口用于快速分析小型文档，提取其纯文本内容并暂存到Redis中。
    它有严格的格式和大小限制，不适用于构建长期知识库。

    - **核心功能**:
        - 支持格式: `docx`, `pdf`, `txt`。
        - 限制: PDF不超过4页，DOCX/TXT不超过4000字符。
        - 存储: 解析结果在Redis中暂存2小时。
        - 幂等性: 每个会话ID只允许上传一次。

    Args:
        session_id (str): 作为查询参数传入的当前会话ID。
        file (UploadFile): 用户通过表单上传的文档文件。
        credentials (JwtAuthorizationCredentials): 依赖注入，用于用户认证。
        db (Session): 依赖注入，提供数据库会话以记录上传操作。

    Returns:
        dict: 一个包含解析结果摘要的字典，如文件名、页数/字符数等。

    Raises:
        HTTPException:
            - 400: 文件格式不支持、内容为空、超出大小限制、会话已存在文档等。
            - 401: 用户认证失败。
            - 500: 服务器内部处理或数据库记录失败。
    """
    try:
        user_id = str(credentials.subject.get("user_id"))
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        # 读取文件内容
        file_content = await file.read()
        
        # 获取文件信息
        file_size = len(file_content)
        file_extension = os.path.splitext(file.filename)[1].lower() if file.filename else ""
        document_type = file_extension.replace(".", "") if file_extension else "unknown"
        
        # 调用服务层处理业务逻辑
        result = quick_parse_service.quick_parse_document(
            session_id=session_id,
            filename=file.filename,
            file_content=file_content
        )
        
        # 记录文档上传信息到数据库
        try:
            DocumentUploadService.create_upload_record(
                db=db,
                session_id=session_id,
                document_name=file.filename,
                document_type=document_type,
                file_size=file_size
            )
            logger.info(f"文档上传记录已保存: session_id={session_id}, document_name={file.filename}")
        except Exception as db_error:
            logger.error(f"保存文档上传记录失败: {str(db_error)}")
            # 数据库记录失败不影响主要功能，继续返回解析结果
        
        logger.info(f"用户 {user_id} 的文档解析完成，session_id: {session_id}")
        return result

    except HTTPException as e:
        logger.error(f"快速解析错误: {str(e)}")
        raise e
    except Exception as e:
        logger.exception(f"快速解析发生未知错误: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"内部服务器错误: {str(e)}"
        )

##################################
# 获取解析内容接口
##################################

@router.get("/get_parsed_content")
async def get_parsed_content(
    session_id: str = Query(..., description="会话ID"),
    credentials: JwtAuthorizationCredentials = Security(access_security),
):
    """
    从Redis中获取由 `quick_parse` 接口暂存的文档文本内容。

    Args:
        session_id (str): 作为查询参数传入，用于在Redis中查找内容的key。
        credentials (JwtAuthorizationCredentials): 依赖注入，用于用户认证。

    Returns:
        dict: 一个包含解析出的文本内容、长度以及在Redis中剩余存活时间的字典。

    Raises:
        HTTPException (401): 用户认证失败。
        HTTPException (404): 如果在Redis中找不到对应session_id的内容（可能已过期或从未上传）。
    """
    try:
        user_id = str(credentials.subject.get("user_id"))
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        # 调用服务层获取内容
        result = quick_parse_service.get_parsed_content(session_id)
        
        logger.info(f"用户 {user_id} 获取解析内容，session_id: {session_id}")
        return result

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

##################################
# 基于RAG知识库对话 (已重构)
##################################

@router.post("/chat_on_docs")
async def chat_on_docs(
    session_id: str = Query(...),
    request: ChatRequest = Body(..., description="User message"),
    credentials: JwtAuthorizationCredentials = Security(access_security),
    rag_service: RAGService = Depends(get_rag_service)
):
    """
    【已重构】执行一次完整的RAG（检索增强生成）问答流程。

    此接口通过依赖注入获取 RAGService 实例，将所有复杂的业务逻辑
    （向量生成、检索、重排、Prompt构造、LLM调用）都委托给服务层处理。
    API层本身只负责请求校验、认证和流式响应的封装。
    """
    try:
        user_id = str(credentials.subject.get("user_id"))
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        
        logger.info(f"User '{user_id}' in session '{session_id}' started a new chat request.")
        logger.info(f"Question: {request.message}")
        
        # 核心逻辑：直接调用 RAGService 的流式问答方法
        # user_id 可以用作 index_name，实现多租户数据隔离
        response_generator = rag_service.ask_stream(
            query=request.message,
            index_name=f"user_{user_id}" # 示例：按用户ID隔离知识库
        )
        
        return StreamingResponse(response_generator, media_type="text/event-stream")
    
    except Exception as e:
        logger.exception(f"An unexpected error occurred in chat_on_docs for user {user_id}: {e}")
        # 统一的异常处理中间件会捕获这个错误并返回 500
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An internal error occurred: {str(e)}"
        )

@router.post("/upload_files")
async def upload_files(
    session_id: Optional[str] = Query(None),
    files: List[UploadFile] = File(...),
    credentials: JwtAuthorizationCredentials = Security(access_security),
    db: Session = Depends(get_db)
):
    """
    【已废弃】上传文件到旧的知识库流程。
    
    此接口已被新的、基于知识库ID的上传接口 `/api/knowledgebases/{kb_id}/documents/upload` 取代。
    调用此接口将直接返回 410 Gone 状态。
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="This API is deprecated. Please use POST /api/knowledgebases/{kb_id}/documents/upload instead."
    )

##################################
# 查询会话文档上传信息接口
##################################

@router.get("/sessions/{session_id}/documents", response_model=SessionDocumentsResponse)
async def get_session_documents(
    session_id: str,
    credentials: JwtAuthorizationCredentials = Security(access_security),
    db: Session = Depends(get_db),
):
    """
    获取指定会话中所有已上传文档的元数据记录。

    Args:
        session_id (str): 作为路径参数传入的目标会话ID。
        credentials (JwtAuthorizationCredentials): 依赖注入，用于用户认证。
        db (Session): 依赖注入，提供数据库会话。

    Returns:
        SessionDocumentsResponse: 一个响应体，其中包含该会话所有文档的详细
            列表（文件名、类型、大小、上传时间等）。

    Raises:
        HTTPException (401): 用户认证失败。
        HTTPException (500): 查询数据库时发生内部错误。
    """
    try:
        user_id = str(credentials.subject.get("user_id"))
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        # 获取会话的所有文档记录
        documents = DocumentUploadService.get_session_documents(db, session_id)
        has_documents = len(documents) > 0
        
        return SessionDocumentsResponse(
            session_id=session_id,
            has_documents=has_documents,
            documents=[DocumentUploadResponse.from_orm(doc) for doc in documents],
            total_count=len(documents)
        )
        
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(f"获取会话文档信息失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/sessions/{session_id}/documents/summary", response_model=SessionDocumentSummary)
async def get_session_document_summary(
    session_id: str,
    credentials: JwtAuthorizationCredentials = Security(access_security),
    db: Session = Depends(get_db),
):
    """
    获取指定会话的文档上传摘要信息。

    提供一个关于会话中已上传文档的快速概览，例如是否有文档、
    最新上传的文档信息以及总文档数，用于在UI上进行轻量级展示。

    Args:
        session_id (str): 作为路径参数传入的目标会话ID。
        credentials (JwtAuthorizationCredentials): 依赖注入，用于用户认证。
        db (Session): 依赖注入，提供数据库会话。

    Returns:
        SessionDocumentSummary: 一个包含会话文档摘要信息的响应体。

    Raises:
        HTTPException (401): 用户认证失败。
        HTTPException (500): 查询数据库时发生内部错误。
    """
    try:
        user_id = str(credentials.subject.get("user_id"))
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        # 检查是否有上传的文档
        has_documents = DocumentUploadService.has_uploaded_documents(db, session_id)
        
        # 获取最新的文档信息
        latest_document = DocumentUploadService.get_latest_document(db, session_id)
        
        # 获取总文档数量
        all_documents = DocumentUploadService.get_session_documents(db, session_id)
        total_documents = len(all_documents)
        
        return SessionDocumentSummary(
            session_id=session_id,
            has_documents=has_documents,
            latest_document_name=latest_document.document_name if latest_document else None,
            latest_document_type=latest_document.document_type if latest_document else None,
            latest_upload_time=latest_document.upload_time if latest_document else None,
            total_documents=total_documents
        )
        
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(f"获取会话文档摘要失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )