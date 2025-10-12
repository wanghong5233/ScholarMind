from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Body, BackgroundTasks
from sqlalchemy.orm import Session
from models.user import User
from schemas.document import DocumentInDB, DocumentUpdate, DocumentCreate
from service.auth import get_current_user
from service import document_service
from service.ingestion_service import ingestion_service
from service.job_service import job_service
from models.job import JobType, JobStatus
from schemas.job import JobInDB
from utils.database import get_db
from exceptions.base import ResourceNotFoundException, PermissionDeniedException, APIException
from pydantic import BaseModel
from typing import List as _List
from fastapi import UploadFile, File
from service.core.api.utils.file_storage import FileStorageUtil
from service.job_runner_service import execute_job
from service.job_handler.online_ingestion_handler import OnlineIngestionHandler
from service.job_handler.local_upload_handler import LocalUploadHandler
from service import knowledgebase_service
from core.config import settings
from utils.quota import quota


router = APIRouter()

# DTO for online search request body
class OnlineSearchRequest(BaseModel):
    query: str
    limit: int = 100
    year: str = ""

# DTO for add-online request body
class AddOnlineDocumentsRequest(BaseModel):
    documents: List[DocumentCreate]
@router.post(
    "/upload",
    response_model=JobInDB,
    summary="本地上传文档（异步）",
    description="接收多文件上传，创建后台任务进行去重、持久化与落盘。"
)
def upload_documents(
    kb_id: int,
    background_tasks: BackgroundTasks,
    files: _List[UploadFile] = File(None),
    file_single: UploadFile | None = File(None, alias="file"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 守卫调用：在执行任何操作之前，先验证知识库是否存在且用户有权访问
    try:
        knowledgebase_service.get_kb_by_id(db=db, kb_id=kb_id, user_id=current_user.id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    # 兼容多种前端字段：files（多）或 file（单）
    up_files: _List[UploadFile] = []
    if file_single is not None:
        up_files.append(file_single)
    if files:
        up_files.extend(files)
    if not up_files:
        raise HTTPException(status_code=400, detail="No files provided")
    # 基础安全校验：仅允许常见学术格式
    allowed_exts = {".pdf", ".docx", ".txt"}
    invalid = [f.filename for f in up_files if f and f.filename and (not any(f.filename.lower().endswith(ext) for ext in allowed_exts))]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported file types: {', '.join(invalid)}")

    # 先将文件保存为临时文件并计算哈希，限制最大体积，避免传递 UploadFile
    metas = []
    errors = []
    for f in up_files:
        try:
            metas.append(FileStorageUtil.save_upload_temp(f, kb_id))
        except ValueError as ve:
            errors.append({"filename": f.filename, "error": str(ve)})
        except Exception as e:
            errors.append({"filename": f.filename, "error": "save failed"})
    if metas and errors:
        # 部分失败也创建任务，但将失败项写入 payload.resultDetails，任务最终可能为 partial
        pass
    if not metas and errors:
        # 全部失败
        raise HTTPException(status_code=413, detail={"message": "All files rejected", "errors": errors})

    # 配额检查：按用户每日上传字节额度
    try:
        total_bytes = sum(int(m.get("size", "0")) for m in metas)
    except Exception:
        total_bytes = 0
    day_key = f"upload:bytes:day:{current_user.id}:{int(__import__('time').time())//86400}"
    if not quota.consume_bytes(day_key, amount=total_bytes, limit=settings.DAILY_UPLOAD_MB * 1024 * 1024, window_seconds=86400):
        # 清理已保存的临时文件
        for m in metas:
            p = m.get("temp_path")
            try:
                if p:
                    import os
                    if os.path.isfile(p):
                        os.remove(p)
            except Exception:
                continue
        raise HTTPException(status_code=429, detail="Daily upload quota exceeded")

    job = job_service.create_job(
        db,
        user_id=current_user.id,
        kb_id=kb_id,
        type=JobType.UPLOAD_LOCAL.value,
        payload={"files": metas, "precheckErrors": errors},
    )

    background_tasks.add_task(
        execute_job,
        job_id=job.id,
        handler_cls=LocalUploadHandler,
    )
    return job

@router.post(
    "/ingest/search-online",
    response_model=List[DocumentCreate],
    summary="在线检索学术论文",
    description="根据关键词从 Semantic Scholar 检索论文，返回待确认的论文列表。"
)
def search_online(
    kb_id: int,
    request: OnlineSearchRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 守卫调用：在执行任何操作之前，先验证知识库是否存在且用户有权访问
    try:
        knowledgebase_service.get_kb_by_id(db=db, kb_id=kb_id, user_id=current_user.id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    try:
        papers = ingestion_service.search_online_papers(
            query=request.query,
            limit=request.limit,
            year=request.year,
            db=db,
            user_id=current_user.id,
            kb_id=kb_id
        )
        return papers
    except APIException as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post(
    "/ingest/add-online",
    response_model=JobInDB,
    summary="异步添加在线检索的论文到知识库",
    description="创建后台任务：持久化并去重所选论文，并尝试下载PDF。返回Job以便轮询进度。"
)
def add_online_documents(
    kb_id: int,
    background_tasks: BackgroundTasks,
    payload: AddOnlineDocumentsRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 守卫调用：在执行任何操作之前，先验证知识库是否存在且用户有权访问
    try:
        knowledgebase_service.get_kb_by_id(db=db, kb_id=kb_id, user_id=current_user.id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    # 将文档转换为 JSON 可序列化的字典（Enum -> str）
    docs_payload = []
    for d in payload.documents:
        data = d.model_dump()
        src = data.get("ingestion_source")
        if hasattr(src, "value"):
            data["ingestion_source"] = src.value
        docs_payload.append(data)

    job = job_service.create_job(
        db,
        user_id=current_user.id,
        kb_id=kb_id,
        type=JobType.INGEST_ONLINE.value,
        payload={"documents": docs_payload},
    )

    # 异步执行任务（后台）
    background_tasks.add_task(
        execute_job,
        job_id=job.id,
        handler_cls=OnlineIngestionHandler,
    )
    # 立即返回 Job（pending），客户端可轮询 `/api/jobs/{id}`
    return job


@router.get(
    "/",
    response_model=List[DocumentInDB],
    summary="获取知识库中的所有文档",
    description="获取指定知识库下的所有文档列表。"
)
def list_documents(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        documents = document_service.list_documents_by_kb_id(db, kb_id, current_user.id)
        return documents
    except ResourceNotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedException as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.patch(
    "/{doc_id}",
    response_model=DocumentInDB,
    summary="更新文档元数据",
    description="更新指定文档的元数据信息。"
)
def update_document_metadata(
    kb_id: int,
    doc_id: int,
    doc_update: DocumentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        # kb_id is used for both permission checks and filtering
        updated_document = document_service.update_document(db, doc_id, doc_update, current_user.id, kb_id)
        return updated_document
    except ResourceNotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedException as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.delete(
    "/{doc_id}",
    response_model=DocumentInDB,
    summary="删除知识库中的文档",
    description="从知识库中删除指定的文档。"
)
def delete_document(
    kb_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        # kb_id is used for permission checks
        deleted_document = document_service.delete_document(db, doc_id, current_user.id, kb_id)
        return deleted_document
    except ResourceNotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedException as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
