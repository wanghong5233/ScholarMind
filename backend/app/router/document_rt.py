from typing import List
from collections import Counter
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Body, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import os
from urllib.parse import quote
from models.user import User
from schemas.document import (
    DocumentInDB,
    DocumentUpdate,
    DocumentCreate,
    CriticalQuestionsResponse,
    DocumentParsePreviewResponse,
    DocumentParseStats,
    DocumentParseBlock,
    DocumentParseStage,
)
from service.auth import get_current_user, get_current_user_optional_query_token
from service import document_service
from service.core.ingestion.parser_orchestrator import ParserOrchestrator
from service.core.ingestion.chunker import RecursiveCharacterChunker
from service.core.ingestion.structured_doc_builder import StructuredDocumentBuilder, StructuredDocument
from service.core.ingestion.constants import is_multimodal_metadata
from service.core.rag.utils.es_conn import ESConnection
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
from service.core.rag.service import RAGService
from utils.rate_limiter import rate_limiter
from utils.ask_logger import AskEventLogger
from service import document_service


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
        parser_pipeline = getattr(settings, "SM_PARSER_ORDER", "")
        enriched: List[DocumentInDB] = []
        for doc in documents:
            model = DocumentInDB.model_validate(doc)
            enriched.append(model.model_copy(update={"parser_pipeline": parser_pipeline}))
        return enriched
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


