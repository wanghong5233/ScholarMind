from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Any, Dict, List
from service.job_handler.interfaces import BaseJobHandler, JobResult
from utils.get_logger import log
from service.core.ingestion.parser_orchestrator import ParserOrchestrator
from service.core.ingestion.chunker import RecursiveCharacterChunker
from service.core.ingestion.interfaces import ParsedBlock
from core.config import settings
from service.core.ingestion.embedder import SimpleAPIEmbedder
from service.core.ingestion.indexer import ESIndexer
from service.core.ingestion.metadata_extractor import DefaultMetadataExtractor
from service.core.ingestion.structured_doc_builder import StructuredDocumentBuilder
from service import document_service

class ParseIndexHandler(BaseJobHandler):
    def run(self, *, db, user_id: int, kb_id: int, payload: Dict[str, Any]) -> JobResult:
        doc_ids = (payload or {}).get("docs", [])
        result = JobResult(total=len(doc_ids))
        # 使用全局 loguru，保证输出格式一致

        orchestrator = ParserOrchestrator()
        try:
            log.info(f"ParserOrchestratorLoaded: order={','.join(orchestrator.order)}")
        except Exception:
            pass
        chunker = RecursiveCharacterChunker()
        embedder = SimpleAPIEmbedder()
        indexer = ESIndexer()
        metadata_extractor = DefaultMetadataExtractor()
        structured_builder = StructuredDocumentBuilder()

        session_index = None
        try:
            sess_id = (payload or {}).get("sessionId")
            if sess_id:
                session_index = f"sm_sess_{sess_id}"
        except Exception:
            session_index = None

        for doc_id in doc_ids:
            try:
                doc = document_service.get_document_by_id(db, doc_id, user_id, kb_id)
                if not doc.local_pdf_path or not os.path.exists(doc.local_pdf_path):
                    raise Exception("local file not found")

                # 解析阶段 - 详细日志
                try:
                    log.info(f"[PARSE_START] doc_id={doc_id} file={doc.local_pdf_path} kb_id={kb_id}")
                    blocks = orchestrator.parse(file_path=doc.local_pdf_path)
                    
                    # 统计解析结果
                    total_blocks = len(blocks)
                    nonempty_blocks = sum(1 for b in blocks if (b.text or "").strip())
                    total_chars = sum(len((b.text or "").strip()) for b in blocks)
                    
                    # 统计 element_type 分布
                    element_types = {}
                    parser_engines = {}
                    for b in blocks:
                        et = b.metadata.get("element_type", "unknown")
                        pe = b.metadata.get("parser_engine", "unknown")
                        element_types[et] = element_types.get(et, 0) + 1
                        parser_engines[pe] = parser_engines.get(pe, 0) + 1
                    
                    log.info(
                        f"[PARSE_OK] doc_id={doc_id} total_blocks={total_blocks} nonempty={nonempty_blocks} "
                        f"total_chars={total_chars} element_types={element_types} parser_engines={parser_engines}"
                    )
                except Exception as e:
                    log.error(f"[PARSE_FAIL] doc_id={doc_id} path={doc.local_pdf_path} error={e}")
                    raise
                # 元数据提取阶段
                log.info(f"[METADATA_START] doc_id={doc_id}")
                doc = metadata_extractor.extract_and_enrich(db=db, document=doc, blocks=blocks)
                log.info(f"[METADATA_OK] doc_id={doc_id} title={doc.title[:50] if doc.title else 'N/A'} doi={doc.doi or 'N/A'}")
                
                # 结构化阶段
                log.info(f"[STRUCT_START] doc_id={doc_id}")
                structured_doc = structured_builder.build(document=doc, mineru_blocks=blocks)
                structured_blocks = structured_doc.to_parsed_blocks()
                log.info(
                    f"[STRUCT_OK] doc_id={doc_id} structured_blocks={len(structured_blocks)} "
                    f"logical_types={self._summarize_logical_types(structured_blocks)}"
                )
                snapshot = self._build_structure_snapshot(structured_doc)
                try:
                    doc.structure_metadata = snapshot
                    db.add(doc)
                    db.commit()
                    db.refresh(doc)
                except Exception as exc:
                    log.warning(f"[STRUCT_SNAPSHOT_SAVE_FAIL] doc_id={doc_id} err={exc}")

                # 分块阶段 - 详细日志
                try:
                    log.info(f"[CHUNK_START] doc_id={doc_id} input_blocks={len(structured_blocks)}")
                    chunks = chunker.chunk(blocks=structured_blocks)
                    
                    # 统计分块结果
                    total_chunks = len(chunks)
                    chunk_element_types = {}
                    for c in chunks:
                        et = c.metadata.get("element_type", "unknown")
                        chunk_element_types[et] = chunk_element_types.get(et, 0) + 1
                    
                    log.info(
                        f"[CHUNK_OK] doc_id={doc_id} output_chunks={total_chunks} "
                        f"element_types={chunk_element_types}"
                    )
                except Exception as e:
                    log.error(f"[CHUNK_FAIL] doc_id={doc_id} error={e}")
                    raise
                # 嵌入阶段 - 详细日志
                try:
                    log.info(f"[EMBED_START] doc_id={doc_id} input_chunks={len(chunks)}")
                    records = embedder.embed(chunks=chunks)
                    log.info(f"[EMBED_OK] doc_id={doc_id} output_records={len(records)}")
                except Exception as e:
                    log.error(f"[EMBED_FAIL] doc_id={doc_id} error={e}")
                    raise
                
                for rec in records:
                    md = rec.setdefault("metadata", {})
                    md.setdefault("kb_id", str(kb_id))
                    md.setdefault("document_id", str(doc_id))
                    md.setdefault("page", md.get("page", 1))
                    md.setdefault("offset_start", md.get("offset_start", 0))
                    md.setdefault("offset_end", md.get("offset_end", 0))
                    if doc.title:
                        md.setdefault("title", doc.title)
                    if doc.doi:
                        md.setdefault("doi", doc.doi)
                
                # 索引阶段 - 详细日志（包含多模态统计）
                try:
                    # 统计多模态字段
                    multimodal_stats = {
                        "table_json": 0,
                        "equation_latex": 0,
                        "figure_caption": 0,
                        "has_bbox": 0,
                        "has_confidence": 0,
                    }
                    for rec in records:
                        md = rec.get("metadata", {})
                        if md.get("table_json"):
                            multimodal_stats["table_json"] += 1
                        if md.get("equation_latex"):
                            multimodal_stats["equation_latex"] += 1
                        if md.get("figure_caption"):
                            multimodal_stats["figure_caption"] += 1
                        if md.get("bbox"):
                            multimodal_stats["has_bbox"] += 1
                        if md.get("confidence"):
                            multimodal_stats["has_confidence"] += 1
                    
                    log.info(
                        f"[INDEX_START] doc_id={doc_id} records={len(records)} "
                        f"multimodal={multimodal_stats} index={session_index or 'default'}"
                    )
                    indexer.index(records=records, kb_id=kb_id, document_id=doc_id, session_index=session_index)
                    log.info(f"[INDEX_OK] doc_id={doc_id}")
                except Exception as e:
                    log.error(f"[INDEX_FAIL] doc_id={doc_id} error={e}")
                    raise
                
                log.info(f"[DOC_COMPLETE] doc_id={doc_id} chunks={len(records)}")
                result.details.append({"doc_id": doc_id, "status": "ok", "chunks": len(records)})
                result.succeeded += 1
            except Exception as e:
                result.details.append({"doc_id": doc_id, "status": "failed", "error": str(e)})
                result.failed += 1
        
        return result

    def _summarize_logical_types(self, blocks: list[ParsedBlock]) -> dict[str, int]:
        summary: dict[str, int] = {}
        for blk in blocks:
            lt = (blk.metadata or {}).get("logical_type", "unknown")
            summary[lt] = summary.get(lt, 0) + 1
        return summary

    def _build_structure_snapshot(self, structured_doc) -> Dict[str, Any]:
        logical_counter: Dict[str, int] = {}
        for blk in structured_doc.blocks:
            logical_counter[blk.logical_type] = logical_counter.get(blk.logical_type, 0) + 1

        max_blocks = getattr(settings, "SM_STRUCTURED_SNAPSHOT_MAX_BLOCKS", 200)
        preview_blocks = []
        for blk in structured_doc.blocks[:max_blocks]:
            meta = blk.metadata or {}
            preview_blocks.append(
                {
                    "block_id": blk.block_id,
                    "logical_type": blk.logical_type,
                    "title": blk.title,
                    "structure_path": blk.structure_path,
                    "level": blk.level,
                    "page_range": meta.get("page_range"),
                    "alignment_status": meta.get("alignment_status"),
                    "source": meta.get("source"),
                    "text_preview": (blk.text or "")[:500],
                }
            )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_blocks": len(structured_doc.blocks),
            "logical_types": logical_counter,
            "blocks": preview_blocks,
        }
