from __future__ import annotations
from typing import Any, Dict, List
from schemas.document import DocumentCreate as DocumentCreateSchema
from service import document_service
from service.job_handler.interfaces import BaseJobHandler, JobResult
from service.core.api.utils.file_storage import FileStorageUtil
from models.document import Document

class OnlineIngestionHandler(BaseJobHandler):
    def run(self, *, db, user_id: int, kb_id: int, payload: Dict[str, Any]) -> JobResult:
        documents_payload = (payload or {}).get("documents", [])
        documents = [DocumentCreateSchema(**d) for d in documents_payload]

        created = document_service.create_documents_bulk_for_kb(db=db, kb_id=kb_id, user_id=user_id, documents=documents)
        newly_created_ids = {d.id for d in created}
        existing = document_service.find_existing_documents_for_payload(db=db, kb_id=kb_id, documents=documents)
        processing_list = created + [e for e in existing if e.id not in newly_created_ids]
        
        result = JobResult(total=len(processing_list))
        doc_ids_to_parse = []

        for doc in processing_list:
            try:
                resolved_url = None
                if doc.source_url:
                    resolved_url = FileStorageUtil.resolve_pdf_url(doc.source_url)
                
                self._download_pdf_and_update(db=db, kb_id=kb_id, doc=doc)
                
                result.succeeded += 1
                result_detail = {
                    "doc_id": doc.id,
                    "title": doc.title,
                    "status": "ok",
                    "local_pdf_path": doc.local_pdf_path,
                }
                if resolved_url and resolved_url != doc.source_url:
                    result_detail["note"] = "resolved_pdf_url"
                    result_detail["resolved_url"] = resolved_url
                result.details.append(result_detail)
                doc_ids_to_parse.append(doc.id)
            except Exception as e:
                result.failed += 1
                if doc.id in newly_created_ids:
                    try:
                        db.delete(doc)
                        db.commit()
                    except Exception:
                        db.rollback()
                result.details.append({
                    "doc_id": doc.id,
                    "title": doc.title,
                    "status": "failed",
                    "error": str(e),
                })
        
        result.doc_ids_to_parse = doc_ids_to_parse
        return result

    def _download_pdf_and_update(self, *, db, kb_id: int, doc: Document) -> None:
        if not doc.source_url:
            return
        try:
            local_path, sha256 = FileStorageUtil.download_pdf(
                url=doc.source_url,
                kb_id=kb_id,
                preferred_name=f"{doc.id}_{doc.title or 'paper'}"
            )
            doc.local_pdf_path = local_path
            doc.file_hash = sha256
            db.add(doc)
            db.commit()
            db.refresh(doc)
        except Exception as e:
            db.rollback()
            raise e
