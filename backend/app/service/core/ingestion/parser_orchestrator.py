from __future__ import annotations

from typing import List
from utils.get_logger import log
from core.config import settings
from service.core.ingestion.interfaces import DocumentParser, ParsedBlock
from service.core.ingestion.document_parser import LightweightDocumentParser
from service.core.ingestion.mineru_parser import MinerUParser
from service.core.ingestion.unstructured_parser import UnstructuredParser


def _build_parser(name: str) -> DocumentParser:
    key = name.strip().lower()
    if key == "deepdoc":
        # 已弃用：保留名称兼容，回退为轻量解析器
        return LightweightDocumentParser()
    if key == "pymupdf":
        return LightweightDocumentParser()
    if key == "unstructured":
        return UnstructuredParser()
    if key == "mineru":
        return MinerUParser()
    return LightweightDocumentParser()


class ParserOrchestrator:
    """按顺序尝试多种解析器，返回首个非空结果。
    顺序由 settings.SM_PARSER_ORDER 控制。
    """

    def __init__(self, order: str | None = None) -> None:
        self.order = (order or settings.SM_PARSER_ORDER or "").split(",")

    def parse(self, *, file_path: str) -> List[ParsedBlock]:
        log.info(f"[PARSER_ORCHESTRATOR_START] file={file_path} order={','.join(self.order)}")
        last_err: Exception | None = None
        for idx, name in enumerate(self.order, 1):
            parser = _build_parser(name)
            try:
                log.info(f"[PARSER_TRY_{idx}] {parser.__class__.__name__}")
                blocks = parser.parse(file_path=file_path)
                if any((b.text or "").strip() for b in blocks):
                    log.info(f"[PARSER_SUCCESS] {parser.__class__.__name__} blocks={len(blocks)}")
                    return blocks
                log.warning(f"[PARSER_EMPTY] {parser.__class__.__name__}")
            except Exception as e:
                last_err = e
                log.error(f"[PARSER_FAIL] {parser.__class__.__name__} err={e}")
                # MinerU 失败直接抛出异常，不允许降级
                if "MinerU" in parser.__class__.__name__:
                    log.error(f"[CRITICAL] MinerU 解析失败，拒绝降级！请检查 MinerU 服务配置。")
                    raise RuntimeError(f"MinerU 解析失败: {e}. 请确保 SM_MINERU_ENDPOINT 正确配置且服务可用。")
                continue
        if last_err:
            log.error(f"[PARSER_ALL_FAILED] last_err={last_err}")
        # 如果所有解析器都失败，抛出异常而不是静默兜底
        raise RuntimeError(f"所有解析器失败。最后错误: {last_err}")


