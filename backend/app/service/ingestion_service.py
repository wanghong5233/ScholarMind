from datetime import datetime
import math
import re
from typing import List, Optional
import os
from sqlalchemy.orm import Session
from schemas.document import DocumentCreate
from service.arxiv_service import arxiv_service
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
        kb_id: int,
        providers: Optional[List[str]] = None,
        rank_by: Optional[str] = None,
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
            providers (Optional[List[str]]): 检索来源列表（默认仅 Semantic Scholar）。
            rank_by (Optional[str]): 排序策略（relevance/recent/citations/hybrid）。

        Returns:
            List[DocumentCreate]: 从外部API检索并转换后的论文数据列表。
        
        Raises:
            APIException: 当外部API调用失败时。
        """
        try:
            provider_list = self._normalize_providers(providers)
            if not provider_list:
                papers = semantic_scholar_service.search_papers(
                    query=query,
                    limit=limit,
                    year=year,
                )

                if papers is None:  # None 表示请求最终失败
                    raise APIException(
                        message="Failed to fetch papers from Semantic Scholar after multiple retries."
                    )

                return papers

            results: List[DocumentCreate] = []
            errors: List[str] = []
            for provider in provider_list:
                if provider == "semantic_scholar":
                    try:
                        results.extend(
                            semantic_scholar_service.search_papers(
                                query=query,
                                limit=limit,
                                year=year,
                            )
                        )
                    except APIException as exc:
                        errors.append(f"semantic_scholar: {exc.message}")
                    except Exception as exc:
                        errors.append(f"semantic_scholar: {exc}")
                elif provider == "arxiv":
                    try:
                        results.extend(
                            arxiv_service.search_papers(
                                query=query,
                                limit=limit,
                                year=year,
                                rank_by=rank_by or "relevance",
                            )
                        )
                    except Exception as exc:
                        errors.append(f"arxiv: {exc}")

            if not results:
                if errors:
                    error_text = "; ".join(errors)
                    raise APIException(message=f"Online paper search failed: {error_text}")
                return []

            deduped = self._dedupe_results(results)
            ranked = self._rank_results(deduped, rank_by)
            return ranked[: max(1, int(limit or 1))]

        except APIException:
            raise
        except Exception as e:
            raise APIException(message=f"An error occurred during online paper search: {e}")

    @staticmethod
    def _normalize_providers(providers: Optional[List[str]]) -> List[str]:
        if not providers:
            return []
        if isinstance(providers, str):
            tokens = [item.strip().lower() for item in re.split(r"[,\s]+", providers) if item.strip()]
            return [p for p in tokens if p in {"semantic_scholar", "arxiv"}]
        normalized = []
        for item in providers:
            if not isinstance(item, str):
                continue
            value = item.strip().lower()
            if value in {"semantic_scholar", "arxiv"}:
                normalized.append(value)
        return normalized

    @staticmethod
    def _dedupe_results(results: List[DocumentCreate]) -> List[DocumentCreate]:
        deduped: List[DocumentCreate] = []
        seen: set[str] = set()
        for doc in results:
            key = IngestionService._paper_key(doc)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(doc)
        return deduped

    @staticmethod
    def _paper_key(doc: DocumentCreate) -> str:
        doi = (doc.doi or "").strip().lower()
        if doi:
            return doi
        semantic_id = (doc.semantic_scholar_id or "").strip().lower()
        if semantic_id:
            return semantic_id
        url = (doc.source_url or "").strip().lower()
        if url:
            return url
        title = (doc.title or "").strip().lower()
        title = re.sub(r"[^a-z0-9]+", " ", title).strip()
        return title or str(doc)

    @staticmethod
    def _rank_results(
        results: List[DocumentCreate], rank_by: Optional[str]
    ) -> List[DocumentCreate]:
        strategy = (rank_by or "relevance").lower()
        if strategy == "relevance":
            return results

        current_year = datetime.utcnow().year
        scored = []
        for idx, doc in enumerate(results):
            year = doc.publication_year
            recency = 0.0
            if isinstance(year, int):
                age = max(0, current_year - year)
                recency = 1.0 / (1.0 + age)
            citations = doc.citation_count or 0
            citation_score = math.log1p(max(0, int(citations))) / math.log1p(500)
            quality = 0.1 if getattr(doc, "highLight", None) else 0.0
            if strategy == "recent":
                score = recency + quality
            elif strategy == "citations":
                score = citation_score + quality
            else:
                score = 0.55 * recency + 0.35 * citation_score + 0.1 * quality
            scored.append((score, idx, doc))

        scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        return [item[2] for item in scored]


# 实例化服务
ingestion_service = IngestionService()
