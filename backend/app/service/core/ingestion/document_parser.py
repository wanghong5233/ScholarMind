from __future__ import annotations

from typing import Any, Dict, List
import os
from service.core.ingestion.interfaces import ParsedBlock, DocumentParser
from utils.get_logger import log
from core.config import settings


class LightweightDocumentParser(DocumentParser):
    """
    轻量解析器：
    - 对 .txt 直接读取为单块
    - 对 .pdf/.docx 返回空块（占位）
    """

    def parse(self, *, file_path: str) -> List[ParsedBlock]:
        _, ext = os.path.splitext(file_path.lower())
        try:
            log.info(f"ParseEntry: file={file_path} ext={ext}")
        except Exception:
            pass
        if ext == ".txt":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return [ParsedBlock(text=content, metadata={"page": 1})]
        if ext == ".pdf":
            # 兜底：使用 PyMuPDF 提取前若干页纯文本
            try:
                import fitz  # 让缺包直接抛错以便定位
                with fitz.open(file_path) as doc:
                    pages_text = []
                    for i in range(min(10, len(doc))):
                        try:
                            pages_text.append(doc[i].get_text("text") or "")
                        except Exception:
                            pages_text.append("")
                    full_text = "\n".join(pages_text).strip()
                if full_text:
                    return [ParsedBlock(text=full_text, metadata={"page": 1, "note": "lightweight_pymupdf"})]
            except Exception:
                pass
        return [ParsedBlock(text="", metadata={"note": "pending_real_parser"})]


