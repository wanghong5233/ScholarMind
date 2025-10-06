from typing import List
from sqlalchemy.orm import Session
from schemas.document import DocumentCreate
from service.semantic_scholar_service import semantic_scholar_service
from exceptions.base import APIException
from service import document_service
from service.core.api.utils.file_storage import FileStorageUtil
from models.document import Document

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


# 实例化服务
ingestion_service = IngestionService()
