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


# 实例化服务
ingestion_service = IngestionService()
