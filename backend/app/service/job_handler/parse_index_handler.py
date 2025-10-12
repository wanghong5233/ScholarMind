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

                try:
                    blocks = parser.parse(file_path=doc.local_pdf_path)
                except Exception as e:
                    log.error(f"ParseIndex: parse failed doc_id={doc_id} path={doc.local_pdf_path}: {e}")
                    raise
                try:
                    log.info(f"ParseIndex: parsed blocks count doc_id={doc_id} count={len(blocks)}")
                except Exception:
                    pass
                doc = metadata_extractor.extract_and_enrich(db=db, document=doc, blocks=blocks)
                try:
                    chunks = chunker.chunk(blocks=blocks)
                except Exception as e:
                    log.error(f"ParseIndex: chunk failed doc_id={doc_id}: {e}")
                    raise
                try:
                    log.info(f"ParseIndex: chunked count doc_id={doc_id} count={len(chunks)}")
                except Exception:
                    pass
                try:
                    records = embedder.embed(chunks=chunks)
                except Exception as e:
                    log.error(f"ParseIndex: embed failed doc_id={doc_id}: {e}")
                    raise
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
                
                try:
                    indexer.index(records=records, kb_id=kb_id, document_id=doc_id, session_index=session_index)
                except Exception as e:
                    log.error(f"ParseIndex: index failed doc_id={doc_id}: {e}")
                    raise
                result.details.append({"doc_id": doc_id, "status": "ok", "chunks": len(records)})
                result.succeeded += 1
            except Exception as e:
                result.details.append({"doc_id": doc_id, "status": "failed", "error": str(e)})
                result.failed += 1
        
        return result
