from __future__ import annotations
from typing import Any, Dict, List, Optional

import httpx

from schemas.document import DocumentCreate as DocumentCreateSchema
from service import document_service
from service.job_handler.interfaces import BaseJobHandler, JobResult
from service.core.api.utils.file_storage import FileStorageUtil
from models.document import Document
from service.job_handler.parse_index_handler import ParseIndexHandler


class PdfDownloadSkipped(Exception):
    """表示 PDF 下载被跳过（无 PDF 或需人工处理）。"""

    def __init__(self, reason: str, link: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.link = link


class OnlineIngestionHandler(BaseJobHandler):
    def run(self, *, db, user_id: int, kb_id: int, payload: Dict[str, Any]) -> JobResult:
        documents_payload = (payload or {}).get("documents", [])
        documents = [DocumentCreateSchema(**d) for d in documents_payload]

        created = document_service.create_documents_bulk_for_kb(db=db, kb_id=kb_id, user_id=user_id, documents=documents)
        newly_created_ids = {d.id for d in created}
        existing = document_service.find_existing_documents_for_payload(db=db, kb_id=kb_id, documents=documents)
        processing_list = created + [e for e in existing if e.id not in newly_created_ids]

        result = JobResult(total=len(processing_list))
        doc_ids_to_parse: List[int] = []
        doc_entries: Dict[int, Dict[str, Any]] = {}
        result_entries: List[Dict[str, Any]] = []

        for doc in processing_list:
            try:
                status, note, manual_link = self._attempt_download(db=db, kb_id=kb_id, doc=doc)
            except PdfDownloadSkipped as skip_reason:
                status = "skipped_pdf"
                note = skip_reason.reason
                manual_link = skip_reason.link or doc.source_url

                if doc.id in newly_created_ids:
                    try:
                        db.delete(doc)
                        db.commit()
                    except Exception:
                        db.rollback()

                result.failed += 1
                result_entries.append({
                    "doc_id": doc.id,
                    "title": doc.title,
                    "download_status": "skipped",
                    "parse_status": "not_applicable",
                    "note": note,
                    "manual_download_url": manual_link,
                })
                continue
            except Exception as e:
                result.failed += 1
                if doc.id in newly_created_ids:
                    try:
                        db.delete(doc)
                        db.commit()
                    except Exception:
                        db.rollback()
                result_entries.append({
                    "doc_id": doc.id,
                    "title": doc.title,
                    "download_status": "failed",
                    "parse_status": "not_applicable",
                    "note": str(e),
                })
                continue

            entry: Dict[str, Any] = {
                "doc_id": doc.id,
                "title": doc.title,
                "download_status": status,
                "manual_download_url": manual_link,
            }
            if note:
                entry["note"] = note
            if doc.local_pdf_path:
                entry["local_pdf_path"] = doc.local_pdf_path

            doc_entries[doc.id] = entry
            doc_ids_to_parse.append(doc.id)

        parse_results: Dict[int, Dict[str, Any]] = {}
        if doc_ids_to_parse:
            parse_handler = ParseIndexHandler()
            try:
                parse_job_result = parse_handler.run(
                    db=db,
                    user_id=user_id,
                    kb_id=kb_id,
                    payload={"docs": doc_ids_to_parse},
                )
                for detail in parse_job_result.details:
                    doc_id = detail.get("doc_id")
                    if doc_id is not None:
                        parse_results[int(doc_id)] = detail
            except Exception as e:
                for doc_id in doc_ids_to_parse:
                    entry = doc_entries.get(doc_id) or {"doc_id": doc_id}
                    entry["parse_status"] = "failed"
                    entry["parse_error"] = str(e)
                    result_entries.append(entry)
                    result.failed += 1
                result.doc_ids_to_parse = []
                result.details.extend(result_entries)
                return result

        for doc_id in doc_ids_to_parse:
            entry = doc_entries.get(doc_id) or {"doc_id": doc_id}
            parse_detail = parse_results.get(doc_id)
            if parse_detail and parse_detail.get("status") == "ok":
                entry["parse_status"] = "parsed"
                if parse_detail.get("chunks") is not None:
                    entry["chunks"] = parse_detail.get("chunks")
                result.succeeded += 1
            else:
                entry["parse_status"] = "failed"
                if parse_detail and parse_detail.get("error"):
                    entry["parse_error"] = parse_detail.get("error")
                result.failed += 1
            result_entries.append(entry)

        result.details.extend(result_entries)
        result.doc_ids_to_parse = []
        return result

    def _attempt_download(self, *, db, kb_id: int, doc: Document) -> tuple[str, Optional[str], Optional[str]]:
        """尝试下载 PDF，返回 (status, note, manual_link)。status=downloaded 或 skipped。"""

        if not doc.source_url:
            raise PdfDownloadSkipped("缺少 source_url，无法自动下载 PDF")

        try:
            local_path, sha256 = FileStorageUtil.download_pdf(
                url=doc.source_url,
                kb_id=kb_id,
                preferred_name=f"{doc.id}_{doc.title or 'paper'}",
            )
        except ValueError as err:
            raise PdfDownloadSkipped(str(err), link=doc.source_url) from err
        except httpx.HTTPStatusError as err:
            if err.response.status_code in {202, 403, 404}:
                manual_url = doc.source_url or str(err.request.url)
                raise PdfDownloadSkipped(
                    f"远程站点返回 {err.response.status_code}，需手动下载原文",
                    link=manual_url,
                ) from err
            raise
        except Exception as err:
            db.rollback()
            raise err
        else:
            doc.local_pdf_path = local_path
            doc.file_hash = sha256
            db.add(doc)
            db.commit()
            db.refresh(doc)
            return "downloaded", None, None
