from __future__ import annotations
import os
from typing import Any, Dict
from service.job_handler.interfaces import BaseJobHandler, JobResult
from utils.get_logger import log
from service.core.ingestion.document_parser import DeepdocDocumentParser
from service.core.ingestion.chunker import RecursiveCharacterChunker
from service.core.ingestion.embedder import SimpleAPIEmbedder
from service.core.ingestion.indexer import ESIndexer
from service.core.ingestion.metadata_extractor import DefaultMetadataExtractor
from service import document_service

class ParseIndexHandler(BaseJobHandler):
    def run(self, *, db, user_id: int, kb_id: int, payload: Dict[str, Any]) -> JobResult:
        doc_ids = (payload or {}).get("docs", [])
        result = JobResult(total=len(doc_ids))
        # 使用全局 loguru，保证输出格式一致

        parser = DeepdocDocumentParser()
        chunker = RecursiveCharacterChunker()
        embedder = SimpleAPIEmbedder()
        indexer = ESIndexer()
        metadata_extractor = DefaultMetadataExtractor()

        for doc_id in doc_ids:
            try:
                doc = document_service.get_document_by_id(db, doc_id, user_id, kb_id)
                if not doc.local_pdf_path or not os.path.exists(doc.local_pdf_path):
                    raise Exception("local file not found")

                blocks = parser.parse(file_path=doc.local_pdf_path)
                try:
                    log.info(f"ParseIndex: parsed blocks count doc_id={doc_id} count={len(blocks)}")
                except Exception:
                    pass
                doc = metadata_extractor.extract_and_enrich(db=db, document=doc, blocks=blocks)
                chunks = chunker.chunk(blocks=blocks)
                try:
                    log.info(f"ParseIndex: chunked count doc_id={doc_id} count={len(chunks)}")
                except Exception:
                    pass
                records = embedder.embed(chunks=chunks)
                try:
                    log.info(f"ParseIndex: embedded records count doc_id={doc_id} count={len(records)}")
                except Exception:
                    pass
                
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
                
                indexer.index(records=records, kb_id=kb_id, document_id=doc_id)
                result.details.append({"doc_id": doc_id, "status": "ok", "chunks": len(records)})
                result.succeeded += 1
            except Exception as e:
                result.details.append({"doc_id": doc_id, "status": "failed", "error": str(e)})
                result.failed += 1
        
        return result
