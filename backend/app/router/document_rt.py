from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from models.user import User
from schemas.document import DocumentInDB, DocumentUpdate, DocumentCreate
from service.auth import get_current_user
from service import document_service
from service.ingestion_service import ingestion_service
from utils.database import get_db
from exceptions.base import ResourceNotFoundException, PermissionDeniedException, APIException
from pydantic import BaseModel

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
    response_model=List[DocumentInDB],
    summary="添加在线检索的论文到知识库",
    description="接收前端勾选确认的论文元数据，执行持久化并去重后返回新增文档"
)
def add_online_documents(
    kb_id: int,
    payload: AddOnlineDocumentsRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        created = ingestion_service.add_online_documents(
            db=db,
            user_id=current_user.id,
            kb_id=kb_id,
            documents=payload.documents,
        )
        # 下载 PDF（尽力而为），并更新本地路径/哈希
        ingestion_service.download_pdfs_and_update(
            db=db,
            user_id=current_user.id,
            kb_id=kb_id,
            documents=created,
        )
        return created
    except APIException as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except PermissionDeniedException as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ResourceNotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


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
