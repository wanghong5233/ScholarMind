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
        seen: dict[str, DocumentCreate] = {}
        for doc in results:
            keys = IngestionService._paper_keys(doc)
            matched = next((seen[key] for key in keys if key in seen), None)
            if matched:
                IngestionService._merge_document_candidate(matched, doc)
                for key in keys:
                    seen[key] = matched
                continue
            deduped.append(doc)
            for key in keys:
                seen[key] = doc
        return deduped

    @staticmethod
    def _paper_keys(doc: DocumentCreate) -> set[str]:
        keys: set[str] = set()
        doi = (doc.doi or "").strip().lower()
        if doi:
            keys.add(f"doi:{doi}")
        semantic_id = (doc.semantic_scholar_id or "").strip().lower()
        if semantic_id:
            keys.add(f"s2:{semantic_id}")
        url = (doc.source_url or "").strip().lower()
        if url:
            keys.add(f"url:{url}")
            arxiv_id = IngestionService._extract_arxiv_id(url)
            if arxiv_id:
                keys.add(f"arxiv:{arxiv_id}")
        title = (doc.title or "").strip().lower()
        title = re.sub(r"[^a-z0-9]+", " ", title).strip()
        if title:
            keys.add(f"title:{title}")
        return keys or {str(doc)}

    @staticmethod
    def _extract_arxiv_id(url: str) -> Optional[str]:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#/]+)", url, flags=re.IGNORECASE)
        if not match:
            return None
        return match.group(1).removesuffix(".pdf").lower()

    @staticmethod
    def _merge_document_candidate(target: DocumentCreate, incoming: DocumentCreate) -> None:
        """Merge duplicate candidates while preserving richer metadata."""

        def is_pdf_url(value: Optional[str]) -> bool:
            lowered = (value or "").lower()
            return lowered.endswith(".pdf") or "/pdf/" in lowered

        if not target.source_url or (not is_pdf_url(target.source_url) and is_pdf_url(incoming.source_url)):
            target.source_url = incoming.source_url
        if not target.doi and incoming.doi:
            target.doi = incoming.doi
        if not target.semantic_scholar_id and incoming.semantic_scholar_id:
            target.semantic_scholar_id = incoming.semantic_scholar_id
        if target.citation_count is None and incoming.citation_count is not None:
            target.citation_count = incoming.citation_count
        if not target.abstract and incoming.abstract:
            target.abstract = incoming.abstract
        if not target.authors and incoming.authors:
            target.authors = incoming.authors
        if not target.keywords and incoming.keywords:
            target.keywords = incoming.keywords
        if not target.fields_of_study and incoming.fields_of_study:
            target.fields_of_study = incoming.fields_of_study
        target.highLight = bool(target.highLight or incoming.highLight)
        if not target.quality_label and incoming.quality_label:
            target.quality_source = incoming.quality_source
            target.quality_rank = incoming.quality_rank
            target.quality_label = incoming.quality_label
            target.quality_score = incoming.quality_score
        if not target.quality_labels and incoming.quality_labels:
            target.quality_labels = incoming.quality_labels

        current_venue = (target.journal_or_conference or "").strip().lower()
        incoming_venue = (incoming.journal_or_conference or "").strip()
        if incoming_venue and current_venue in {"", "arxiv", "preprint", "arxiv preprint"}:
            target.journal_or_conference = incoming_venue

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
            # quality_score is now an explicit ordinal (CCF-A=7, ..., JCR-Q4=2).
            # Normalise against the top of RANK_ORDER (7) to keep the weighted
            # combination roughly in [0,1].
            quality = min(1.0, float(getattr(doc, "quality_score", 0) or 0) / 7.0)
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
