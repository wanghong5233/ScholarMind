"""
Grobid 客户端
用于调用 Grobid 服务提取学术论文元数据
"""
from __future__ import annotations

from typing import Dict, Any, Optional
import requests
from utils.get_logger import log
from core.config import settings


class GrobidClient:
    """Grobid HTTP 客户端，用于学术元数据抽取"""

    def __init__(self, endpoint: str | None = None, timeout: int | None = None):
        self.endpoint = (endpoint or settings.SM_GROBID_ENDPOINT or "").rstrip("/")
        self.timeout = timeout or settings.SM_GROBID_TIMEOUT_SECS
        self.enabled = settings.SM_GROBID_ENABLED and bool(self.endpoint)

    def is_available(self) -> bool:
        """检查 Grobid 服务是否可用"""
        if not self.enabled:
            return False
        try:
            r = requests.get(f"{self.endpoint}/api/isalive", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def process_header_document(self, pdf_path: str) -> Optional[Dict[str, Any]]:
        """
        提取论文头部元数据（标题、作者、摘要、关键词等）
        
        Args:
            pdf_path: PDF 文件路径
            
        Returns:
            包含元数据的字典，失败返回 None
            {
                "title": str,
                "authors": [{"name": str, "affiliation": str}, ...],
                "abstract": str,
                "keywords": [str, ...],
                "publication_date": str,
                "doi": str,
                ...
            }
        """
        if not self.enabled:
            return None

        try:
            log.info(f"GrobidClient.process_header_document file={pdf_path}")
            
            with open(pdf_path, "rb") as f:
                files = {"input": f}
                # Grobid 参数配置
                # consolidateHeader: 1=调用CrossRef进行DOI解析（推荐，F1-score>0.95）
                # 注意：consolidateHeader=1 会增加处理时间（~1-2秒/文档）
                data = {
                    "consolidateHeader": "1",  # 启用CrossRef DOI解析
                    "includeRawAffiliations": "1",  # 包含原始单位信息
                }
                headers = {"Accept": "application/xml"}  # 请求 TEI XML 格式
                r = requests.post(
                    f"{self.endpoint}/api/processHeaderDocument",
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=self.timeout,
                )
            
            if r.status_code != 200:
                log.warning(f"GrobidClient.process_header_document failed status={r.status_code}")
                return None
            
            # Grobid 返回 TEI XML，需要解析
            tei_xml = r.text
            metadata = self._parse_tei_xml(tei_xml)
            
            log.info(f"GrobidClient.process_header_document ok title={metadata.get('title', '')[:50]} doi={metadata.get('doi', 'N/A')}")
            return metadata
            
        except Exception as e:
            log.error(f"GrobidClient.process_header_document error={e}")
            return None

    def _parse_tei_xml(self, tei_xml: str) -> Dict[str, Any]:
        """
        解析 Grobid 返回的 TEI XML 格式
        
        简化版实现：使用正则表达式提取关键字段
        完整实现可使用 lxml 或 BeautifulSoup
        """
        import re
        
        metadata: Dict[str, Any] = {}
        
        # 提取标题
        title_match = re.search(r'<title[^>]*level="a"[^>]*>(.*?)</title>', tei_xml, re.DOTALL)
        if title_match:
            title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            metadata["title"] = title
        
        # 提取作者
        authors = []
        author_pattern = r'<author>(.*?)</author>'
        for author_match in re.finditer(author_pattern, tei_xml, re.DOTALL):
            author_xml = author_match.group(1)
            # 提取姓名
            forename_match = re.search(r'<forename[^>]*>(.*?)</forename>', author_xml)
            surname_match = re.search(r'<surname[^>]*>(.*?)</surname>', author_xml)
            if forename_match and surname_match:
                name = f"{forename_match.group(1)} {surname_match.group(1)}"
                authors.append({"name": name.strip()})
        if authors:
            metadata["authors"] = authors
        
        # 提取摘要
        abstract_match = re.search(r'<abstract[^>]*>(.*?)</abstract>', tei_xml, re.DOTALL)
        if abstract_match:
            abstract = re.sub(r'<[^>]+>', '', abstract_match.group(1)).strip()
            # 清理多余空白
            abstract = re.sub(r'\s+', ' ', abstract)
            metadata["abstract"] = abstract
        
        # 提取 DOI
        doi_match = re.search(r'<idno[^>]*type="DOI"[^>]*>(.*?)</idno>', tei_xml, re.IGNORECASE)
        if doi_match:
            metadata["doi"] = doi_match.group(1).strip()
        
        # 提取关键词
        keywords = []
        keyword_pattern = r'<term[^>]*>(.*?)</term>'
        for kw_match in re.finditer(keyword_pattern, tei_xml):
            kw = re.sub(r'<[^>]+>', '', kw_match.group(1)).strip()
            if kw:
                keywords.append(kw)
        if keywords:
            metadata["keywords"] = keywords
        
        # 提取发表日期
        date_match = re.search(r'<date[^>]*when="([^"]+)"', tei_xml)
        if date_match:
            metadata["publication_date"] = date_match.group(1)
        
        return metadata

    def process_full_text_document(self, pdf_path: str) -> Optional[Dict[str, Any]]:
        """
        提取全文结构（章节、段落、引用等）
        
        注意：此接口较慢且返回数据量大，按需使用
        """
        if not self.enabled:
            return None

        try:
            log.info(f"GrobidClient.process_full_text file={pdf_path}")
            
            with open(pdf_path, "rb") as f:
                files = {"input": f}
                r = requests.post(
                    f"{self.endpoint}/api/processFulltextDocument",
                    files=files,
                    timeout=self.timeout * 2,  # 全文处理更慢，加倍超时
                )
            
            if r.status_code != 200:
                log.warning(f"GrobidClient.process_full_text failed status={r.status_code}")
                return None
            
            # 返回原始 TEI XML，由调用方决定如何处理
            return {"tei_xml": r.text}
            
        except Exception as e:
            log.error(f"GrobidClient.process_full_text error={e}")
            return None


# 全局单例
_grobid_client: Optional[GrobidClient] = None


def get_grobid_client() -> GrobidClient:
    """获取全局 Grobid 客户端单例"""
    global _grobid_client
    if _grobid_client is None:
        _grobid_client = GrobidClient()
    return _grobid_client

