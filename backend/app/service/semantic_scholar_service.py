import time
from typing import List, Dict, Any, Optional

import httpx
from schemas.document import DocumentCreate
from utils.get_logger import logger
from models.document import DocumentIngestionSource
from service.core.api.utils.ccf_whitelist import is_high_quality_venue
from urllib.parse import quote
from core.config import settings


class SemanticScholarService:
    """
    与 Semantic Scholar 学术文献 API 交互的服务。

    - 负责搜索论文
    - 处理速率限制（429）与重试
    - 统一将原始结果转换为内部模型
    """

    DEFAULT_PAPER_FIELDS = [
        "paperId",
        "title",
        "year",
        "abstract",
        "authors.name",
        "venue",
        "publicationDate",
        "tldr",
        "externalIds",
        "fieldsOfStudy",
        "citationCount",
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
        max_retries: int = 5,
        backoff_factor: float = 0.5,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.headers = {"x-api-key": self.api_key} if self.api_key else {}
        # 如果 key 已被服务端拒绝（401/403），在当前进程内禁用该 key，
        # 避免每次请求都先吃一次 403 再触发匿名链路。
        self._key_disabled = False
        # 匿名模式全局退避窗口，避免短时间内连续撞 429。
        self._anonymous_next_allowed_at = 0.0

    def _make_request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送 GET 请求（带重试与 429 退避）。
        endpoint 传相对路径，如 '/paper/search'
        """
        url_path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        last_error: Optional[Exception] = None
        auth_failed = False

        auth_headers = self.headers if self.headers and not self._key_disabled else {}
        if not auth_headers:
            return self._make_request_without_api_key(url_path, params)

        for attempt in range(self.max_retries):
            try:
                with httpx.Client(base_url=self.base_url, timeout=self.timeout, headers=auth_headers) as client:
                    response = client.get(url_path, params=params)
                    if response.status_code == 429:
                        sleep_s = self._resolve_retry_delay(
                            retry_after=response.headers.get("retry-after"),
                            attempt=attempt,
                        )
                        logger.warning(f"Semantic Scholar rate limited (429). retry in {sleep_s:.2f}s")
                        time.sleep(sleep_s)
                        continue
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPError as http_err:
                last_error = http_err
                status_code = getattr(getattr(http_err, "response", None), "status_code", None)
                if status_code in {401, 403} and auth_headers:
                    auth_failed = True
                    self._key_disabled = True
                    logger.warning(
                        f"Semantic Scholar key auth failed ({status_code}), disable key for current process "
                        f"and retry without API key."
                    )
                    break
                # 非 429 的错误直接退避后重试（有限次）
                sleep_s = self.backoff_factor * (2 ** attempt)
                logger.error(f"Semantic Scholar request failed (attempt {attempt + 1}/{self.max_retries}): {http_err}")
                time.sleep(sleep_s)

        if auth_failed:
            return self._make_request_without_api_key(url_path, params)

        # 所有重试失败
        if last_error is not None:
            raise last_error
        # 理论不会到达此处
        return {}

    def _make_request_without_api_key(self, url_path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """当 API key 鉴权失败时，回退到匿名请求（与历史知识库链路行为一致）。"""

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            now_ts = time.time()
            if now_ts < self._anonymous_next_allowed_at:
                time.sleep(max(0.0, self._anonymous_next_allowed_at - now_ts))
            try:
                with httpx.Client(base_url=self.base_url, timeout=self.timeout, headers={}) as client:
                    response = client.get(url_path, params=params)
                    if response.status_code == 429:
                        sleep_s = self._resolve_retry_delay(
                            retry_after=response.headers.get("retry-after"),
                            attempt=attempt,
                        )
                        self._anonymous_next_allowed_at = time.time() + sleep_s
                        logger.warning(
                            f"Semantic Scholar anonymous request rate limited (429). retry in {sleep_s:.2f}s"
                        )
                        time.sleep(sleep_s)
                        continue
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPError as http_err:
                last_error = http_err
                sleep_s = self.backoff_factor * (2 ** attempt)
                logger.error(
                    f"Semantic Scholar anonymous request failed "
                    f"(attempt {attempt + 1}/{self.max_retries}): {http_err}"
                )
                time.sleep(sleep_s)

        if last_error is not None:
            raise last_error
        return {}

    def _resolve_retry_delay(self, retry_after: Optional[str], attempt: int) -> float:
        """Resolve retry delay with Retry-After header and exponential backoff."""

        header_delay = 0.0
        if retry_after:
            try:
                header_delay = float(str(retry_after).strip())
            except ValueError:
                header_delay = 0.0
        exp_delay = self.backoff_factor * (2 ** attempt)
        # 控制上限，避免阻塞过长。
        return max(0.25, min(8.0, max(header_delay, exp_delay)))

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

    def get_paper_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """
        通过 DOI 精确获取论文（优先于模糊搜索）。
        """
        if not doi:
            return None
        # 对 DOI 进行 URL 编码以避免路径中的 '/'' 导致 400
        encoded = quote(doi, safe="")
        endpoint = f"/paper/DOI:{encoded}"
        params = {"fields": ",".join(self.DEFAULT_PAPER_FIELDS)}
        try:
            data = self._make_request(endpoint, params)
            if not data or not data.get("title"):
                return None
            return data
        except Exception as e:
            logger.error(f"Semantic Scholar get_paper_by_doi failed: {e}")
            # 降级重试：使用精简字段再试一次，规避字段不兼容导致的 400
            try:
                minimal_fields = [
                    "paperId",
                    "title",
                    "year",
                    "authors.name",
                    "venue",
                    "externalIds",
                    "url",
                    "citationCount",
                    "fieldsOfStudy",
                ]
                params_min = {"fields": ",".join(minimal_fields)}
                data = self._make_request(endpoint, params_min)
                if not data or not data.get("title"):
                    return None
                return data
            except Exception as e2:
                logger.error(f"Semantic Scholar get_paper_by_doi minimal retry failed: {e2}")
                return None

    def _transform_results(self, results: List[Dict[str, Any]]) -> List[DocumentCreate]:
        transformed: List[DocumentCreate] = []
        for paper in results:
            external_ids = paper.get("externalIds", {}) or {}
            open_access_pdf = (paper.get("openAccessPdf") or {})
            pdf_url = open_access_pdf.get("url") or None
            # 兜底：若无 openAccessPdf.url，保留页面 URL 以便前端跳转
            page_url = paper.get("url")
            venue = paper.get("venue")
            high_light = is_high_quality_venue(venue)
            kws = paper.get("keywords")  # 不再用 fieldsOfStudy 充当关键词
            transformed.append(DocumentCreate(
                title=paper.get("title") or "N/A",
                authors=[a.get("name") for a in (paper.get("authors") or []) if a.get("name")],
                abstract=paper.get("abstract"),
                publication_year=paper.get("year"),
                journal_or_conference=venue,
                keywords=kws,
                citation_count=paper.get("citationCount"),
                fields_of_study=paper.get("fieldsOfStudy"),
                doi=external_ids.get("DOI"),
                semantic_scholar_id=paper.get("paperId"),
                source_url=pdf_url or page_url,
                local_pdf_path=None,
                file_hash=None,
                ingestion_source=DocumentIngestionSource.ONLINE_IMPORT,
                highLight=high_light,
            ))
        return transformed


# 单例（使用应用配置中的 API Key，避免无 Key 导致频繁限流）
semantic_scholar_service = SemanticScholarService(
    api_key=settings.semantic_scholar_api_key,
)