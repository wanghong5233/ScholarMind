from __future__ import annotations

import os
import re
from typing import List, Tuple, Optional

from service.core.ingestion.interfaces import ParsedBlock, MetadataExtractor
from service.semantic_scholar_service import semantic_scholar_service


# Pre-compiled regex for speed
# Matches "10.1234/abc.123"
DOI_RE = re.compile(r'\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b', re.IGNORECASE)

# Enhanced regex to stop before common section headers for general sections/keywords
# Keep double newline here for keyword boundaries
STOP_PATTERNS = r'\n\s*\n|\n\s*(?:(?:I{1,3}|IV|V|VI|VII|VIII|IX|X)\.\s|[1-9]\d*\.?\s|INTRODUCTION|CONCLUSION[S]?|REFERENCE[S]?|APPENDIX|ACKNOWLEDGEMENT[S]?)'

# Final, more comprehensive stop pattern for abstracts.
# It specifically includes common metadata lines found in headers/footers of academic papers.
ABSTRACT_STOP_PATTERNS = r'\n\s*(?:(?:I{1,3}|IV|V|VI|VII|VIII|IX|X)\.\s|[1-9]\d*\.?\s|INDEX\s*TERMS|KEY\s*WORDS?|INTRODUCTION|CONCLUSION[S]?|REFERENCE[S]?|APPENDIX|ACKNOWLEDG(E?)MENT[S]?|MANUSCRIPT\s*RECEIVED|DOI:|Date\s*of\s*publication|©\s*\d{4})'

# Matches "Abstract" and captures everything after it until a stop pattern.
# This is simpler and allows helper functions to handle cleanup.
ABSTRACT_RE = re.compile(
    r"\b(a\s*b\s*s\s*t\s*r\s*a\s*c\s*t|abstract)\b(.*?)(?=" + ABSTRACT_STOP_PATTERNS + r"|\Z)",
    re.IGNORECASE | re.DOTALL
)

# Matches "Keywords..." or "Index Terms..."
KEYWORDS_RE = re.compile(
    r"(?:keywords|key words)[\s:—–-]*-?\s*(.*?)(?=" + STOP_PATTERNS + r"|\Z)", 
    re.IGNORECASE | re.DOTALL
)
INDEX_TERMS_RE = re.compile(
    r"index terms[\s:—–-]*-?\s*(.*?)(?=" + STOP_PATTERNS + r"|\Z)", 
    re.IGNORECASE | re.DOTALL
)

# --- Text normalization helpers ---
_HYPHENS = "-‐‑–—"  # common hyphen/dash variants

def _fix_hyphenation(text: str) -> str:
    """Join words broken with hyphen + newline/space: e.g., 'secu- rity' -> 'security'."""
    return re.sub(rf"(\w)[{_HYPHENS}]\s+(\w)", r"\1\2", text)

def _strip_leading_punct(text: str) -> str:
    """Strip leading dashes/bullets/colons/spaces that often follow 'Abstract—' styles."""
    return re.sub(r"^[\s\-–—:•·]+", "", text)

