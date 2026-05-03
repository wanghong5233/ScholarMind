"""ArXiv paper search service."""

from __future__ import annotations

from datetime import datetime
import re
from typing import List, Optional, Tuple

import arxiv

from models.document import DocumentIngestionSource
from schemas.document import DocumentCreate
from utils.get_logger import logger


class ArxivService:
    """Search and normalize arXiv papers into DocumentCreate payloads."""

    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        max_retries: int = 2,
        delay_seconds: float = 0.5,
        max_results: int = 100,
        max_age_years: Optional[int] = 5,
    ) -> None:
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._max_retries = max(0, int(max_retries))
        self._delay_seconds = max(0.0, float(delay_seconds))
        self._max_results = max(1, int(max_results))
        self._max_age_years = max_age_years if max_age_years is None else max(0, int(max_age_years))
        self._client = arxiv.Client(
            page_size=min(100, self._max_results),
            delay_seconds=self._delay_seconds,
            num_retries=self._max_retries,
        )

    def search_papers(
        self, *, query: str, limit: int = 100, year: str = "", rank_by: str = "relevance"
    ) -> List[DocumentCreate]:
        """Search arXiv and normalize results."""

        query = (query or "").strip()
        if not query:
            return []
        limit = max(1, min(int(limit or self._max_results), self._max_results))
        min_year, max_year = self._parse_year_filter(year)
        if min_year is None and max_year is None and self._max_age_years is not None:
            current_year = datetime.utcnow().year
            min_year = max(0, current_year - self._max_age_years)
            max_year = current_year

        sort_by = arxiv.SortCriterion.Relevance
        if (rank_by or "").lower() in {"recent", "hybrid"}:
            sort_by = arxiv.SortCriterion.SubmittedDate

        search = arxiv.Search(
            query=query,
            max_results=limit * 3,
            sort_by=sort_by,
            sort_order=arxiv.SortOrder.Descending,
        )

        try:
            results = list(self._client.results(search))
        except Exception as exc:  # pragma: no cover - upstream/network failure
            logger.error(f"arXiv search failed: {exc}")
            return []

        documents: List[DocumentCreate] = []
        for result in results:
            published = getattr(result, "published", None)
            if not published:
                continue
            year_val = published.year
            if min_year is not None and year_val < min_year:
                continue
            if max_year is not None and year_val > max_year:
                continue

            authors = [author.name for author in getattr(result, "authors", [])]
            pdf_url = getattr(result, "pdf_url", None)
            entry_id = getattr(result, "entry_id", None)
            journal_ref = (getattr(result, "journal_ref", None) or "").strip()
            doc = DocumentCreate(
                title=(getattr(result, "title", "") or "").strip() or "N/A",
                authors=authors,
                abstract=(getattr(result, "summary", "") or "").strip() or None,
                publication_year=year_val,
                journal_or_conference=journal_ref or "Preprint",
                # arXiv exposes only subject categories (e.g. "cs.CV"), not
                # paper-level author keywords. Keep keywords=None here and let
                # metadata_extractor fill real keywords after PDF parsing.
                keywords=None,
                citation_count=None,
                fields_of_study=list(getattr(result, "categories", []) or []) or None,
                doi=getattr(result, "doi", None),
                semantic_scholar_id=None,
                source_url=pdf_url or entry_id,
                local_pdf_path=None,
                file_hash=None,
                ingestion_source=DocumentIngestionSource.ONLINE_IMPORT,
                highLight=None,
                quality_source=None,
                quality_rank=None,
                quality_label=None,
                quality_score=0,
                quality_labels=None,
            )
            documents.append(doc)
            if len(documents) >= limit:
                break

        return documents

    @staticmethod
    def _parse_year_filter(value: str) -> Tuple[Optional[int], Optional[int]]:
        if not value:
            return None, None
        cleaned = value.strip().lower()
        match = re.match(r"since[_:\s]*(\d{4})", cleaned)
        if match:
            return int(match.group(1)), None
        match = re.match(r"(\d{4})\s*-\s*(\d{4})", cleaned)
        if match:
            return int(match.group(1)), int(match.group(2))
        match = re.match(r"(\d{4})\s*-\s*$", cleaned)
        if match:
            return int(match.group(1)), None
        match = re.match(r"^\d{4}$", cleaned)
        if match:
            year = int(cleaned)
            return year, year
        return None, None


arxiv_service = ArxivService()
