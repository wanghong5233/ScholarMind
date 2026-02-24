"""Web search client abstractions."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx


class WebSearchClient:
    """Async client for web search providers."""

    def __init__(
        self,
        provider: str,
        api_key: Optional[str],
        base_url: Optional[str],
        timeout: int = 20,
    ) -> None:
        """Initialize the web search client.

        Args:
            provider (str): Provider name (e.g., tavily, serper).
            api_key (Optional[str]): API key for the provider.
            base_url (Optional[str]): Override base URL.
            timeout (int): Request timeout in seconds.
        """

        self._provider = (provider or "tavily").strip().lower()
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Close the underlying HTTP client."""

        await self._client.aclose()

    def is_configured(self) -> bool:
        """Check if the client has required credentials."""

        return bool(self._api_key)

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute a web search and return normalized results."""

        if not self.is_configured():
            raise RuntimeError("Web search client is not configured.")
        return await self._search_with_provider(
            provider=self._provider,
            query=query,
            max_results=max_results,
            api_key=self._api_key,
            base_url=self._base_url,
            include_domains=include_domains or [],
            exclude_domains=exclude_domains or [],
        )

    async def open_page(self, url: str, max_chars: int = 6000) -> Dict[str, Any]:
        """Open a web page and return extracted text content."""

        normalized_url = str(url or "").strip()
        if not normalized_url:
            raise ValueError("Missing url for open_page")
        if self._provider == "tavily":
            # Strict mode: do not silently downgrade to direct fetch.
            return await self._open_page_tavily(normalized_url, max_chars=max_chars)
        return await self._open_page_direct(normalized_url, max_chars=max_chars)

    async def find_in_page(
        self,
        *,
        url: str,
        query: str,
        max_matches: int = 5,
        max_chars: int = 8000,
    ) -> Dict[str, Any]:
        """Find query-relevant snippets from a web page."""

        page_payload = await self.open_page(url, max_chars=max_chars)
        content = str(page_payload.get("content") or "")
        keywords = self._query_keywords(query)
        snippets = self._extract_matches(content, keywords, max_matches=max_matches)
        return {
            "provider": page_payload.get("provider") or self._provider,
            "url": page_payload.get("url") or url,
            "query": query,
            "keywords": keywords,
            "matches": snippets,
            "raw": page_payload.get("raw"),
        }

    async def _search_with_provider(
        self,
        *,
        provider: str,
        query: str,
        max_results: int,
        api_key: Optional[str],
        base_url: Optional[str],
        include_domains: List[str],
        exclude_domains: List[str],
    ) -> Dict[str, Any]:
        """Route search call by provider."""

        normalized = (provider or "").strip().lower()
        if normalized == "tavily":
            return await self._search_tavily(
                query=query,
                max_results=max_results,
                api_key=api_key,
                base_url=base_url,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
        if normalized == "serper":
            return await self._search_serper(
                query=query,
                max_results=max_results,
                api_key=api_key,
                base_url=base_url,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
        raise ValueError(f"Unsupported web search provider: {provider}")

    async def _search_tavily(
        self,
        *,
        query: str,
        max_results: int,
        api_key: Optional[str],
        base_url: Optional[str],
        include_domains: List[str],
        exclude_domains: List[str],
    ) -> Dict[str, Any]:
        """Call Tavily search API."""

        url = base_url or "https://api.tavily.com/search"
        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": False,
        }
        if include_domains:
            payload["include_domains"] = include_domains[:300]
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains[:150]
        response = await self._client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        results = [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("content") or item.get("snippet"),
            }
            for item in data.get("results", [])
        ]
        results = self._apply_domain_filters(
            results=results,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )
        return {"provider": "tavily", "query": query, "results": results, "raw": data}

    async def _search_serper(
        self,
        *,
        query: str,
        max_results: int,
        api_key: Optional[str],
        base_url: Optional[str],
        include_domains: List[str],
        exclude_domains: List[str],
    ) -> Dict[str, Any]:
        """Call Serper (Google) search API."""

        url = base_url or "https://google.serper.dev/search"
        headers = {"X-API-KEY": api_key or ""}
        payload = {"q": query, "num": max_results}
        response = await self._client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        results = [
            {
                "title": item.get("title"),
                "url": item.get("link"),
                "snippet": item.get("snippet"),
            }
            for item in data.get("organic", [])
        ]
        results = self._apply_domain_filters(
            results=results,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )
        return {"provider": "serper", "query": query, "results": results, "raw": data}

    @staticmethod
    def _normalize_domain(url_or_domain: Any) -> str:
        """Normalize URL/domain into a host-only lower-case string."""

        raw = str(url_or_domain or "").strip().lower()
        if not raw:
            return ""
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = (parsed.netloc or parsed.path or "").strip().lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    @classmethod
    def _domain_matches(cls, domain: str, patterns: List[str]) -> bool:
        """Check if a domain matches any pattern."""

        normalized = cls._normalize_domain(domain)
        if not normalized:
            return False
        for pattern in patterns or []:
            candidate = cls._normalize_domain(pattern)
            if not candidate:
                continue
            if normalized == candidate or normalized.endswith(f".{candidate}"):
                return True
        return False

    @classmethod
    def _apply_domain_filters(
        cls,
        *,
        results: List[Dict[str, Any]],
        include_domains: List[str],
        exclude_domains: List[str],
    ) -> List[Dict[str, Any]]:
        """Apply include/exclude domain filters on normalized results."""

        filtered: List[Dict[str, Any]] = []
        for item in results or []:
            domain = cls._normalize_domain(item.get("url"))
            if not domain:
                continue
            if exclude_domains and cls._domain_matches(domain, exclude_domains):
                continue
            if include_domains and not cls._domain_matches(domain, include_domains):
                continue
            filtered.append(item)
        return filtered

    async def _open_page_tavily(self, url: str, *, max_chars: int) -> Dict[str, Any]:
        """Call Tavily extract API for page content."""

        if not self._api_key:
            raise RuntimeError("Tavily API key is required for web.open_page.")
        extract_url = "https://api.tavily.com/extract"
        payload = {
            "api_key": self._api_key,
            "urls": [url],
            "include_images": False,
            "extract_depth": "advanced",
        }
        response = await self._client.post(extract_url, json=payload)
        response.raise_for_status()
        data = response.json()
        results = data.get("results") or []
        first = results[0] if isinstance(results, list) and results else {}
        title = first.get("title")
        raw_content = first.get("raw_content") or first.get("content") or first.get("text") or ""
        content = self._normalize_text(raw_content, max_chars=max_chars)
        return {
            "provider": "tavily",
            "url": first.get("url") or url,
            "title": title or self._extract_title(raw_content),
            "content": content,
            "raw": data,
        }

    async def _open_page_direct(self, url: str, *, max_chars: int) -> Dict[str, Any]:
        """Fetch page content directly."""

        headers = {"User-Agent": "ScholarMind-DeepResearch/1.0"}
        response = await self._client.get(
            url,
            headers=headers,
            follow_redirects=True,
            timeout=self._timeout,
        )
        response.raise_for_status()
        raw_html = response.text or ""
        title = self._extract_title(raw_html)
        content = self._normalize_text(raw_html, max_chars=max_chars)
        return {
            "provider": "direct",
            "url": str(response.url),
            "title": title,
            "content": content,
            "raw": {
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type"),
            },
        }

    @staticmethod
    def _extract_title(text: str) -> str:
        """Extract page title from raw HTML/text."""

        title_match = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.IGNORECASE | re.DOTALL)
        if title_match:
            return " ".join(title_match.group(1).split()).strip()[:200]
        return "Untitled"

    @staticmethod
    def _normalize_text(text: str, *, max_chars: int) -> str:
        """Normalize raw HTML/text into plain content."""

        payload = text or ""
        payload = re.sub(r"(?is)<script.*?>.*?</script>", " ", payload)
        payload = re.sub(r"(?is)<style.*?>.*?</style>", " ", payload)
        payload = re.sub(r"(?s)<[^>]+>", " ", payload)
        payload = re.sub(r"\s+", " ", payload).strip()
        return payload[: max(500, int(max_chars or 6000))]

    @staticmethod
    def _query_keywords(query: str) -> List[str]:
        """Extract keywords from a query string."""

        tokens = re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", str(query or ""))
        seen: set[str] = set()
        keywords: List[str] = []
        for token in tokens:
            normalized = token.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            keywords.append(token)
            if len(keywords) >= 8:
                break
        return keywords

    @staticmethod
    def _extract_matches(content: str, keywords: List[str], *, max_matches: int) -> List[str]:
        """Extract query-related snippets from page content."""

        if not content:
            return []
        if not keywords:
            return [content[:240]]
        parts = re.split(r"(?<=[\.\!\?\u3002\uff01\uff1f])\s+", content)
        matches: List[str] = []
        for part in parts:
            segment = part.strip()
            if not segment:
                continue
            lowered = segment.lower()
            if any(keyword.lower() in lowered for keyword in keywords):
                matches.append(segment[:280])
                if len(matches) >= max(1, int(max_matches or 5)):
                    break
        if matches:
            return matches
        return [content[:240]]
