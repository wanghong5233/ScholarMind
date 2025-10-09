from __future__ import annotations
import os
from typing import Any, Dict
from schemas.document import DocumentCreate as DocumentCreateSchema
from service.document_service import find_document_by_file_hash, create_documents_bulk_for_kb
from service.job_handler.interfaces import BaseJobHandler, JobResult
from service.core.api.utils.file_storage import FileStorageUtil

class LocalUploadHandler(BaseJobHandler):
    def run(self, *, db, user_id: int, kb_id: int, payload: Dict[str, Any]) -> JobResult:
        files = (payload or {}).get("files", [])
        result = JobResult(total=len(files))
        doc_ids_to_parse = []

        for f_meta in files:
            try:
                dup = find_document_by_file_hash(db, kb_id, f_meta["sha256"])
                if dup:
                    result.details.append({
                        "filename": f_meta.get("original_name"),
                        "status": "duplicate",
                        "doc_id": dup.id,
                    })
                    if f_meta.get("temp_path"):
                        os.remove(f_meta["temp_path"])
                    result.succeeded += 1
                    doc_ids_to_parse.append(dup.id)
                else:
                    doc_schema = DocumentCreateSchema(
                        title=f_meta["original_name"],
                        file_hash=f_meta["sha256"],
                        ingestion_source="local_upload",
                    )
                    created = create_documents_bulk_for_kb(db=db, kb_id=kb_id, user_id=user_id, documents=[doc_schema])
                    doc = created[0] if created else find_document_by_file_hash(db, kb_id, f_meta["sha256"])
                    
                    final_path = FileStorageUtil.move_temp_to_final(f_meta["temp_path"], kb_id, doc.id, f_meta.get("original_name"))
                    doc.local_pdf_path = final_path
                    db.add(doc)
                    db.commit()
                    db.refresh(doc)

                    result.details.append({
                        "filename": f_meta.get("original_name"),
                        "status": "ok",
                        "doc_id": doc.id,
                        "local_path": final_path,
                    })
                    result.succeeded += 1
                    doc_ids_to_parse.append(doc.id)
            except Exception as e:
                result.failed += 1
                result.details.append({
                    "filename": f_meta.get("original_name", "unknown"),
                    "status": "failed",
                    "error": str(e),
                })
                if f_meta.get("temp_path") and os.path.exists(f_meta["temp_path"]):
                    os.remove(f_meta["temp_path"])
        
        result.doc_ids_to_parse = doc_ids_to_parse
        # 清理超时的临时文件
        try:
            FileStorageUtil.clean_tmp_dir(kb_id)
        except Exception:
            pass
        return result