class DeepdocDocumentParser(DocumentParser):
    """
    基于 deepdoc 的真实解析器：
    - PDF/DOCX 使用 deepdoc pipeline 提取段落；返回 ParsedBlock 列表
    - TXT 走轻量路径
    失败时回退到轻量解析，保证稳健
    """

    def parse(self, *, file_path: str) -> List[ParsedBlock]:
        _, ext = os.path.splitext(file_path.lower())
        if ext == ".txt":
            return LightweightDocumentParser().parse(file_path=file_path)
        try:
            from service.core.rag.app.naive import Pdf as DeepPdf, Docx as DeepDocx
            # deepdoc 解析器需要一个可调用的 callback；若不给会触发 'NoneType' is not callable
            def _noop_cb(*args, **kwargs):
                return None
            if ext == ".pdf":
                parser = DeepPdf()
                sections, _ = parser(file_path, callback=_noop_cb)
            elif ext == ".docx":
                parser = DeepDocx()
                sections, _ = parser(file_path)
            else:
                # 其它格式暂不支持
                return [ParsedBlock(text="", metadata={"note": f"unsupported_ext:{ext}"})]

            blocks: List[ParsedBlock] = []
            for item in sections or []:
                # item 形如 (text, tag)
                if not isinstance(item, (list, tuple)) or len(item) < 1:
                    continue
                text = item[0] or ""
                tag = item[1] if len(item) > 1 else ""
                if text.strip():
                    blocks.append(ParsedBlock(text=text, metadata={"tag": tag}))
            # 解析统计日志
            try:
                nonempty_blocks = sum(1 for b in blocks if (b.text or '').strip())
                total_chars_dbg = sum(len((b.text or '').strip()) for b in blocks)
                log.info(f"ParseStats: ext={ext} sections={len(sections) if sections is not None else 0} nonempty_blocks={nonempty_blocks} total_chars={total_chars_dbg}")
            except Exception:
                pass
            # 若 deepdoc 解析为空或文本极少，针对 PDF 做 PyMuPDF 兜底/补强纯文本
            need_fallback = (not blocks or all(not (b.text or "").strip() for b in blocks))
            if not need_fallback:
                try:
                    total_chars = sum(len((b.text or "").strip()) for b in blocks)
                    if total_chars < 200:  # 文本极少，尝试补强
                        need_fallback = True
                except Exception:
                    pass
            if need_fallback:
                if ext == ".pdf":
                    try:
                        import fitz  # 让缺包直接抛错以便定位
                        with fitz.open(file_path) as doc:
                            pages_text = []
                            # 提取前 30 页，兼顾扫描件或内容稀疏文档
                            for i in range(min(30, len(doc))):
                                try:
                                    pages_text.append(doc[i].get_text("text") or "")
                                except Exception:
                                    pages_text.append("")
                            full_text = "\n".join(pages_text).strip()
                        # 决策日志：打印 deepdoc/补强状态
                        try:
                            log.info(
                                f"ParseDecision: need_fallback={need_fallback} force={getattr(settings, 'SM_FORCE_PYMUPDF_FALLBACK', False)} deepdoc_blocks={len(blocks)} file={file_path}"
                            )
                        except Exception:
                            pass
                        if full_text or getattr(settings, "SM_FORCE_PYMUPDF_FALLBACK", False):
                            try:
                                if not blocks:
                                    log.warning(
                                        f"Deepdoc empty -> PyMuPDF fallback succeeded. file={file_path} extracted_chars={len(full_text)}"
                                    )
                                else:
                                    # 深度解析仅有极少文本，补强纯文本
                                    log.info(
                                        f"WeakText -> PyMuPDF supplement. file={file_path} supplement_chars={len(full_text)}"
                                    )
                            except Exception:
                                pass
                            # 纯文本块
                            fallback_blocks: List[ParsedBlock] = [
                                ParsedBlock(text=full_text, metadata={"page": 1, "note": "fallback_pymupdf"})
                            ]
                            # 轻量多模态兜底（基于简单规则从文本中抓取 caption/table 行）
                            if getattr(settings, "SM_MULTIMODAL_PARSE_ENABLED", False):
                                try:
                                    fig_cnt = 0
                                    tbl_cnt = 0
                                    import re as _re
                                    lines = [ln.strip() for ln in full_text.splitlines() if ln and ln.strip()]
                                    for ln in lines:
                                        low = ln.lower()
                                        if _re.match(r"^(figure|fig\.|图)[\s:：]", low):
                                            fallback_blocks.append(ParsedBlock(text=ln, metadata={"element_type": "figure_summary", "note": "fallback_caption"}))
                                            fig_cnt += 1
                                        elif _re.match(r"^(table|表)[\s:：]", low):
                                            fallback_blocks.append(ParsedBlock(text=ln, metadata={"element_type": "table_struct", "note": "fallback_caption"}))
                                            tbl_cnt += 1
                                    if fig_cnt or tbl_cnt:
                                        log.info(
                                            f"Multimodal fallback from PyMuPDF: figures={fig_cnt} tables={tbl_cnt} file={file_path}"
                                        )
                                except Exception:
                                    pass
                            return fallback_blocks
                    except Exception:
                        # 回退失败则继续返回 deepdoc 空占位，便于日志观察
                        pass
                try:
                    log.warning(
                        f"Deepdoc parse returned empty output with no viable fallback. file={file_path}"
                    )
                except Exception:
                    pass
                return [ParsedBlock(text="", metadata={"note": "deepdoc_empty_output"})]
            # 可选：多模态产物接入为 Chunk（基于 deepdoc caption/table 简化生成）
            try:
                if getattr(settings, "SM_MULTIMODAL_PARSE_ENABLED", False):
                    # 引入轻量 caption/table 转文本（如存在 deepdoc 的 caption/tag 信息）
                    extra_blocks: List[ParsedBlock] = []
                    fig_cnt = 0
                    tbl_cnt = 0
                    for it in sections or []:
                        if not isinstance(it, (list, tuple)) or len(it) < 2:
                            continue
                        text = it[0] or ""
                        tag = it[1] or ""
                        t = str(tag).lower()
                        if not text.strip():
                            continue
                        if "figure" in t or "caption" in t:
                            extra_blocks.append(ParsedBlock(text=text.strip(), metadata={"element_type": "figure_summary"}))
                            fig_cnt += 1
                        elif "table" in t:
                            # 简化：表格文本直接归并；后续可转 Markdown
                            extra_blocks.append(ParsedBlock(text=text.strip(), metadata={"element_type": "table_struct"}))
                            tbl_cnt += 1
                    if extra_blocks:
                        blocks.extend(extra_blocks)
                        try:
                            log.info(
                                f"Multimodal extras appended: figures={fig_cnt} tables={tbl_cnt} total_blocks={len(blocks)} file={file_path}"
                            )
                        except Exception:
                            pass
            except Exception:
                pass
            return blocks
        except Exception as e:
            # 回退轻量解析
            try:
                log.warning(f"Deepdoc parse raised exception. Falling back to lightweight parser. file={file_path} err={e}")
            except Exception:
                pass
            return LightweightDocumentParser().parse(file_path=file_path)


