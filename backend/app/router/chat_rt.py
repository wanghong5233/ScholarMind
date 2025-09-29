from fastapi import APIRouter, Body, UploadFile, File, HTTPException, Query, Security, status, Depends
import uuid
from schemas.chat import SessionResponse, ChatRequest
from fastapi.responses import StreamingResponse
import os
from dotenv import load_dotenv
from typing import List, Optional
from service.core.file_parse import execute_insert_process
from service.core.api.utils.file_utils import get_project_base_directory
from fastapi_jwt import JwtAuthorizationCredentials
from service.core.retrieval import retrieve_content
from service.core.chat import get_chat_completion
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

# 加载 .env 文件
load_dotenv()

# 配置日志
logger.info(f"ES_HOST: {os.getenv('ES_HOST')}")
logger.info(f"ELASTICSEARCH_URL: {os.getenv('ELASTICSEARCH_URL')}")

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
# 基于ragflow知识库对话
##################################

@router.post("/chat_on_docs")
async def chat_on_docs(
    session_id: str = Query(...),
    request: ChatRequest = Body(..., description="User message"),
    credentials: JwtAuthorizationCredentials = Security(access_security),
):
    """
    执行一次完整的RAG（检索增强生成）问答流程。

    这是应用的核心功能接口。它接收用户的问题，首先从与该用户关联的
    知识库中检索最相关的信息片段，然后将问题和这些信息片段一同发送给
    大语言模型（LLM）以生成最终答案。

    Args:
        session_id (str): 作为查询参数传入的当前会话ID。
        request (ChatRequest): 请求体，包含用户发送的 `message`。
        credentials (JwtAuthorizationCredentials): 依赖注入，用于用户认证。

    Returns:
        StreamingResponse: 一个事件流（event-stream）响应。LLM生成的答案会
            被实时地、一小块一小块地推送给客户端，实现打字机效果。

    Raises:
        HTTPException (401): 用户认证失败。
        HTTPException (500): 在检索或与LLM通信过程中发生内部错误。
    """
    try:
        user_id = str(credentials.subject.get("user_id"))
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        
        logger.info(f"开始处理用户 {user_id} 的请求")
        logger.info(f"问题内容: {request.message}")
        
        question = request.message
        
        # 尝试从知识库检索内容，如果没有知识库也不报错
        references = []
        try:
            logger.info("开始检索相关内容...")
            references = retrieve_content(user_id, question)
            logger.info(f"检索到 {len(references)} 条相关内容")
        except Exception as e:
            logger.info(f"用户 {user_id} 没有知识库或检索失败: {str(e)}，将不使用知识库内容")
            references = []

        logger.info("开始生成回答...")
        # 返回流式响应
        return StreamingResponse(
            get_chat_completion(session_id, question, references, user_id),
            media_type="text/event-stream"
        )
    
    except HTTPException as e:
        logger.error(f"HTTP错误: {str(e)}")
        raise e
    except Exception as e:
        logger.exception(f"发生未知错误: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/upload_files")
