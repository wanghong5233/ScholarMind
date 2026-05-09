from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import unquote, urlparse

_DOI_URL_PREFIX_RE = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/", re.IGNORECASE)
_DOI_LEADING_PREFIX_RE = re.compile(r"^doi\s*:\s*", re.IGNORECASE)
_DOI_TRAILING_PUNCT_RE = re.compile(r"[)\].,;:]+$")

_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([^?#/]+)", re.IGNORECASE)
_ARXIV_PREFIX_RE = re.compile(r"^arxiv\s*:\s*", re.IGNORECASE)
_ARXIV_VERSION_SUFFIX_RE = re.compile(r"(?P<base>.+?)(?:v\d+)$", re.IGNORECASE)
_ARXIV_NEW_STYLE_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$", re.IGNORECASE)
_ARXIV_OLD_STYLE_RE = re.compile(r"^[a-z\-]+(?:\.[a-z\-]+)?/\d{7}(?:v\d+)?$", re.IGNORECASE)


def normalize_doi(raw_value: Optional[str]) -> Optional[str]:
    """Normalize DOI to lowercase bare identifier.

    Examples:
    - https://doi.org/10.1000/ABC -> 10.1000/abc
    - DOI:10.1000/ABC -> 10.1000/abc
    """

    if raw_value is None:
        return None
    value = unquote(str(raw_value)).strip()
    if not value:
        return None

    value = _DOI_LEADING_PREFIX_RE.sub("", value, count=1)
    value = _DOI_URL_PREFIX_RE.sub("", value, count=1)
    value = _DOI_TRAILING_PUNCT_RE.sub("", value.strip())
    value = value.lower()
    return value or None


def normalize_semantic_scholar_id(raw_value: Optional[str]) -> Optional[str]:
    """Normalize Semantic Scholar paper id (or compatible identifier)."""

    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None

    lowered = value.lower()
    if "semanticscholar.org" in lowered:
        try:
            parsed = urlparse(value)
            segments = [seg for seg in parsed.path.split("/") if seg]
            if segments:
                value = segments[-1]
        except ValueError:
            pass
    value = value.strip().lower()
    return value or None


def extract_arxiv_id(raw_value: Optional[str]) -> Optional[str]:
    """Extract canonical arXiv id from URL/raw string.

    Canonicalization removes the version suffix, e.g. ``2310.06825v2`` -> ``2310.06825``.
    """

    if raw_value is None:
        return None
    value = unquote(str(raw_value)).strip().lower()
    if not value:
        return None

    matched = _ARXIV_URL_RE.search(value)
    if matched:
        candidate = matched.group(1)
    else:
        candidate = _ARXIV_PREFIX_RE.sub("", value, count=1)

    candidate = candidate.strip().strip("/")
    candidate = candidate.removesuffix(".pdf")
    candidate = candidate.split("?", 1)[0].split("#", 1)[0].strip()
    if not candidate:
        return None

    if not (_ARXIV_NEW_STYLE_RE.fullmatch(candidate) or _ARXIV_OLD_STYLE_RE.fullmatch(candidate)):
        return None

    return _strip_arxiv_version(candidate)


def normalize_source_url(raw_value: Optional[str]) -> Optional[str]:
    """Normalize URL host/scheme casing while preserving path/query/fragment."""

    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None

    try:
        parsed = urlparse(value)
    except ValueError:
        return value

    if not parsed.scheme or not parsed.netloc:
        return value

    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{scheme}://{host}{path}{query}{fragment}"


def normalize_document_identity(
    *,
    semantic_scholar_id: Optional[str],
    doi: Optional[str],
    source_url: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return normalized (semantic_id, doi, arxiv_id)."""

    normalized_semantic = normalize_semantic_scholar_id(semantic_scholar_id)
    normalized_doi = normalize_doi(doi)
    normalized_arxiv = extract_arxiv_id(source_url)
    return normalized_semantic, normalized_doi, normalized_arxiv


def _strip_arxiv_version(raw_arxiv_id: str) -> str:
    match = _ARXIV_VERSION_SUFFIX_RE.fullmatch(raw_arxiv_id)
    if not match:
        return raw_arxiv_id
    return match.group("base")
