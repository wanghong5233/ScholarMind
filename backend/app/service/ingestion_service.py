from typing import List
import os
from sqlalchemy.orm import Session
from schemas.document import DocumentCreate
from service.semantic_scholar_service import semantic_scholar_service
from exceptions.base import APIException
from service import document_service
from service.job_service import job_service
from utils.database import SessionLocal
from models.job import JobStatus
from schemas.document import DocumentCreate as DocumentCreateSchema
from service.core.api.utils.file_storage import FileStorageUtil
from models.document import Document
from models.job import JobType

class IngestionService:
    """
    一个统一的内容提取与处理服务。

    该服务负责编排从不同来源（如在线API、本地文件）获取内容，
    并将其处理、持久化到系统中的整个流程。
    """

    def search_online_papers(
        self, 
        query: str, 
        limit: int, 
        year: str,
        db: Session, 
        user_id: int, 
        kb_id: int
    ) -> List[DocumentCreate]:
        """
        在线检索论文并返回一个待确认的列表。

        这是在线导入流程的第一步。它只负责从外部API获取数据并进行转换，
        并不执行任何数据库写入操作。

        Args:
            query (str): 搜索关键词。
            limit (int): 数量限制。
            year (str): 年份范围。
            db (Session): 数据库会话 (当前未使用，为未来扩展保留)。
            user_id (int): 当前用户ID (当前未使用，为未来扩展保留)。
            kb_id (int): 目标知识库ID (当前未使用，为未来扩展保留)。

        Returns:
            List[DocumentCreate]: 从外部API检索并转换后的论文数据列表。
        
        Raises:
            APIException: 当外部API调用失败时。
        """
        try:
            # 1. 调用底层服务从外部API获取数据
            papers = semantic_scholar_service.search_papers(
                query=query, 
                limit=limit, 
                year=year
            )
            
            if papers is None: # None 表示请求最终失败
                 raise APIException("Failed to fetch papers from Semantic Scholar after multiple retries.")

            # 2. (未来步骤) 在这里可以增加对现有数据库的查重逻辑
            #    以标记出哪些论文已经是知识库的一部分。

            # 3. 返回转换后的数据列表给上层（API路由）
            return papers

        except Exception as e:
            # 捕获所有潜在异常，包括请求超时等，并封装为统一的API异常
            raise APIException(f"An error occurred during online paper search: {e}")

    def add_online_documents(
        self,
        db: Session,
        user_id: int,
        kb_id: int,
        documents: List[DocumentCreate]
    ):
        """
        将在线检索到并由用户勾选确认的论文，持久化到指定知识库。
        内部做去重处理，返回成功创建的文档。
        """
        try:
            created = document_service.create_documents_bulk_for_kb(
                db=db,
                kb_id=kb_id,
                user_id=user_id,
                documents=documents,
            )
            # 若未创建任何新文档，补充查找请求中对应的既有文档（用于触发下载补全）
            if not created:
                existing = document_service.find_existing_documents_for_payload(
                    db=db,
                    kb_id=kb_id,
                    documents=documents,
                )
                return existing
            return created
        except (APIException) as e:
            # 透传内部定义的统一异常
            raise e
        except Exception as e:
            raise APIException(f"Failed to add online documents: {e}")

    def download_pdfs_and_update(
        self,
        db: Session,
        user_id: int,
        kb_id: int,
        documents: List[Document]
    ) -> None:
        """
        为一组已持久化的文档尝试下载 PDF，成功则更新 local_pdf_path 与 file_hash。
        失败则忽略（保留元数据），不抛出导致整个流程失败的异常。
        """
        for doc in documents:
            if not doc.source_url:
                continue
            try:
                local_path, sha256 = FileStorageUtil.download_pdf(
                    url=doc.source_url,
                    kb_id=kb_id,
                    preferred_name=f"{doc.id}_{doc.title or 'paper'}"
                )
                doc.local_pdf_path = local_path
                doc.file_hash = sha256
                db.add(doc)
                db.commit()
                db.refresh(doc)
            except Exception:
                # 静默失败：记录层面保留 URL，后续可手动补齐
                db.rollback()
                continue

    def run_ingest_online_job(
        self,
        *,
        job_id: int,
        user_id: int,
        kb_id: int,
        documents_payload: List[dict],
    ) -> None:
        """
        后台执行在线导入任务：持久化所选文献并尝试下载 PDF，期间持续更新 Job 状态。
        使用独立的 DB 会话，避免与请求生命周期耦合。
        """
        db = SessionLocal()
        try:
            job_service.update_progress(
                db,
                job_id=job_id,
                user_id=user_id,
                status=JobStatus.RUNNING.value,
                progress=0,
            )

            documents = [DocumentCreateSchema(**d) for d in documents_payload]
            # 原子性改进：先创建记录（带去重），再尝试下载；对本次新建且下载失败的记录回滚删除
            created = document_service.create_documents_bulk_for_kb(
                db=db,
                kb_id=kb_id,
                user_id=user_id,
                documents=documents,
            )
            newly_created_ids = {d.id for d in created}
            existing = document_service.find_existing_documents_for_payload(db=db, kb_id=kb_id, documents=documents)
            processing_list = created + [e for e in existing if e.id not in newly_created_ids]

            total = len(processing_list)
            succeeded = 0
            failed = 0
            result_details = []

            for idx, doc in enumerate(processing_list, start=1):
                try:
                    self.download_pdfs_and_update(
                        db=db,
                        user_id=user_id,
                        kb_id=kb_id,
                        documents=[doc],
                    )
                    succeeded += 1
                    result_details.append({
                        "doc_id": doc.id,
                        "title": doc.title,
                        "status": "ok",
                        "local_pdf_path": doc.local_pdf_path,
                    })
                except Exception as e:
                    failed += 1
                    # 对于本次新建的记录，如果下载失败则回滚删除，保证原子性
                    if doc.id in newly_created_ids:
                        try:
                            db.delete(doc)
                            db.commit()
                        except Exception:
                            db.rollback()
                    result_details.append({
                        "doc_id": doc.id,
                        "title": doc.title,
                        "status": "failed",
                        "error": str(e),
                    })
                finally:
                    job_service.update_progress(
                        db,
                        job_id=job_id,
                        user_id=user_id,
                        progress=int(idx * 100 / max(total, 1)),
                        total=total,
                        succeeded=succeeded,
                        failed=failed,
                    )

            final_status = (
                JobStatus.SUCCESS.value
                if failed == 0
                else (JobStatus.PARTIAL.value if succeeded > 0 else JobStatus.FAILED.value)
            )
            job_service.update_progress(
                db,
                job_id=job_id,
                user_id=user_id,
                status=final_status,
                progress=100,
                total=total,
                succeeded=succeeded,
                failed=failed,
            )
            # 追加结果明细到 payload
            job = job_service.get_job(db, job_id=job_id, user_id=user_id)
            payload = job.payload or {}
            payload["resultDetails"] = result_details
            job.payload = payload
            db.add(job)
            db.commit()
            # 占位：自动创建解析/索引任务（后续替换为真实实现）
            try:
                parse_job = job_service.create_job(
                    db,
                    user_id=user_id,
                    kb_id=kb_id,
                    type=JobType.PARSE_INDEX.value,
                    payload={"fromJobId": job_id, "docs": [d.id for d in created]},
                )
                self.run_parse_index_job(job_id=parse_job.id, user_id=user_id, kb_id=kb_id, doc_ids=[d.id for d in created])
            except Exception:
                pass
        except Exception as e:
            job_service.update_progress(
                db,
                job_id=job_id,
                user_id=user_id,
                status=JobStatus.FAILED.value,
                error=str(e),
            )
        finally:
            db.close()

    def run_upload_local_job(
        self,
        *,
        job_id: int,
        user_id: int,
        kb_id: int,
        files: List[dict],
    ) -> None:
        """
        后台执行本地上传任务：
        - 保存为临时文件并计算哈希
        - 基于 file_hash 去重；不存在则创建文档并移动到最终路径
        - 更新 Job 进度与明细
        """
        db = SessionLocal()
        try:
            job_service.update_progress(
                db,
                job_id=job_id,
                user_id=user_id,
                status=JobStatus.RUNNING.value,
                progress=0,
            )

            total = len(files)
            succeeded = 0
            failed = 0
            result_details = []

            for idx, f in enumerate(files, start=1):
                try:
                    meta = f  # 已在路由层保存到临时文件
                    from service.document_service import find_document_by_file_hash, create_documents_bulk_for_kb
                    # 去重
                    dup = find_document_by_file_hash(db, kb_id, meta["sha256"])
                    if dup:
                        result_details.append({
                            "filename": meta.get("original_name"),
                            "status": "duplicate",
                            "doc_id": dup.id,
                        })
                        # 清理临时文件
                        try:
                            if meta.get("temp_path"):
                                os.remove(meta["temp_path"])
                        except Exception:
                            pass
                        succeeded += 1
                    else:
                        # 先创建文档记录（占位，带 file_hash 和 ingestion_source）
                        doc_schema = DocumentCreateSchema(
                            title=meta["original_name"],
                            authors=None,
                            abstract=None,
                            publication_year=None,
                            journal_or_conference=None,
                            keywords=None,
                            citation_count=None,
                            fields_of_study=None,
                            doi=None,
                            semantic_scholar_id=None,
                            source_url=None,
                            local_pdf_path=None,
                            file_hash=meta["sha256"],
                            ingestion_source="local_upload",
                        )
                        created = create_documents_bulk_for_kb(
                            db=db,
                            kb_id=kb_id,
                            user_id=user_id,
                            documents=[doc_schema],
                        )
                        doc = created[0] if created else find_document_by_file_hash(db, kb_id, meta["sha256"])  # 并发兜底
                        final_path = FileStorageUtil.move_temp_to_final(meta["temp_path"], kb_id, doc.id, meta.get("original_name"))
                        doc.local_pdf_path = final_path
                        db.add(doc)
                        db.commit()
                        db.refresh(doc)
                        # 移动成功后删除 tmp 已由 os.replace 完成；这里仅记录结果
                        result_details.append({
                            "filename": meta.get("original_name"),
                            "status": "ok",
                            "doc_id": doc.id,
                            "local_path": final_path,
                        })
                        succeeded += 1
                except Exception as e:
                    failed += 1
                    result_details.append({
                        "filename": meta.get("original_name", "unknown"),
                        "status": "failed",
                        "error": str(e),
                    })
                    # 失败时尽力清理临时文件
                    try:
                        if meta.get("temp_path") and os.path.exists(meta["temp_path"]):
                            os.remove(meta["temp_path"])
                    except Exception:
                        pass
                finally:
                    job_service.update_progress(
                        db,
                        job_id=job_id,
                        user_id=user_id,
                        progress=int(idx * 100 / max(total, 1)),
                        total=total,
                        succeeded=succeeded,
                        failed=failed,
                    )

            final_status = (
                JobStatus.SUCCESS.value
                if failed == 0
                else (JobStatus.PARTIAL.value if succeeded > 0 else JobStatus.FAILED.value)
            )
            job_service.update_progress(
                db,
                job_id=job_id,
                user_id=user_id,
                status=final_status,
                progress=100,
                total=total,
                succeeded=succeeded,
                failed=failed,
            )

            job = job_service.get_job(db, job_id=job_id, user_id=user_id)
            payload = job.payload or {}
            payload["resultDetails"] = result_details
            job.payload = payload
            db.add(job)
            db.commit()
            # 占位：自动创建解析/索引任务
            try:
                created_doc_ids = [d.get("doc_id") for d in result_details if d.get("status") in ("ok", "duplicate") and d.get("doc_id")]
                if created_doc_ids:
                    parse_job = job_service.create_job(
                        db,
                        user_id=user_id,
                        kb_id=kb_id,
                        type=JobType.PARSE_INDEX.value,
                        payload={"fromJobId": job_id, "docs": created_doc_ids},
                    )
                    self.run_parse_index_job(job_id=parse_job.id, user_id=user_id, kb_id=kb_id, doc_ids=created_doc_ids)
            except Exception:
                pass
        except Exception as e:
            job_service.update_progress(
                db,
                job_id=job_id,
                user_id=user_id,
                status=JobStatus.FAILED.value,
                error=str(e),
            )
        finally:
            db.close()

    def run_parse_index_job(self, *, job_id: int, user_id: int, kb_id: int, doc_ids: List[int]) -> None:
        """
        占位：解析/分块/向量化/索引。
        当前仅更新 Job 状态为 success，并记录 doc_ids。后续替换为真实流水线。
        """
        db = SessionLocal()
        try:
            job_service.update_progress(db, job_id=job_id, user_id=user_id, status=JobStatus.RUNNING.value, progress=0)
            # TODO: 调用真实解析与索引流水线
            job_service.update_progress(db, job_id=job_id, user_id=user_id, status=JobStatus.SUCCESS.value, progress=100)
            job = job_service.get_job(db, job_id=job_id, user_id=user_id)
            payload = job.payload or {}
            payload["parsedDocs"] = doc_ids
            job.payload = payload
            db.add(job)
            db.commit()
        except Exception as e:
            job_service.update_progress(db, job_id=job_id, user_id=user_id, status=JobStatus.FAILED.value, error=str(e))
        finally:
            db.close()


# 实例化服务
ingestion_service = IngestionService()