async def upload_files(
    session_id: Optional[str] = Query(None),
    files: List[UploadFile] = File(...),
    credentials: JwtAuthorizationCredentials = Security(access_security),
    db: Session = Depends(get_db)
):
    """
    上传一个或多个文件，以构建或扩充用户的个人知识库。

    这是一个重量级操作。接口会接收文件，进行严格的校验，然后将其
    保存并触发一个后台处理流程，该流程会对文件进行深度解析、文本分块、
    向量化，并最终将结果索引到向量数据库（如Elasticsearch）和关系型
    数据库中，使其可用于RAG检索。

    Args:
        session_id (Optional[str]): 关联的会话ID（可选）。如果未提供，
            将使用用户ID作为默认的知识库归属标识。
        files (List[UploadFile]): 一个包含一个或多个待上传文件的列表。
        credentials (JwtAuthorizationCredentials): 依赖注入，用于用户认证。
        db (Session): 依赖注入，提供数据库会话以进行文件名查重等操作。

    Returns:
        dict: 一个处理报告，详细说明了哪些文件成功上传，哪些失败，以及失败原因。

    Raises:
        HTTPException:
            - 400: 上传的文件已存在、文件内容为空或格式损坏、所有文件均处理失败等。
            - 401: 用户认证失败。
            - 500: 服务器在处理文件时发生未预料的内部错误。
    """
    try:
        user_id = str(credentials.subject.get("user_id"))
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        # 如果没有 session_id，使用 user_id 作为 session_id
        if session_id is None:
            session_id = user_id

        # 确保 storage/file 文件夹存在
        storage_dir = os.path.join(get_project_base_directory(), "storage/file")
        if not os.path.exists(storage_dir):
            os.makedirs(storage_dir)
        
        # 根据 session_id 创建子文件夹
        session_dir = os.path.join(storage_dir, session_id)
        if not os.path.exists(session_dir):
            os.makedirs(session_dir)
        
        # 检查文件名是否重复
        existing_files = []
        for file in files:
            file_name = file.filename
            # 查询数据库中是否已存在该文件名
            stmt = select(KnowledgeBase).where(
                KnowledgeBase.user_id == user_id,
                KnowledgeBase.file_name == file_name
            )
            existing_file = db.execute(stmt).scalar_one_or_none()
            if existing_file:
                existing_files.append(file_name)
        
        if existing_files:
            raise HTTPException(
                status_code=400,
                detail=f"以下文件已存在，请勿重复上传: {', '.join(existing_files)}"
            )

        # 处理文件上传
        successful_files = []
        failed_files = []
        
        for file in files:
            file_name = file.filename
            file_path = os.path.join(session_dir, file_name)
            
            try:
                # 读取文件内容
                file_content = await file.read()
                
                # 验证文件内容不为空
                if not file_content:
                    failed_files.append(f"{file_name}: 文件内容为空")
                    continue
                
                # 对于 Excel 文件，进行额外验证
                if file_name.lower().endswith(('.xlsx', '.xls')):
                    # 检查文件头，xlsx 文件应该是 ZIP 格式
                    if file_name.lower().endswith('.xlsx'):
                        # XLSX 文件应该以 PK 开头（ZIP 文件头）
                        if not file_content.startswith(b'PK'):
                            failed_files.append(f"{file_name}: 不是有效的 XLSX 文件格式，可能是 XLS 文件")
                            continue
                    elif file_name.lower().endswith('.xls'):
                        # XLS 文件有特定的文件头
                        if not (file_content.startswith(b'\xd0\xcf\x11\xe0') or 
                               file_content.startswith(b'\x09\x08')):
                            failed_files.append(f"{file_name}: 不是有效的 XLS 文件格式")
                            continue
                
                # 保存文件到本地
                with open(file_path, "wb") as buffer:
                    buffer.write(file_content)
                
                # 验证文件大小
                if os.path.getsize(file_path) != len(file_content):
                    failed_files.append(f"{file_name}: 文件保存失败，大小不匹配")
                    # 修正：清理因写入不完整而残留在磁盘上的文件
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    continue
                
                # 保存文件 URL 和 Base64 编码的文件流
                file_url = f"{storage_dir}/{session_id}/{file_name}"
                logger.info(f"Processing file: {file_url}")

                # 尝试解析和插入文档
                try:
                    execute_insert_process(file_url, file_name, session_id)
                    logger.info(f"数据插入es成功: {file_name}")
                    
                    insert_knowledgebase(user_id, file_name)
                    logger.info(f"数据插入pg成功: {file_name}")
                    
                    successful_files.append(file_name)
                    
                except Exception as parse_error:
                    logger.error(f"文件解析失败 {file_name}: {str(parse_error)}")
                    failed_files.append(f"{file_name}: 文件解析失败 - {str(parse_error)}")
                    # 删除已保存的文件
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    continue
                        
            except Exception as e:
                logger.error(f"处理文件失败 {file_name}: {str(e)}")
                failed_files.append(f"{file_name}: 处理失败 - {str(e)}")
                continue

        # 构建返回结果
        if successful_files and not failed_files:
            return {
                "status": "success",
                "message": "所有文件解析成功",
                "successful_files": successful_files,
                "total_files": len(files)
            }
        elif successful_files and failed_files:
            return {
                "status": "partial_success",
                "message": f"部分文件解析成功，{len(successful_files)} 个成功，{len(failed_files)} 个失败",
                "successful_files": successful_files,
                "failed_files": failed_files,
                "total_files": len(files)
            }
        else:
            raise HTTPException(
                status_code=400,
                detail={
                    "status": "failed",
                    "message": "所有文件解析失败",
                    "failed_files": failed_files,
                    "total_files": len(files)
                }
            )
    
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
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