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
            # 若 deepdoc 解析为空或文本全空，针对 PDF 做 PyMuPDF 兜底提取纯文本
            if not blocks or all(not (b.text or "").strip() for b in blocks):
                if ext == ".pdf":
                    try:
                        import fitz  # 让缺包直接抛错以便定位
                        with fitz.open(file_path) as doc:
                            pages_text = []
                            # 提取前 10 页，避免超大文件一次性加载
                            for i in range(min(10, len(doc))):
                                try:
                                    pages_text.append(doc[i].get_text("text") or "")
                                except Exception:
                                    pages_text.append("")
                            full_text = "\n".join(pages_text).strip()
                        if full_text:
                            try:
                                log.warning(f"Deepdoc parse returned empty. Falling back to PyMuPDF. file={file_path}")
                            except Exception:
                                pass
                            return [ParsedBlock(text=full_text, metadata={"page": 1, "note": "fallback_pymupdf"})]
                    except Exception:
                        # 回退失败则继续返回 deepdoc 空占位，便于日志观察
                        pass
                try:
                    log.warning(f"Deepdoc parse returned empty output with no viable fallback. file={file_path}")
                except Exception:
                    pass
                return [ParsedBlock(text="", metadata={"note": "deepdoc_empty_output"})]
            # 可选：多模态产物接入为 Chunk（基于 deepdoc caption/table 简化生成）
            try:
                if getattr(settings, "SM_MULTIMODAL_PARSE_ENABLED", False):
                    # 引入轻量 caption/table 转文本（如存在 deepdoc 的 caption/tag 信息）
                    extra_blocks: List[ParsedBlock] = []
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
                        elif "table" in t:
                            # 简化：表格文本直接归并；后续可转 Markdown
                            extra_blocks.append(ParsedBlock(text=text.strip(), metadata={"element_type": "table_struct"}))
                    if extra_blocks:
                        blocks.extend(extra_blocks)
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