def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class DefaultMetadataExtractor(MetadataExtractor):
    """
    负责从解析块中抽取 title/doi/abstract/keywords，并用外部 API 高置信补全。
    策略：
    - 首选 Semantic Scholar（DOI 优先，其次标题搜索）：允许高置信覆盖标题，其余字段仅填空
    - 之后使用文档解析结果仅补全为 null 的字段（尤其是关键词，来自“Index Terms/Keywords/Key words”）
    - 不伪造关键词，不用研究领域替代关键词
    """

    def extract_and_enrich(self, *, db, document, blocks: List[ParsedBlock]):
        # 先做轻量内容解析，收集候选（但此时不写回，只缓存）
        parsed_title, parsed_doi, parsed_abstract, parsed_keywords = self._extract_from_blocks(blocks)
        if (not parsed_title or not parsed_doi or not parsed_abstract) and document.local_pdf_path:
            f_title, f_doi, f_abs, f_kw = self._fallback_from_pdf(document.local_pdf_path)
            parsed_title = parsed_title or f_title
            parsed_doi = parsed_doi or f_doi
            parsed_abstract = parsed_abstract or f_abs
            parsed_keywords = parsed_keywords or f_kw

        changed = False

        # 1) 先用 Semantic Scholar（DOI 优先）：允许高置信覆盖标题，其余字段只填空
        doi = document.doi or parsed_doi
        detail = None
        if doi:
            detail = semantic_scholar_service.get_paper_by_doi(doi)
            if not detail:
                papers = semantic_scholar_service.search_papers(query=doi, limit=1)
                detail = papers[0].model_dump() if papers else None
        elif parsed_title:
            papers = semantic_scholar_service.search_papers(query=parsed_title, limit=1)
            detail = papers[0].model_dump() if papers else None

        if detail:
            changed = self._merge_semantic_scholar_detail(document, detail, override_title=True) or changed

        # 2) 再用解析结果仅补空（不覆盖非空字段，不伪造）
        if parsed_title and not getattr(document, "title", None):
            document.title = parsed_title
            changed = True
        if parsed_doi and not getattr(document, "doi", None):
            document.doi = parsed_doi
            changed = True
        if parsed_abstract and not getattr(document, "abstract", None):
            document.abstract = parsed_abstract[:4000]
            changed = True
        if parsed_keywords and not getattr(document, "keywords", None):
            document.keywords = parsed_keywords[:50]
            changed = True

        if changed:
            db.add(document)
            db.commit()
            db.refresh(document)
        return document

    def _extract_from_blocks(self, blocks: List[ParsedBlock]) -> Tuple[str | None, str | None, str | None, list | None]:
        title = None
        doi = None
        abstract = None
        keywords = None

        # DOI
        for b in blocks[:50]:
            m = DOI_RE.search(b.text or "")
            if m:
                doi = m.group(0)
                break

        # title（带 tag 的优先）
        for b in blocks[:30]:
            tag = (b.metadata or {}).get("tag", "")
            if isinstance(tag, str) and ("title" in tag.lower()):
                t = (b.text or "").strip()
                if len(t) >= 5:
                    title = t.split("\n")[0][:200]
                    break
        if not title:
            # 兜底：取前几块中最长行
            cand = ""
            for b in blocks[:20]:
                t = (b.text or "").strip()
                if len(t) > len(cand):
                    cand = t
            if len(cand) >= 5:
                title = cand.split("\n")[0][:200]

        # abstract + keywords
        joined = "\n".join([(b.text or "") for b in blocks[:80]])
        m2 = ABSTRACT_RE.search(joined)
        if m2:
            body = m2.group(2)
            body = _fix_hyphenation(body)
            body = _strip_leading_punct(body)
            body = _normalize_whitespace(body)
            abstract = body[:4000]

        # 优先 Index Terms，其次 Keywords/Key words
        mk = INDEX_TERMS_RE.search(joined)
        if not mk:
            mk = KEYWORDS_RE.search(joined)
        if mk:
            raw = mk.group(1).replace('\n', ' ')
            raw = _fix_hyphenation(raw)
            raw = _strip_leading_punct(raw)
            raw = _normalize_whitespace(raw)
            parts = [x.strip().strip('.') for x in re.split(r"[,;]", raw) if x.strip()]
            if parts:
                keywords = parts

        return title, doi, abstract, keywords

    def _fallback_from_pdf(self, file_path: str) -> Tuple[str | None, str | None, str | None, list | None]:
        import fitz  # 让缺包直接抛错以便定位
        title = None
        doi = None
        abstract = None
        keywords = None
        try:
            with fitz.open(file_path) as doc:
                # 先尝试基于版面坐标的摘要提取（适配 IEEE 两栏 + 页脚干扰）
                try:
                    first_page = doc[0]
                    blocks = first_page.get_text("blocks", sort=True)
                    page_h = float(first_page.rect.height)
                    abstract = self._extract_abstract_from_pdf_blocks(blocks, page_height=page_h)
                except Exception:
                    pass

                pages_text = []
                for i in range(min(2, len(doc))):
                    try:
                        pages_text.append(doc[i].get_text("text") or "")
                    except Exception:
                        pages_text.append("")
                full = "\n".join(pages_text)
                # DOI
                m = DOI_RE.search(full)
                if m:
                    doi = m.group(0)
                # Abstract（如果坐标法未取到，再回退正则法）
                if not abstract:
                    m2 = ABSTRACT_RE.search(full)
                    if m2:
                        body = m2.group(2)
                        body = _fix_hyphenation(body)
                        body = _strip_leading_punct(body)
                        body = _normalize_whitespace(body)
                        abstract = body[:4000]
                # Index Terms / Keywords
                mk = INDEX_TERMS_RE.search(full)
                if not mk:
                    mk = KEYWORDS_RE.search(full)
                if mk:
                    raw = mk.group(1).replace('\n', ' ')
                    raw = _fix_hyphenation(raw)
                    raw = _strip_leading_punct(raw)
                    raw = _normalize_whitespace(raw)
                    parts = [x.strip().strip('.') for x in re.split(r"[,;]", raw) if x.strip()]
                    if parts:
                        keywords = parts
                # Title：过滤页眉/版权等
                head = (pages_text[0] if pages_text else "").splitlines()
                candidates = []
                for line in head[:25]:
                    s = line.strip()
                    if len(s) < 10 or len(s) > 200:
                        continue
                    if s.endswith(('.', ',', ':')):
                        continue
                    if s.lower() in ('abstract', 'introduction', 'keywords', 'index terms'):
                        continue
                    if re.search(r"(IEEE|ACM|copyright|This article|DOI|Vol\.|No\.)", s, re.IGNORECASE):
                        continue
                    # 排除全大写的行（通常是期刊名或抬头）
                    if s.isupper() and len(s.split()) > 1:
                        continue
                    candidates.append(s)
                if candidates:
                    title = max(candidates, key=len)[:200]
        except Exception:
            pass
        return title, doi, abstract, keywords

    def _extract_abstract_from_pdf_blocks(self, blocks: list, page_height: float | None = None) -> str | None:
        """
        基于坐标的摘要抽取（适配 IEEE 两栏 + 页脚/脚注干扰）。
        - 通过推断列分割线，将摘要在左右两栏分别截取后再合并；
        - 左栏以 Index Terms/Keywords/Introduction/脚注 为终止；
        - 右栏不受 Index Terms 限制（避免左栏关键词提前截断右栏摘要）。
        """
        if not blocks:
            return None

        # 统一解析 blocks 元组结构
        norm_blocks: list[tuple[float, float, float, float, str]] = []  # (x0,y0,x1,y1,text)
        for b in blocks:
            try:
                x0, y0, x1, y1, text, *_ = b
            except Exception:
                if len(b) >= 5:
                    x0 = float(b[0]); y0 = float(b[1]); x1 = float(b[2]); y1 = float(b[3]); text = b[4]
                else:
                    continue
            if not isinstance(text, str):
                continue
            norm_blocks.append((float(x0), float(y0), float(x1), float(y1), text))
        if not norm_blocks:
            return None

        # 辅助模式
        FOOTER_PAT = re.compile(r"(manuscript\s+received|digital\s+object\s+identifier|doi:|©\s*\d{4}|permission\s+is\s+required|corresponding\s+author)", re.IGNORECASE)
        INTRO_PAT = re.compile(r"^(i\.|1\.)?\s*introduction\b|^background\b", re.IGNORECASE)
        ABS_PAT = re.compile(r"\b(a\s*b\s*s\s*t\s*r\s*a\s*c\s*t|abstract)\b", re.IGNORECASE)
        KW_PAT = re.compile(r"key\s*words?", re.IGNORECASE)
        IT_PAT = re.compile(r"index\s*terms", re.IGNORECASE)

        # 推断列分割线（简单而有效）：取所有块的最小 x0 与最大 x1 的中点
        min_x = min(x0 for x0, *_ in norm_blocks)
        max_x = max(x1 for *_, x1, _t in [(b[0], b[1], b[2], b[3], b[4]) for b in norm_blocks])
        x_split = (min_x + max_x) / 2.0
        margin = 6.0  # 像素容差

        # 起点：含“Abstract”的最小 y0
        start_y_candidates = [y0 for x0, y0, x1, y1, t in norm_blocks if ABS_PAT.search(t)]
        if not start_y_candidates:
            return None
        start_y = min(start_y_candidates)

        # 终点候选：Index Terms / Keywords / Introduction / 脚注
        def min_y_for(pattern: re.Pattern[str]) -> float | None:
            ys = [y0 for _x0, y0, _x1, _y1, t in norm_blocks if pattern.search(t)]
            return min(ys) if ys else None

        y_index_terms = min_y_for(IT_PAT)
        y_keywords = min_y_for(KW_PAT)
        y_intro = min_y_for(INTRO_PAT)
        y_footer = min_y_for(FOOTER_PAT)

        # 全局保险：限制最大搜索高度，防止跨入正文（适度放宽）
        if page_height is not None:
            y_guard = start_y + 0.8 * float(page_height)
        else:
            y_guard = None

        # 分栏终点：
        # 左栏：仅受 Index Terms/Keywords/Introduction 约束（脚注不作为终止，只过滤其块）
        left_end_candidates = [y for y in [y_index_terms, y_keywords, y_intro, y_guard] if y is not None and y > start_y]
        left_end = min(left_end_candidates) if left_end_candidates else None
        # 右栏：不受 Index Terms/Keywords 约束，仅受 Introduction 限制（和全局保险）
        right_end_candidates = [y for y in [y_intro, y_guard] if y is not None and y > start_y]
        right_end = min(right_end_candidates) if right_end_candidates else None

        if left_end is None and right_end is None:
            return None

        parts: list[tuple[float, float, str]] = []
        for x0, y0, _x1, _y1, text in norm_blocks:
            # 排除明显的脚注/元信息
            if FOOTER_PAT.search(text):
                continue
            # 落在摘要起点之后
            if y0 + 1e-3 < start_y:
                continue
            # 左栏块
            if x0 <= x_split + margin:
                if left_end is not None and y0 < left_end:
                    parts.append((y0, x0, text))
            # 右栏块
            if x0 >= x_split - margin:
                if right_end is not None and y0 < right_end:
                    parts.append((y0, x0, text))

        if not parts:
            return None

        # 排序并拼接
        parts.sort(key=lambda t: (round(t[0], 1), t[1]))
        raw = " ".join(p[2] for p in parts)
        # 清洗
        raw = re.sub(r"^(?:\s*(?:a\s*b\s*s\s*t\s*r\s*a\s*c\s*t|abstract))\W+", "", raw, flags=re.IGNORECASE)
        raw = _fix_hyphenation(raw)
        raw = _strip_leading_punct(raw)
        raw = _normalize_whitespace(raw)
        return raw[:4000]

    def _should_override_title(self, document) -> bool:
        t = getattr(document, "title", None)
        if not t:
            return True
        tl = t.strip().lower()
        if tl.endswith(".pdf"):
            return True
        try:
            base = os.path.basename(document.local_pdf_path or "")
            base_no_ext = os.path.splitext(base)[0].lower()
            if tl == base_no_ext or tl == base.lower():
                return True
        except Exception:
            pass
        return False

    def _merge_semantic_scholar_detail(self, document, detail: dict | None, override_title: bool = True) -> bool:
        if not detail:
            return False
        changed = False
        title = detail.get("title")
        if title and (override_title or self._should_override_title(document)):
            document.title = title
            changed = True
        if not getattr(document, "authors", None):
            authors = detail.get("authors") or []
            names = [a.get("name") if isinstance(a, dict) else a for a in authors if a]
            if names:
                document.authors = names
                changed = True
        if not getattr(document, "publication_year", None) and detail.get("year"):
            document.publication_year = detail.get("year")
            changed = True
        if not getattr(document, "journal_or_conference", None) and detail.get("venue"):
            document.journal_or_conference = detail.get("venue")
            changed = True
        if not getattr(document, "semantic_scholar_id", None) and detail.get("paperId"):
            document.semantic_scholar_id = detail.get("paperId")
            changed = True
        if not getattr(document, "citation_count", None) and detail.get("citationCount"):
            document.citation_count = detail.get("citationCount")
            changed = True
        if not getattr(document, "fields_of_study", None) and detail.get("fieldsOfStudy"):
            document.fields_of_study = detail.get("fieldsOfStudy")
            changed = True
        # 不从 fieldsOfStudy 生成关键词；仅当 API 返回明确的 keywords 字段才填入
        if not getattr(document, "keywords", None):
            kws_raw = detail.get("keywords")
            if isinstance(kws_raw, list) and kws_raw:
                kws: List[str] = []
                for item in kws_raw:
                    if isinstance(item, str) and item:
                        kws.append(item)
                    elif isinstance(item, dict):
                        name = item.get("name")
                        if name:
                            kws.append(name)
                if kws:
                    document.keywords = kws[:50]
                    changed = True
        return changed

    def _extract_keywords_from_text(self, text: str) -> Optional[List[str]]:
        """Extracts keywords from a given body of text."""
        # Prioritize "Index Terms", then "Keywords"
        match = INDEX_TERMS_RE.search(text)
        if not match:
            match = KEYWORDS_RE.search(text)
        
        if match:
            # Take the captured group, replace newlines with spaces
            keywords_str = match.group(1).replace('\n', ' ').strip()
            
            # Clean up: remove trailing period if it's the absolute last character
            if keywords_str.endswith('.'):
                keywords_str = keywords_str[:-1]

            # Split by comma or semicolon, handle trailing punctuation on each keyword
            keywords = [
                kw.strip() for kw in re.split(r'[,;]', keywords_str) if kw.strip() and len(kw.strip()) > 1
            ]
            
            if keywords:
                return keywords
        
        return None

    def _parse_and_extract_fallback_metadata(
        self, file_path: str
    ) -> Tuple[Optional[List[str]], Optional[str], Optional[str], Optional[List[str]]]:
        import fitz  # 让缺包直接抛错以便定位
        import logging
        logger = logging.getLogger(__name__)

        try:
            with fitz.open(file_path) as doc:
                pages_text = [doc.load_page(i).get_text("text", sort=True) for i in range(min(3, doc.page_count))]
                full_text = "\n".join(pages_text)

                # Extract metadata using the same robust methods
                doi = self._extract_doi_from_text(full_text)
                abstract = self._extract_abstract_from_text(full_text)
                keywords = self._extract_keywords_from_text(full_text)
                
                # Title fallback: first non-empty line
                title = None
                for line in pages_text[0].split('\n'):
                    cleaned_line = line.strip()
                    if cleaned_line and len(cleaned_line) > 15: # Basic filter
                        title = cleaned_line
                        break
                
                return pages_text, title, doi, abstract, keywords

        except Exception as e:
            logger.error(f"Fallback PDF parsing failed for {file_path}: {e}")
            return None, None, None, None, None

    def _extract_title_and_doi_from_blocks(
        self, parsed_blocks: List[ParsedBlock]
    ) -> Tuple[Optional[str], Optional[str]]:
        # Title is likely the first block with significant text
        for block in parsed_blocks[:10]:
            text = block.get("text", "").strip()
            if len(text) > 20 and "\n" not in text:  # Simple heuristic for a title
                # Further filter out common non-title headers
                if not any(header in text.lower() for header in ["abstract", "introduction", "contents"]):
                    title = text
                    break
        
        # DOI can appear in early blocks
        doi = self._extract_doi_from_text(
            "\n".join([b.get("text", "") for b in parsed_blocks[:50]])
        )
        
        return title, doi

    def _extract_abstract_from_text(self, text: str) -> Optional[str]:
        """Extracts the abstract from a given body of text."""
        match = ABSTRACT_RE.search(text)
        if match:
            # Capture the main body of the abstract, clean up whitespace
            abstract_text = ' '.join(match.group(0).split()[1:]) # Skip the word 'Abstract'
            # Stop at the next major section to avoid over-capturing
            stop_match = re.search(ABSTRACT_STOP_PATTERNS, abstract_text, re.IGNORECASE | re.DOTALL)
            if stop_match:
                abstract_text = abstract_text[:stop_match.start()]
            return abstract_text.strip()
        return None

    def _extract_doi_from_text(self, text: str) -> Optional[str]:
        """Extracts the first valid DOI from a given body of text."""
        match = DOI_RE.search(text)
        return match.group(0) if match else None


