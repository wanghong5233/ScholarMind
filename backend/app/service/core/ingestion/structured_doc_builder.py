from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from xml.etree import ElementTree as ET

from core.config import settings
from service.core.ingestion.grobid_client import get_grobid_client
from service.core.ingestion.interfaces import ParsedBlock
from utils.get_logger import log


TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


def _text_from_node(node: ET.Element | None) -> str:
    if node is None:
        return ""
    text = "".join(node.itertext()).strip()
    return re.sub(r"\s+", " ", text)


def _normalize_for_match(text: str) -> str:
    if not text:
        return ""
    no_space = re.sub(r"\s+", " ", text)
    return no_space.strip().lower()


@dataclass
class StructuredBlock:
    block_id: str
    logical_type: str
    title: Optional[str]
    text: str
    level: int = 0
    structure_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuredDocument:
    blocks: List[StructuredBlock] = field(default_factory=list)

    def to_parsed_blocks(self) -> List[ParsedBlock]:
        parsed: List[ParsedBlock] = []
        for blk in self.blocks:
            txt = (blk.text or "").strip()
            if not txt:
                continue
            md = dict(blk.metadata or {})
            original_element = md.get("element_type")
            if original_element != blk.logical_type:
                if original_element:
                    md.setdefault("original_element_type", original_element)
            md["element_type"] = blk.logical_type
            md.update(
                {
                    "logical_type": blk.logical_type,
                    "structure_path": blk.structure_path,
                    "structure_title": blk.title,
                    "structure_level": blk.level,
                }
            )
            if blk.title and "title" not in md:
                md["title"] = blk.title
            parsed.append(ParsedBlock(text=txt, metadata=md))
        return parsed


@dataclass
class LayoutMatch:
    pages: List[int] = field(default_factory=list)
    bboxes: List[Any] = field(default_factory=list)
    indices: List[int] = field(default_factory=list)


