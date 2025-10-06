import time
from typing import List, Dict, Any, Optional

import httpx
from schemas.document import DocumentCreate
from utils.get_logger import logger
from models.document import DocumentIngestionSource


class SemanticScholarService:
    """
    与 Semantic Scholar 学术文献 API 交互的服务。

    - 负责搜索论文
    - 处理速率限制（429）与重试
    - 统一将原始结果转换为内部模型
    """

    DEFAULT_PAPER_FIELDS = [
        "title",
        "year",
        "abstract",
        "authors.name",
        "venue",
        "publicationDate",
        "tldr",
        "externalIds",
        # 以便后续下载 PDF
        "isOpenAccess",
        "openAccessPdf",
        "url",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.semanticscholar.org/graph/v1",
        timeout: int = 20,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.headers = {"x-api-key": self.api_key} if self.api_key else {}

    def _make_request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送 GET 请求（带重试与 429 退避）。
        endpoint 传相对路径，如 '/paper/search'
        """
        url_path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                with httpx.Client(base_url=self.base_url, timeout=self.timeout, headers=self.headers) as client:
                    response = client.get(url_path, params=params)
                    if response.status_code == 429:
                        sleep_s = self.backoff_factor * (2 ** attempt)
                        logger.warning(f"Semantic Scholar rate limited (429). retry in {sleep_s:.2f}s")
                        time.sleep(sleep_s)
                        continue
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPError as http_err:
                last_error = http_err
                # 非 429 的错误直接退避后重试（有限次）
                sleep_s = self.backoff_factor * (2 ** attempt)
                logger.error(f"Semantic Scholar request failed (attempt {attempt + 1}/{self.max_retries}): {http_err}")
                time.sleep(sleep_s)

        # 所有重试失败
        if last_error is not None:
            raise last_error
        # 理论不会到达此处
        return {}

    def search_papers(self, query: str, limit: int = 100, year: Optional[str] = None) -> List[DocumentCreate]:
        params: Dict[str, Any] = {
            "query": query,
            "limit": limit,
            "fields": ",".join(self.DEFAULT_PAPER_FIELDS),
        }
        if year:
            params["year"] = year

        data = self._make_request("/paper/search", params)
        if not data or "data" not in data:
            return []
        return self._transform_results(data["data"]) 

    def _transform_results(self, results: List[Dict[str, Any]]) -> List[DocumentCreate]:
        transformed: List[DocumentCreate] = []
        for paper in results:
            external_ids = paper.get("externalIds", {}) or {}
            open_access_pdf = (paper.get("openAccessPdf") or {})
            pdf_url = open_access_pdf.get("url") or None
            # 兜底：若无 openAccessPdf.url，保留页面 URL 以便前端跳转
            page_url = paper.get("url")
            transformed.append(
                DocumentCreate(
                    title=paper.get("title") or "N/A",
                    authors=[a.get("name") for a in (paper.get("authors") or []) if a.get("name")],
                    abstract=paper.get("abstract"),
                    publication_year=paper.get("year"),
                    journal_or_conference=paper.get("venue"),
                    # 关键词/学科领域（当前接口不返回稳定字段，置为 None，后续可扩展）
                    keywords=None,
                    citation_count=None,
                    fields_of_study=None,
                    doi=external_ids.get("DOI"),
                    semantic_scholar_id=paper.get("paperId"),
                    source_url=pdf_url or page_url,
                    local_pdf_path=None,
                    file_hash=None,
                    ingestion_source=DocumentIngestionSource.ONLINE_IMPORT,
                )
            )
        return transformed


# 单例（可按需改为依赖注入创建）
semantic_scholar_service = SemanticScholarService()