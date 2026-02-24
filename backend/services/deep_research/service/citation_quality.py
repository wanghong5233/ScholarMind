"""Quality helpers for web results and report citations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}")

ACADEMIC_DOMAIN_HINTS = (
    "arxiv.org",
    "semanticscholar.org",
    "ieee.org",
    "acm.org",
    "springer.com",
    "nature.com",
    "science.org",
    "sciencedirect.com",
    "openreview.net",
    "neurips.cc",
    "iclr.cc",
    "aaai.org",
    "usenix.org",
    "jmlr.org",
    "doi.org",
    "aclanthology.org",
)

LOW_SIGNAL_DOMAIN_HINTS = (
    "csdn.net",
    "zhihu.com",
    "cnblogs.com",
    "toutiao.com",
    "poki.com",
    "openclash.cc",
    "freev2raynode.com",
)

DEFAULT_SENSITIVE_DOMAIN_PATTERNS = (
    "bbj75.com",
    "qf76.com",
    "larouwen.com",
    "kjxbtrs.cc",
    "ecrutzj.com",
    "zgwrbrw.cc",
    "rmjvfne.cc",
    "wtpzbwf.com",
    "wycraqdk.cc",
    "xanscgf.cc",
    "szdqjri.cc",
    "fahctgra.cc",
    "etlauzw.cc",
    "wvyceiqg.cc",
)

DEFAULT_BLOCKED_CONTENT_TERMS = (
    "porn",
    "sex",
    "xxx",
    "成人视频",
    "吃瓜",
    "黑料",
    "约炮",
    "乳肥",
    "shadowrocket",
    "v2ray",
    "clash节点",
)

QUESTION_PREFIX_MARKERS = (
    "你",
    "请",
    "能否",
    "可否",
    "是否",
    "can you",
    "could you",
    "would you",
    "do you",
    "what is your",
)

QUESTION_INLINE_MARKERS = (
    "你希望",
    "请提供",
    "请给出",
    "你能否",
    "你更",
    "your preference",
    "which do you prefer",
)

QUERY_STOP_TERMS = {
    "research",
    "topic",
    "question",
    "please",
    "provide",
    "details",
    "follow",
    "followup",
    "你",
    "希望",
    "请",
    "提供",
    "给出",
    "哪些",
    "是否",
    "更想",
    "更偏向",
    "研究",
    "问题",
    "主题",
}


@dataclass
class QualityAssessment:
    """Assessment result for a citation-like record."""

    accepted: bool
    score: float
    domain: str
    reason: str = ""
    overlap: float = 0.0


def split_csv_list(value: Optional[str]) -> List[str]:
    """Split comma/newline separated text into normalized lower-case items."""

    if not value:
        return []
    parts: List[str] = []
    for item in re.split(r"[,;\n]+", str(value)):
        normalized = str(item or "").strip().lower()
        if normalized:
            parts.append(normalized)
    return parts


def normalize_domain(url_or_domain: Any) -> str:
    """Extract and normalize a domain from URL/domain text."""

    raw = str(url_or_domain or "").strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.netloc or parsed.path or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def matches_domain_pattern(domain: str, patterns: Sequence[str]) -> bool:
    """Return True when domain matches any allow/deny pattern."""

    normalized = normalize_domain(domain)
    if not normalized:
        return False
    for pattern in patterns or []:
        candidate = normalize_domain(pattern)
        if not candidate:
            continue
        if normalized == candidate or normalized.endswith(f".{candidate}"):
            return True
    return False


def tokenize_query_terms(text: str, *, max_terms: int = 32) -> List[str]:
    """Extract compact lexical terms from mixed-language text."""

    terms: List[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(str(text or "").lower()):
        cleaned = token.strip()
        if not cleaned or cleaned in QUERY_STOP_TERMS:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        terms.append(cleaned)
        if len(terms) >= max(1, max_terms):
            break
    return terms


def looks_question_like_query(text: str) -> bool:
    """Detect whether a query looks like a user-clarification question."""

    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    if "?" in normalized or "？" in normalized:
        return True
    if any(normalized.startswith(marker) for marker in QUESTION_PREFIX_MARKERS):
        return True
    return any(marker in normalized for marker in QUESTION_INLINE_MARKERS)


def rewrite_query_to_keywords(text: str, *, max_terms: int = 10) -> str:
    """Rewrite a long question-like query into compact keyword form."""

    original = " ".join(str(text or "").strip().split())
    if not original:
        return ""
    terms = tokenize_query_terms(original, max_terms=max_terms * 2)
    if not terms:
        return original[:240]
    compact = " ".join(terms[: max(1, max_terms)]).strip()
    return compact[:240] if compact else original[:240]


def contains_blocked_content(texts: Iterable[str], blocked_terms: Sequence[str]) -> bool:
    """Detect blocked terms in free text with simple lexical matching."""

    payload = " ".join(str(item or "").lower() for item in texts)
    return any(term and term in payload for term in blocked_terms or [])


def _term_overlap_ratio(query_terms: List[str], evidence_terms: List[str]) -> float:
    """Return overlap ratio in range [0, 1]."""

    if not query_terms:
        return 1.0
    evidence = set(evidence_terms)
    if not evidence:
        return 0.0
    hits = sum(1 for term in query_terms if term in evidence)
    return hits / max(1, len(query_terms))


def _domain_signal_score(domain: str, *, academic_query: bool) -> float:
    """Return a domain-level prior score."""

    score = 0.0
    if matches_domain_pattern(domain, ACADEMIC_DOMAIN_HINTS):
        score += 1.6 if academic_query else 1.0
    elif domain.endswith(".edu") or domain.endswith(".gov"):
        score += 1.2 if academic_query else 0.8
    if matches_domain_pattern(domain, LOW_SIGNAL_DOMAIN_HINTS):
        score -= 1.2 if academic_query else 0.6
    return score


def assess_web_result_quality(
    *,
    query: str,
    title: str,
    snippet: str,
    url: str,
    academic_query: bool,
    allowlist: Sequence[str],
    denylist: Sequence[str],
    blocked_terms: Sequence[str],
    min_score: float,
    min_query_overlap: float,
) -> QualityAssessment:
    """Assess quality for one web search item."""

    domain = normalize_domain(url)
    if not domain:
        return QualityAssessment(False, -1.0, domain, "missing_domain")
    if matches_domain_pattern(domain, denylist):
        return QualityAssessment(False, -1.0, domain, "blocked_domain")
    if allowlist and not matches_domain_pattern(domain, allowlist):
        return QualityAssessment(False, -1.0, domain, "outside_allowlist")
    if contains_blocked_content([title, snippet, url], blocked_terms):
        return QualityAssessment(False, -1.0, domain, "blocked_term")

    query_terms = tokenize_query_terms(query, max_terms=20)
    evidence_terms = tokenize_query_terms(f"{title} {snippet} {domain}", max_terms=40)
    overlap = _term_overlap_ratio(query_terms, evidence_terms)
    if query_terms and overlap < max(0.0, min_query_overlap):
        return QualityAssessment(False, overlap, domain, "low_overlap", overlap)

    score = _domain_signal_score(domain, academic_query=academic_query) + overlap * 2.0
    if score < min_score:
        return QualityAssessment(False, score, domain, "low_score", overlap)
    return QualityAssessment(True, score, domain, "", overlap)


def assess_citation_quality(
    *,
    topic: str,
    title: str,
    snippet: str,
    url: str,
    source_type: str,
    allowlist: Sequence[str],
    denylist: Sequence[str],
    blocked_terms: Sequence[str],
    min_score: float,
    min_query_overlap: float,
) -> QualityAssessment:
    """Assess quality for a persisted citation before final references export."""

    domain = normalize_domain(url)
    if not domain:
        return QualityAssessment(False, -1.0, domain, "missing_domain")
    if matches_domain_pattern(domain, denylist):
        return QualityAssessment(False, -1.0, domain, "blocked_domain")
    if allowlist and not matches_domain_pattern(domain, allowlist):
        return QualityAssessment(False, -1.0, domain, "outside_allowlist")
    if contains_blocked_content([title, snippet, url], blocked_terms):
        return QualityAssessment(False, -1.0, domain, "blocked_term")

    query_terms = tokenize_query_terms(topic, max_terms=20)
    evidence_terms = tokenize_query_terms(f"{title} {snippet} {domain}", max_terms=40)
    overlap = _term_overlap_ratio(query_terms, evidence_terms)
    if query_terms and overlap < max(0.0, min_query_overlap):
        return QualityAssessment(False, overlap, domain, "low_overlap", overlap)

    normalized_source = str(source_type or "").strip().lower()
    source_boost = 0.0
    if "paper" in normalized_source:
        source_boost = 1.1
    elif "rag" in normalized_source:
        source_boost = 0.9
    elif "web" in normalized_source:
        source_boost = 0.2

    score = source_boost + _domain_signal_score(domain, academic_query=True) + overlap * 2.1
    if score < min_score:
        return QualityAssessment(False, score, domain, "low_score", overlap)
    return QualityAssessment(True, score, domain, "", overlap)