@router.get(
    "/{doc_id}/critical_questions",
    response_model=CriticalQuestionsResponse,
    summary="批判性问题生成",
    description="基于指定文档进行聚焦检索，生成若干高价值批判性问题（不调用 LLM 生成答案，仅输出问题）。"
)
def generate_critical_questions(
    kb_id: int,
    doc_id: int,
    top_n: int = 6,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 权限校验：KB 与 文档归属
    try:
        knowledgebase_service.get_kb_by_id(db=db, kb_id=kb_id, user_id=current_user.id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    try:
        document_service.get_document_by_id(db=db, doc_id=doc_id, user_id=current_user.id, kb_id=kb_id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    # 频控与配额
    bucket = f"criticalq:{current_user.id}:{kb_id}:{doc_id}"
    if not rate_limiter.check_and_consume(bucket, limit=60, window_seconds=60):
        raise HTTPException(status_code=429, detail="Too Many Requests")
    day_key = f"criticalq:day:{current_user.id}:{int(__import__('time').time())//86400}"
    if not quota.consume_count(day_key, settings.DAILY_ASK_COUNT, window_seconds=86400):
        raise HTTPException(status_code=429, detail="Daily quota exceeded")

    # 构造 meta-prompt：聚焦在文档的主要贡献、方法、实验、局限与未来工作
    dims = [
        "核心问题与动机",
        "关键方法与假设",
        "实验设计与数据",
        "主要结论与局限",
        "与相关工作比较",
        "潜在扩展与开放问题",
    ]
    n = max(1, min(int(top_n), len(dims)))
    question_intro = (
        "请基于以下论文内容，面向深入阅读提出" + str(n) + "个批判性问题，要求具体、可操作，避免泛泛而谈。"
    )
    # 使用检索聚焦该文档
    rag = RAGService()
    query = "; ".join(dims[:n])
    chunks = rag.retrieve(query=query, kb_id=kb_id, top_k=max(8, n*4), focus_doc_ids=[doc_id], index_override=None)
    # 生成问题列表
    prompt = (
        question_intro + "\n请按序号给出：1) 问题描述 2) 指向性提示（可引用 [文档ID:页码]）。"
    )
    content = rag.generate(question=prompt, chunks=chunks, stream=False, history=[], compress_history=False)
    # 将生成文本切分为问题列表（鲁棒：按行或按数字序号分割）
    text = (content or "").strip()
    lines = [x.strip(" -•\t").strip() for x in text.splitlines() if x.strip()]
    # 简单规整：若为长段落，则按 '1.' '2.' 等编号分割
    if len(lines) <= 2:
        import re as _re
        parts = _re.split(r"(?:^|\n)\s*(?:\d+\.|\d+、|\(\d+\))\s*", text)
        lines = [p.strip() for p in parts if p and p.strip()]
    questions = lines[:n]
    citations = rag.build_citations(chunks)
    debug = {"doc_id": doc_id, "dims": dims[:n], "retrieval": rag.get_last_retrieval_debug() or {}}
    # 观测日志
    try:
        AskEventLogger().log_event({
            "user_id": str(current_user.id),
            "session_id": None,
            "kb_id": int(kb_id),
            "question": prompt[:512],
            "top_k": len(chunks),
            "strategy": getattr(settings, "SM_RETRIEVAL_STRATEGY", "multi_stage"),
            "hits": len(chunks),
            "retrieval": rag.get_last_retrieval_debug() or {},
            "citations": citations,
            "usage": rag.get_last_usage() or {},
            "answer_chars": len(content or ""),
            "variant": "critical_questions",
        })
    except Exception:
        pass

    return CriticalQuestionsResponse(questions=questions, citations=citations, debug=debug)


@router.get(
    "/{doc_id}/parse-preview",
    response_model=DocumentParsePreviewResponse,
    summary="文档解析预览（测试用）",
    description="运行解析管线并返回完整的解析块与统计信息，便于调试解析质量。"
)
def preview_document_parse(
    kb_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    logger = logging.getLogger(__name__)

    def _convert_parsed_blocks(parsed_blocks):
        converted: List[DocumentParseBlock] = []
        for idx, block in enumerate(parsed_blocks, start=1):
            metadata = dict(block.metadata or {})
            converted.append(
                DocumentParseBlock(
                    index=idx,
                    text=block.text or "",
                    element_type=metadata.get("element_type"),
                    page=metadata.get("page"),
                    metadata=metadata,
                )
            )
        return converted

    def _stats_from_blocks(blocks: List[DocumentParseBlock]) -> DocumentParseStats:
        element_counter: Counter[str] = Counter()
        parser_counter: Counter[str] = Counter()
        nonempty_blocks = 0
        total_chars = 0
        for block in blocks:
            text = block.text or ""
            if text.strip():
                nonempty_blocks += 1
                total_chars += len(text)
            element_counter[str(block.element_type or "unknown")] += 1
            parser_counter[str(block.metadata.get("parser_engine") or "unknown")] += 1
        return DocumentParseStats(
            total_blocks=len(blocks),
            nonempty_blocks=nonempty_blocks,
            total_chars=total_chars,
            element_types=dict(element_counter),
            parser_engines=dict(parser_counter),
        )

    def _build_stage(key: str, title: str, description: str | None, blocks: List[DocumentParseBlock]) -> DocumentParseStage:
        return DocumentParseStage(
            key=key,
            title=title,
            description=description,
            stats=_stats_from_blocks(blocks),
            blocks=blocks,
        )

    def _convert_structured_blocks(structured_doc: StructuredDocument) -> List[DocumentParseBlock]:
        converted: List[DocumentParseBlock] = []
        for idx, block in enumerate(structured_doc.blocks, start=1):
            metadata = dict(block.metadata or {})
            metadata.update(
                {
                    "logical_type": block.logical_type,
                    "structure_path": block.structure_path,
                    "structure_level": block.level,
                    "structure_title": block.title,
                }
            )
            pages = metadata.get("page_range") or []
            page_val = pages[0] if pages else None
            original_element = metadata.get("element_type")
            if original_element != block.logical_type:
                if original_element:
                    metadata.setdefault("original_element_type", original_element)
            metadata["element_type"] = block.logical_type
            element_type = metadata["element_type"]
            converted.append(
                DocumentParseBlock(
                    index=idx,
                    text=block.text,
                    element_type=element_type or block.logical_type,
                    page=page_val,
                    metadata=metadata,
                )
            )
        return converted

    def _load_indexed_blocks(kb: int, document: int) -> List[DocumentParseBlock]:
        size = int(getattr(settings, "SM_DEBUG_MAX_CHUNKS", 2000) or 2000)
        try:
            es = ESConnection()
        except Exception as exc:
            logger.error(f"Failed to init ESConnection for chunk preview: {exc}")
            return []
        query = {
            "size": size,
            "query": {
                "bool": {
                    "must": [
                        {"term": {"kb_id": str(kb)}},
                        {"term": {"document_id": str(document)}},
                    ]
                }
            },
            "sort": [
                {"chunk_index": {"order": "asc", "missing": "_first", "unmapped_type": "integer"}},
                {"_doc": {"order": "asc"}},
            ],
            "_source": {
                "excludes": ["vector"]
            },
        }
        try:
            res = es.es.search(index=settings.ES_DEFAULT_INDEX, body=query)
        except Exception as exc:
            logger.error(f"ES search failed for chunk preview doc={document}: {exc}")
            return []
        hits = res.get("hits", {}).get("hits", [])
        blocks: List[DocumentParseBlock] = []
        for idx, hit in enumerate(hits, start=1):
            source = hit.get("_source", {}) or {}
            metadata = {k: v for k, v in source.items() if k not in {"text", "vector"}}
            metadata["chunk_id"] = hit.get("_id")
            if (not settings.SM_ENABLE_MULTIMODAL_CHUNKS) and is_multimodal_metadata(metadata):
                continue
            if not metadata.get("element_type") and metadata.get("logical_type"):
                metadata["element_type"] = metadata["logical_type"]
            metadata = {k: v for k, v in metadata.items() if v is not None}
            page_val = source.get("page")
            page_num = None
            try:
                if page_val is not None:
                    page_num = int(page_val)
            except (TypeError, ValueError):
                page_num = None
            blocks.append(
                DocumentParseBlock(
                    index=idx,
                    text=source.get("text") or "",
                    element_type=source.get("element_type"),
                    page=page_num,
                    metadata=metadata,
                )
            )
        return blocks

    # 权限校验
    try:
        knowledgebase_service.get_kb_by_id(db=db, kb_id=kb_id, user_id=current_user.id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    try:
        doc = document_service.get_document_by_id(db, doc_id, current_user.id, kb_id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    file_path = doc.local_pdf_path
    if not file_path:
        raise HTTPException(status_code=404, detail="文档未关联本地文件，无法解析。")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")

    orchestrator = ParserOrchestrator()
    try:
        blocks = orchestrator.parse(file_path=file_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析失败: {e}")

    parser_blocks = _convert_parsed_blocks(blocks)
    parser_stage = _build_stage(
        key="parser",
        title="解析输出",
        description="解析器（MinerU/Unstructured/PyMuPDF）返回的原始块。",
        blocks=parser_blocks,
    )

    structured_builder = StructuredDocumentBuilder()
    structured_doc = structured_builder.build(document=doc, mineru_blocks=blocks)
    structured_blocks = _convert_structured_blocks(structured_doc)
    structured_stage = _build_stage(
        key="structured",
        title="结构化输出",
        description="Grobid + MinerU 对齐后的结构块（带章节、页码和 bbox 信息）。",
        blocks=structured_blocks,
    )

    structured_parsed_blocks = structured_doc.to_parsed_blocks()

    chunker = RecursiveCharacterChunker()
    try:
        chunked_blocks_raw = chunker.chunk(blocks=structured_parsed_blocks)
    except Exception as exc:
        logger.error(f"Chunker execution failed for doc_id={doc_id}: {exc}")
        chunked_blocks_raw = []
    chunker_blocks = _convert_parsed_blocks(chunked_blocks_raw)
    chunker_stage = _build_stage(
        key="chunker",
        title="分块后输出",
        description="运行 Chunker/语义合并后的片段（入库前）。",
        blocks=chunker_blocks,
    )

    indexed_blocks = _load_indexed_blocks(kb_id, doc_id)
    indexed_stage = None
    if indexed_blocks:
        indexed_stage = _build_stage(
            key="indexed",
            title="实际入库 Chunk",
            description="从向量库读取的已入库 chunk 内容（排序按 chunk_index）。",
            blocks=indexed_blocks,
        )

    stages: List[DocumentParseStage] = [parser_stage, structured_stage, chunker_stage]
    if indexed_stage:
        stages.append(indexed_stage)

    parser_order = [name for name in (orchestrator.order or []) if name]
    primary_stats = stages[0].stats if stages else DocumentParseStats(
        total_blocks=0,
        nonempty_blocks=0,
        total_chars=0,
        element_types={},
        parser_engines={},
    )
    primary_blocks = stages[0].blocks if stages else []

    return DocumentParsePreviewResponse(
        document_id=doc.id,
        knowledge_base_id=doc.knowledge_base_id,
        filename=os.path.basename(file_path),
        parser_order=parser_order,
        stages=stages,
        stats=primary_stats,
        blocks=primary_blocks,
    )


@router.get(
    "/{doc_id}/preview",
    summary="预览/下载 PDF 文件",
    description="返回文档的 PDF 文件，可在浏览器中预览或下载"
)
def preview_document(
    kb_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional_query_token),
):
    """
    预览或下载文档的 PDF 文件
    - 权限校验：确保用户有权访问该知识库和文档
    - 返回 PDF 文件流，浏览器可直接预览
    """
    # 权限校验：KB 与文档归属
    try:
        knowledgebase_service.get_kb_by_id(db=db, kb_id=kb_id, user_id=current_user.id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    
    try:
        doc = document_service.get_document_by_id(db=db, doc_id=doc_id, user_id=current_user.id, kb_id=kb_id)
    except (ResourceNotFoundException, PermissionDeniedException) as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    
    # 获取文件路径
    file_path = doc.local_pdf_path
    if not file_path:
        raise HTTPException(status_code=404, detail="PDF file not found for this document")
    
    # 检查文件是否存在
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"PDF file does not exist at path: {file_path}")
    
    # 返回文件响应，设置 Content-Disposition 为 inline 以便浏览器预览
    # 如果想强制下载，可以改为 attachment
    filename = doc.title or f"document_{doc_id}.pdf"
    if not filename.endswith('.pdf'):
        filename += '.pdf'

    ascii_fallback = "".join(char if ord(char) < 128 else "_" for char in filename) or f"document_{doc_id}.pdf"
    if not ascii_fallback.endswith(".pdf"):
        ascii_fallback += ".pdf"

    content_disposition = f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
    
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition
        }
    )
