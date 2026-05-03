from __future__ import annotations
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from models.document import Document, DocumentProcessingStatus
from schemas.document import DocumentCreate as DocumentCreateSchema
from service import document_service
from service.document_lifecycle import mark_failed, mark_pending, reset_for_retry
from service.job_handler.interfaces import BaseJobHandler, JobResult
from service.core.api.utils.file_storage import FileStorageUtil
from service.job_handler.parse_index_handler import ParseIndexHandler


class PdfDownloadSkipped(Exception):
    """Recoverable download outcome: no PDF link, paywall, or rate-limit.

    The doc row is preserved with status='failed' and ``failure_stage='download'``;
    the user can open the manual link from the UI or trigger a retry.
    """

    def __init__(self, reason: str, link: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.link = link


class OnlineIngestionHandler(BaseJobHandler):
    """Bulk-import documents discovered via Semantic Scholar / arXiv.

    Lifecycle contract: every doc seen by this handler ends in exactly one of
        - status='ready'   (download + parse + index OK)
        - status='failed'  (any stage failed; failure_stage / failure_reason
                            point to the offending step)
    The handler never deletes rows. The KB UI surfaces failed rows with a
    retry button, so the user can choose to redownload or remove.
    """

    def run(self, *, db, user_id: int, kb_id: int, payload: Dict[str, Any]) -> JobResult:
        documents_payload = (payload or {}).get("documents", [])
        documents = [DocumentCreateSchema(**d) for d in documents_payload]

        created = document_service.create_documents_bulk_for_kb(
            db=db, kb_id=kb_id, user_id=user_id, documents=documents
        )
        # OnlineIngestion drives every doc through the full lifecycle, so its
        # job-level success count must match the doc-level 'ready' count.
        # (The internally chained ParseIndexHandler invocation also sets this
        # flag, which is fine - reconcile is idempotent.)
        newly_created_ids = {d.id for d in created}
        existing = document_service.find_existing_documents_for_payload(
            db=db, kb_id=kb_id, documents=documents
        )
        processing_list = created + [e for e in existing if e.id not in newly_created_ids]

        # Treat re-imports of previously failed docs as user-driven retries:
        # the row had failure metadata stuck on it, which we now clear so the
        # download stage runs from scratch.
        for doc in processing_list:
            if doc.id in newly_created_ids:
                mark_pending(db, doc)
            elif doc.processing_status == DocumentProcessingStatus.FAILED.value:
                reset_for_retry(db, doc)

        result = JobResult(total=len(processing_list), reconcile_with_lifecycle=True)
        doc_ids_to_parse: List[int] = []
        doc_entries: Dict[int, Dict[str, Any]] = {}
        result_entries: List[Dict[str, Any]] = []

        for doc in processing_list:
            try:
                status, note, manual_link = self._attempt_download(db=db, kb_id=kb_id, doc=doc)
            except PdfDownloadSkipped as skip_reason:
                manual_link = skip_reason.link or doc.source_url
                mark_failed(db, doc, stage="download", reason=skip_reason.reason)
                result.failed += 1
                result_entries.append({
                    "status": "skipped_pdf",
                    "doc_id": doc.id,
                    "title": doc.title,
                    "download_status": "skipped",
                    "parse_status": "not_applicable",
                    "note": skip_reason.reason,
                    "manual_download_url": manual_link,
                })
                continue
            except Exception as e:
                mark_failed(db, doc, stage="download", reason=str(e))
                result.failed += 1
                result_entries.append({
                    "status": "failed",
                    "doc_id": doc.id,
                    "title": doc.title,
                    "download_status": "failed",
                    "parse_status": "not_applicable",
                    "note": str(e),
                })
                continue

            entry: Dict[str, Any] = {
                "status": "ok",
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

        # Hand off to ParseIndexHandler. It owns its own state transitions
        # (parsing -> ready/failed) so we don't need to clean up after it.
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
                # Catastrophic ParseIndexHandler failure: it should have
                # written failed status itself, but be defensive in case it
                # raised before transitioning the rows.
                for doc_id in doc_ids_to_parse:
                    entry = doc_entries.get(doc_id) or {"doc_id": doc_id}
                    entry["parse_status"] = "failed"
                    entry["parse_error"] = str(e)
                    result_entries.append(entry)
                    result.failed += 1
                    doc_obj = db.query(Document).get(doc_id)
                    if doc_obj is not None and doc_obj.processing_status != DocumentProcessingStatus.FAILED.value:
                        mark_failed(db, doc_obj, stage="parse", reason=str(e))
                result.doc_ids_to_parse = []
                result.touched_doc_ids = [doc.id for doc in processing_list]
                result.details.extend(result_entries)
                return result

        for doc_id in doc_ids_to_parse:
            entry = doc_entries.get(doc_id) or {"doc_id": doc_id}
            parse_detail = parse_results.get(doc_id)
            if parse_detail and parse_detail.get("status") == "ok":
                entry["status"] = "ok"
                entry["parse_status"] = "parsed"
                if parse_detail.get("chunks") is not None:
                    entry["chunks"] = parse_detail.get("chunks")
                result.succeeded += 1
            else:
                entry["status"] = "failed"
                entry["parse_status"] = "failed"
                if parse_detail and parse_detail.get("error"):
                    entry["parse_error"] = parse_detail.get("error")
                result.failed += 1
            result_entries.append(entry)

        result.details.extend(result_entries)
        result.doc_ids_to_parse = []
        result.touched_doc_ids = [doc.id for doc in processing_list]
        return result

    def _attempt_download(self, *, db, kb_id: int, doc: Document) -> tuple[str, Optional[str], Optional[str]]:
        """Try to download the PDF; on success the doc row is updated in place.

        Returns ``(status, note, manual_link)`` so the caller can record the
        download outcome alongside the per-doc result entry.
        """

        if not doc.source_url:
            raise PdfDownloadSkipped("缺少 source_url，无法自动下载 PDF")

        parsed_source = urlparse(doc.source_url)
        if "semanticscholar.org" in parsed_source.netloc and "/paper/" in parsed_source.path:
            raise PdfDownloadSkipped(
                "Semantic Scholar 未提供 PDF 直链，仅返回论文详情页，需手动下载原文",
                link=doc.source_url,
            )

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