class StructuredDocumentBuilder:
    """
    结合 Grobid 全文结构 & MinerU 布局块，输出带结构标签 + 位置元数据的 StructuredBlock 列表。
    """

    def __init__(self):
        self.grobid_client = get_grobid_client()

    def build(self, document, mineru_blocks: List[ParsedBlock]) -> StructuredDocument:
        tei_xml = self._load_fulltext_tei(document)
        if not tei_xml:
            log.warning(
                f"[STRUCT_BUILDER_FALLBACK] doc_id={getattr(document, 'id', 'unknown')} reason=no_tei"
            )
            return self._fallback_document(mineru_blocks)

        try:
            root = ET.fromstring(tei_xml)
        except Exception as exc:
            log.error(f"[STRUCT_BUILDER_TEI_PARSE_FAIL] doc_id={getattr(document, 'id', 'unknown')} err={exc}")
            return self._fallback_document(mineru_blocks)

        blocks: List[StructuredBlock] = []
        blocks.extend(self._extract_title(root))
        blocks.extend(self._extract_authors(root))
        blocks.extend(self._extract_keywords(root))
        blocks.extend(self._extract_abstract(root))
        blocks.extend(self._extract_body(root))
        blocks.extend(self._extract_figures(root))
        blocks.extend(self._extract_tables(root))
        blocks.extend(self._extract_back_matter(root))

        if not blocks:
            log.warning(
                f"[STRUCT_BUILDER_EMPTY] doc_id={getattr(document, 'id', 'unknown')} fallback_to_layout"
            )
            return self._fallback_document(mineru_blocks)

        annotated = self._attach_layout(blocks, mineru_blocks)
        annotated = self._label_reference_blocks(annotated)
        annotated = self._absorb_equations(annotated)
        annotated = self._merge_short_text_blocks(annotated)
        return StructuredDocument(blocks=annotated)

    # --- TEI parsing helpers -------------------------------------------------

    def _extract_title(self, root: ET.Element) -> List[StructuredBlock]:
        results: List[StructuredBlock] = []
        title_node = root.find(".//tei:fileDesc/tei:titleStmt/tei:title[@level='a']", TEI_NS)
        if title_node is not None:
            text = _text_from_node(title_node)
            if text:
                results.append(
                    StructuredBlock(
                        block_id="title",
                        logical_type="title",
                        title="Title",
                        text=text,
                        level=0,
                        structure_path="front.title",
                    )
                )
        return results

    def _extract_authors(self, root: ET.Element) -> List[StructuredBlock]:
        authors_nodes = root.findall(".//tei:fileDesc/tei:titleStmt/tei:author", TEI_NS)
        authors: List[str] = []
        for node in authors_nodes:
            text = _text_from_node(node)
            if text:
                authors.append(text)
        if not authors:
            return []
        joined = ", ".join(authors)
        return [
            StructuredBlock(
                block_id="front.authors",
                logical_type="authors",
                title="Authors",
                text=joined,
                level=0,
                structure_path="front.authors",
                metadata={"authors": authors},
            )
        ]

    def _extract_keywords(self, root: ET.Element) -> List[StructuredBlock]:
        keyword_nodes = root.findall(".//tei:profileDesc/tei:textClass/tei:keywords", TEI_NS)
        results: List[StructuredBlock] = []
        for idx, kw_node in enumerate(keyword_nodes, start=1):
            terms = [
                term.strip()
                for term in [
                    _text_from_node(t)
                    for t in kw_node.findall(".//tei:term", TEI_NS)
                ]
                if term
            ]
            if not terms:
                text = _text_from_node(kw_node)
                if text:
                    terms = [t.strip() for t in re.split(r"[;,]", text) if t.strip()]
            if not terms:
                continue
            results.append(
                StructuredBlock(
                    block_id=f"keywords_{idx}",
                    logical_type="keywords",
                    title="Keywords",
                    text=", ".join(terms),
                    level=0,
                    structure_path=f"front.keywords.{idx}",
                    metadata={"keywords": terms},
                )
            )
        return results

    def _extract_abstract(self, root: ET.Element) -> List[StructuredBlock]:
        results: List[StructuredBlock] = []
        for idx, abstract_node in enumerate(root.findall(".//tei:abstract", TEI_NS), start=1):
            paragraphs = [
                _text_from_node(p)
                for p in abstract_node.findall(".//tei:p", TEI_NS)
                if _text_from_node(p)
            ]
            text = "\n\n".join(paragraphs).strip()
            if text:
                results.append(
                    StructuredBlock(
                        block_id=f"abstract_{idx}",
                        logical_type="abstract",
                        title="Abstract",
                        text=text,
                        level=0,
                        structure_path=f"front.abstract.{idx}",
                    )
                )
        return results

    def _extract_body(self, root: ET.Element) -> List[StructuredBlock]:
        body = root.find(".//tei:text/tei:body", TEI_NS)
        if body is None:
            return []

        results: List[StructuredBlock] = []
        divs = body.findall("tei:div", TEI_NS)
        for idx, div in enumerate(divs, start=1):
            results.extend(self._parse_div(div, path=f"body.{idx}", level=1))
        return results

    def _parse_div(self, div: ET.Element, path: str, level: int) -> List[StructuredBlock]:
        head = div.find("tei:head", TEI_NS)
        heading = _text_from_node(head)
        paragraphs = [
            _text_from_node(p)
            for p in div.findall("tei:p", TEI_NS)
            if _text_from_node(p)
        ]
        text = "\n\n".join(paragraphs).strip()

        logical_type = "section" if level == 1 else "subsection"
        block_id = f"{path}"
        title = heading or logical_type.title()

        results: List[StructuredBlock] = []
        if text:
            results.append(
                StructuredBlock(
                    block_id=block_id,
                    logical_type=logical_type,
                    title=title,
                    text=text,
                    level=level,
                    structure_path=path,
                )
            )

        # Nested divs
        child_divs = div.findall("tei:div", TEI_NS)
        for idx, child in enumerate(child_divs, start=1):
            child_path = f"{path}.{idx}"
            results.extend(self._parse_div(child, path=child_path, level=level + 1))
        return results

    def _extract_back_matter(self, root: ET.Element) -> List[StructuredBlock]:
        results: List[StructuredBlock] = []
        back = root.find(".//tei:text/tei:back", TEI_NS)
        if back is None:
            return results

        refs = back.findall(".//tei:listBibl", TEI_NS)
        for idx, ref_list in enumerate(refs, start=1):
            entries = [
                _text_from_node(bibl)
                for bibl in ref_list.findall(".//tei:biblStruct", TEI_NS)
                if _text_from_node(bibl)
            ]
            if not entries:
                entries = [
                    _text_from_node(p)
                    for p in ref_list.findall(".//tei:p", TEI_NS)
                    if _text_from_node(p)
                ]
            if not entries:
                continue
            formatted = self._format_reference_entries(entries)
            if not formatted:
                continue
            results.extend(self._build_reference_groups(parent_idx=idx, formatted_entries=formatted))

        # Additional fallback for reference divs
        div_refs = back.findall(".//tei:div[@type='references']", TEI_NS)
        for extra_idx, div in enumerate(div_refs, start=len(refs) + 1):
            paragraphs = [
                _text_from_node(p)
                for p in div.findall(".//tei:p", TEI_NS)
                if _text_from_node(p)
            ]
            if not paragraphs:
                continue
            formatted = self._format_reference_entries(paragraphs)
            if not formatted:
                continue
            results.extend(self._build_reference_groups(parent_idx=extra_idx, formatted_entries=formatted))
        return results

    # --- Layout alignment ----------------------------------------------------

    def _attach_layout(self, blocks: List[StructuredBlock], mineru_blocks: List[ParsedBlock]) -> List[StructuredBlock]:
        used_indices: Set[int] = set()
        annotated: List[StructuredBlock] = []
        for blk in blocks:
            match = self._match_layout(blk, mineru_blocks)
            if match.pages:
                blk.metadata.setdefault("page_range", match.pages)
                blk.metadata.setdefault("page", match.pages[0])
            if match.bboxes:
                blk.metadata.setdefault("bbox_list", match.bboxes)
            special = self._extract_special_metadata(mineru_blocks, match.indices)
            for key, value in special.items():
                if key not in blk.metadata:
                    blk.metadata[key] = value
            blk.metadata.setdefault("source", "grobid+mineru")
            blk.metadata.setdefault("alignment_status", "matched" if match.indices else "grobid_only")
            used_indices.update(match.indices)
            annotated.append(blk)

        orphan_blocks = self._build_orphan_blocks(mineru_blocks, used_indices)
        if orphan_blocks:
            annotated.extend(orphan_blocks)
        return annotated

    def _match_layout(self, block: StructuredBlock, mineru_blocks: List[ParsedBlock]) -> LayoutMatch:
        normalized = _normalize_for_match(block.text)
        if not normalized:
            return LayoutMatch()

        snippet = normalized[:400]
        start_idx = self._find_start_index(snippet, mineru_blocks)
        if start_idx is None:
            return LayoutMatch()

        collected_indices = self._collect_indices(start_idx, normalized, mineru_blocks)
        pages: List[int] = []
        bbox_list: List[Any] = []
        for idx in collected_indices:
            meta = mineru_blocks[idx].metadata or {}
            page = meta.get("page")
            if page is not None:
                pages.append(int(page))
            bbox = meta.get("bbox")
            if bbox:
                bbox_list.append(bbox)
        pages = sorted(set(pages))
        return LayoutMatch(pages=pages, bboxes=bbox_list, indices=collected_indices)

    def _find_start_index(self, snippet: str, mineru_blocks: List[ParsedBlock]) -> Optional[int]:
        snippet = snippet.strip()
        if not snippet:
            return None

        short_snippet = snippet[:120]
        for idx, block in enumerate(mineru_blocks):
            block_text = _normalize_for_match(block.text or "")
            if not block_text:
                continue
            if short_snippet and short_snippet in block_text:
                return idx
            # 如果 Grobid 文本比 MinerU 块短，也尝试包含判断
            if block_text and block_text in snippet:
                return idx
        return None

    def _collect_indices(self, start_idx: int, normalized_target: str, mineru_blocks: List[ParsedBlock]) -> List[int]:
        collected: List[int] = []
        combined = ""
        target_len = len(normalized_target)

        for idx in range(start_idx, len(mineru_blocks)):
            text_norm = _normalize_for_match(mineru_blocks[idx].text or "")
            if not text_norm:
                continue
            collected.append(idx)
            combined += " " + text_norm
            if len(combined) >= target_len * 0.85:
                break

        return collected

    # --- Fallback ------------------------------------------------------------

    def _fallback_document(self, mineru_blocks: List[ParsedBlock]) -> StructuredDocument:
        fallback_blocks = self._build_orphan_blocks(mineru_blocks, used_indices=set())
        return StructuredDocument(blocks=fallback_blocks)

    def _build_orphan_blocks(self, mineru_blocks: List[ParsedBlock], used_indices: Set[int]) -> List[StructuredBlock]:
        orphan_blocks: List[StructuredBlock] = []
        for idx, block in enumerate(mineru_blocks):
            if idx in used_indices:
                continue
            text = (block.text or "").strip()
            if not text:
                continue
            meta = dict(block.metadata or {})
            logical = str(meta.get("element_type") or "unclassified")
            normalized_text = text.lower()
            if logical == "paragraph":
                if self._looks_like_index_terms(normalized_text):
                    logical = "keywords"
                    keywords = self._extract_keywords_from_text(text)
                    if keywords:
                        meta.setdefault("keywords", keywords)
                        meta.setdefault("structure_title", "Keywords")
                elif self._looks_like_author_bio(normalized_text):
                    logical = "author_bio"
                    meta.setdefault("structure_title", "Author Bio")
                elif self._looks_like_figure_caption(text):
                    logical = "figure"
                    label = self._extract_figure_label(text)
                    meta.setdefault("figure_caption", text)
                    if label:
                        meta.setdefault("figure_label", label)
                        meta.setdefault("structure_title", label)
                    else:
                        meta.setdefault("structure_title", "Figure")
            original_element = meta.get("element_type")
            if original_element != logical:
                meta.setdefault("original_element_type", original_element)
            meta["element_type"] = logical
            path = f"orphan.{logical}.{idx}"
            page_val = meta.get("page")
            page_int = None
            try:
                if page_val is not None:
                    page_int = int(page_val)
            except (TypeError, ValueError):
                page_int = None
            bbox = meta.get("bbox")
            orphan_blocks.append(
                StructuredBlock(
                    block_id=path,
                    logical_type=logical,
                    title=meta.get("section") or logical.title(),
                    text=text,
                    level=int(meta.get("level") or 0),
                    structure_path=path,
                    metadata={
                        **meta,
                        "source": meta.get("parser_engine") or "mineru",
                        "alignment_status": "mineru_only",
                        "page": page_int,
                        "page_range": [page_int] if isinstance(page_int, int) else meta.get("page_range", []),
                        "bbox_list": meta.get("bbox_list") or ([bbox] if bbox else []),
                    },
                )
            )
        return orphan_blocks

    def _extract_special_metadata(self, mineru_blocks: List[ParsedBlock], indices: List[int]) -> Dict[str, Any]:
        if not indices:
            return {}
        special_keys_single = {"figure_image_path", "figure_image_url", "figure_md5"}
        special_keys_multi = {"figure_caption", "figure_json", "table_json", "equation_latex"}
        result: Dict[str, Any] = {}
        for idx in indices:
            meta = mineru_blocks[idx].metadata or {}
            for key in special_keys_single:
                if key in meta and key not in result:
                    result[key] = meta.get(key)
            for key in special_keys_multi:
                if key in meta:
                    target = result.setdefault(key, [])
                    value = meta.get(key)
                    if isinstance(value, list):
                        for item in value:
                            if item not in target:
                                target.append(item)
                    else:
                        if value not in target:
                            target.append(value)
        return result

    def _extract_figures(self, root: ET.Element) -> List[StructuredBlock]:
        figures: List[StructuredBlock] = []
        for idx, fig in enumerate(root.findall(".//tei:figure", TEI_NS), start=1):
            head = _text_from_node(fig.find("tei:head", TEI_NS))
            desc = _text_from_node(fig.find("tei:figDesc", TEI_NS))
            text = "\n\n".join([t for t in [head, desc] if t]).strip()
            if not text:
                continue
            figures.append(
                StructuredBlock(
                    block_id=f"figure.{idx}",
                    logical_type="figure",
                    title=head or f"Figure {idx}",
                    text=text,
                    level=0,
                    structure_path=f"body.figure.{idx}",
                    metadata={"figure_label": head or f"Figure {idx}"},
                )
            )
        return figures

    def _extract_tables(self, root: ET.Element) -> List[StructuredBlock]:
        tables: List[StructuredBlock] = []
        for idx, table in enumerate(root.findall(".//tei:table", TEI_NS), start=1):
            head = _text_from_node(table.find("tei:head", TEI_NS))
            desc = _text_from_node(table.find("tei:figDesc", TEI_NS))
            rows = []
            for row in table.findall(".//tei:row", TEI_NS):
                cells = [_text_from_node(cell) for cell in row.findall(".//tei:cell", TEI_NS)]
                joined = " | ".join(c for c in cells if c)
                if joined:
                    rows.append(joined)
            text_parts = [t for t in [head, desc] if t]
            if rows:
                text_parts.append("\n".join(rows))
            text = "\n\n".join(text_parts).strip()
            if not text:
                continue
            tables.append(
                StructuredBlock(
                    block_id=f"table.{idx}",
                    logical_type="table",
                    title=head or f"Table {idx}",
                    text=text,
                    level=0,
                    structure_path=f"body.table.{idx}",
                    metadata={"table_label": head or f"Table {idx}"},
                )
            )
        return tables

    def _label_reference_blocks(self, blocks: List[StructuredBlock]) -> List[StructuredBlock]:
        reference_found = any(blk.logical_type == "references" for blk in blocks)
        for blk in blocks:
            text = (blk.text or "").strip()
            if not text:
                continue
            if blk.logical_type == "references":
                reference_found = True
                continue
            if blk.logical_type == "reference_entry":
                reference_found = True
                continue
            if not reference_found and text.lower().startswith("references"):
                blk.logical_type = "references"
                blk.metadata["structure_title"] = "References"
                reference_found = True
                continue
            if self._looks_like_reference_entry(text):
                blk.logical_type = "reference_entry"
                blk.metadata.setdefault("structure_title", "Reference")
        return blocks

    def _look_like_equation(self, blk: StructuredBlock) -> bool:
        if blk.logical_type in {"equation", "equation_latex"}:
            return True
        text = (blk.text or "").strip()
        return bool(text.startswith("$$") and text.endswith("$$"))

    def _absorb_equations(self, blocks: List[StructuredBlock]) -> List[StructuredBlock]:
        """
        Ensure equations never appear as standalone blocks. Attach them to the nearest textual block.
        """
        textual_types = {
            "paragraph",
            "section",
            "subsection",
            "abstract",
            "title",
            "authors",
            "references",
            "reference_entry",
        }
        merged: List[StructuredBlock] = []
        pending_equations: List[str] = []
        last_text_block: StructuredBlock | None = None

        def _attach_to(block: StructuredBlock, equations: List[str]) -> None:
            if not equations:
                return
            existing = (block.text or "").strip()
            prefix = "\n\n".join(equations).strip()
            if existing:
                block.text = f"{existing}\n\n{prefix}"
            else:
                block.text = prefix
            inline_list = block.metadata.setdefault("inline_equations", [])
            inline_list.extend(equations)
            equations.clear()

        for blk in blocks:
            text = (blk.text or "").strip()
            if self._look_like_equation(blk):
                if last_text_block:
                    _attach_to(last_text_block, [text])
                else:
                    pending_equations.append(text)
                continue

            if pending_equations:
                _attach_to(blk, pending_equations)

            merged.append(blk)
            if blk.logical_type in textual_types and (blk.text or "").strip():
                last_text_block = blk

        if pending_equations and last_text_block:
            _attach_to(last_text_block, pending_equations)

        return merged

    def _merge_short_text_blocks(self, blocks: List[StructuredBlock]) -> List[StructuredBlock]:
        """
        Merge extremely short textual blocks (e.g., single words like "In") into adjacent blocks.
        Preference:
          - If the block is marked with a bullet (•, -, etc.), attach to the following block.
          - Else attach to the previous textual block.
        """
        textual_types = {"paragraph", "section", "subsection", "abstract", "title", "authors"}
        merged: List[StructuredBlock] = []
        i = 0
        min_chars = getattr(settings, "SM_STRUCT_SHORT_BLOCK_CHARS", 40)

        def is_short_text(block: StructuredBlock) -> bool:
            text = (block.text or "").strip()
            if not text:
                return True
            return len(text) < min_chars

        while i < len(blocks):
            blk = blocks[i]
            text = (blk.text or "").strip()
            if (
                blk.logical_type in textual_types
                and is_short_text(blk)
                and not blk.metadata.get("inline_equations")
            ):
                next_block = blocks[i + 1] if i + 1 < len(blocks) else None
                prev_block = merged[-1] if merged else None
                attach_after = False
                if text.startswith(("•", "-", "(", ")", "Fig", "Table")) and next_block:
                    attach_after = True

                target_block = None
                if attach_after and next_block and next_block.logical_type in textual_types:
                    target_block = next_block
                elif prev_block and prev_block.logical_type in textual_types:
                    target_block = prev_block

                if target_block:
                    existing = (target_block.text or "").strip()
                    joined = " ".join([text, existing]).strip() if attach_after else " ".join([existing, text]).strip()
                    target_block.text = joined
                    blk.text = ""
                    blk.metadata.setdefault("merged_into", target_block.structure_path)
                    i += 1
                    continue

            merged.append(blk)
            i += 1

        return [blk for blk in merged if (blk.text or "").strip()]

    def _looks_like_reference_entry(self, text: str) -> bool:
        line = text.strip()
        if not line:
            return False
        if re.match(r"^\s*(\[\d+\]|\d+\.)", line):
            return True
        if re.match(r"^[A-Z][A-Za-z\-\s']+,\s", line):
            return bool(re.search(r"\d{4}", line))
        return False

    def _looks_like_index_terms(self, text: str) -> bool:
        return text.startswith("index terms") or text.startswith("keywords:")

    def _extract_keywords_from_text(self, text: str) -> List[str]:
        cleaned = re.sub(r"^index terms[\s—:\-]*", "", text, flags=re.IGNORECASE).strip()
        if not cleaned:
            cleaned = re.sub(r"^keywords[\s—:\-]*", "", text, flags=re.IGNORECASE).strip()
        if not cleaned:
            return []
        parts = re.split(r"[;,]", cleaned)
        return [part.strip(" \n.") for part in parts if part.strip()]

    def _looks_like_author_bio(self, text: str) -> bool:
        """判断文本是否为作者简介（通常出现在论文末尾）。"""
        bio_patterns = [
            "received the",
            "working toward the",
            "his research interests include",
            "her research interests include",
            "their research interests include",
            "he is a",
            "she is a",
            "he is currently",
            "she is currently",
            "member, ieee",  # 注意逗号
            "member of ieee",
            "associate professor",
            "full professor",
            "assistant professor",
            "phd degree",
            "ms degree",
            "be degree",
            "visiting scholar",
            "postdoctoral",
        ]
        hits = sum(1 for phrase in bio_patterns if phrase in text)
        # 降低阈值：只需匹配 1 个模式即可（因为作者简介通常很明显）
        return hits >= 1

    def _looks_like_figure_caption(self, text: str) -> bool:
        return bool(re.match(r"^\s*(fig\.?|figure)\s*\d+", text, flags=re.IGNORECASE))

    def _extract_figure_label(self, text: str) -> str | None:
        match = re.search(r"(Fig\.?|Figure)\s*\.*\s*([A-Za-z0-9\-]+)", text, flags=re.IGNORECASE)
        if match:
            label = match.group(0).strip()
            return label
        return None

    def _clean_reference_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", (text or "")).strip(" .;")
        if cleaned.lower().startswith("utf8"):
            return ""
        if len(cleaned) < 6:
            return ""
        return cleaned

    def _format_reference_entries(self, entries: List[str]) -> List[Dict[str, Any]]:
        formatted: List[Dict[str, Any]] = []
        for idx, entry in enumerate(entries, start=1):
            cleaned = self._clean_reference_text(entry)
            if not cleaned:
                continue
            formatted.append(
                {
                    "number": idx,
                    "text": cleaned,
                    "display": f"[{idx}] {cleaned}",
                }
            )
        return formatted

    def _build_reference_groups(self, parent_idx: int, formatted_entries: List[Dict[str, Any]]) -> List[StructuredBlock]:
        if not formatted_entries:
            return []
        group_size = max(int(getattr(settings, "SM_REFERENCE_GROUP_SIZE", 25)), 1)
        blocks: List[StructuredBlock] = []
        for chunk_idx in range(0, len(formatted_entries), group_size):
            group = formatted_entries[chunk_idx : chunk_idx + group_size]
            if not group:
                continue
            start_num = group[0]["number"]
            end_num = group[-1]["number"]
            title = "References" if len(formatted_entries) <= group_size else f"References [{start_num}-{end_num}]"
            text = "\n".join(item["display"] for item in group)
            block_id = f"references.{parent_idx}"
            path = f"back.references.{parent_idx}"
            if len(formatted_entries) > group_size:
                suffix = chunk_idx // group_size + 1
                block_id = f"references.{parent_idx}.{suffix}"
                path = f"back.references.{parent_idx}.{suffix}"
            metadata = {
                "reference_numbers": [item["number"] for item in group],
                "reference_entries": [{"number": item["number"], "text": item["text"]} for item in group],
            }
            blocks.append(
                StructuredBlock(
                    block_id=block_id,
                    logical_type="references",
                    title=title,
                    text=text,
                    level=1,
                    structure_path=path,
                    metadata=metadata,
                )
            )
        return blocks

    # --- Grobid helpers ------------------------------------------------------

    def _load_fulltext_tei(self, document) -> Optional[str]:
        if not document or not getattr(document, "local_pdf_path", None):
            return None
        if not self.grobid_client.is_available():
            return None
        try:
            result = self.grobid_client.process_full_text_document(document.local_pdf_path)
            if result and result.get("tei_xml"):
                return result["tei_xml"]
        except Exception as exc:
            log.error(f"[STRUCT_BUILDER_GROBID_FAIL] doc_id={getattr(document, 'id', 'unknown')} err={exc}")
        return None



