"""Retrieval evaluation replay service."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.config import settings
from models.user import User
from schemas.retrieval_debug import (
    RetrievalCompareRequest,
    RetrievalEvalItem,
    RetrievalEvalItemResult,
    RetrievalEvalRunRequest,
    RetrievalEvalRunResponse,
)
from service.core.api.utils.file_utils import get_project_base_directory
from service.core.conversation.retrieval_compare_service import RetrievalCompareService


class RetrievalEvalService:
    """Run retrieval evaluation over a predefined query set."""

    def __init__(self, *, db: Session, current_user: User) -> None:
        self.db = db
        self.current_user = current_user
        self.compare_service = RetrievalCompareService(db=db, current_user=current_user)

    def list_eval_sets(self) -> Dict[str, Any]:
        data = self._load_eval_sets()
        summary = {}
        for name, item in data.items():
            if not isinstance(item, dict):
                continue
            desc = item.get("description") or ""
            items = item.get("items") if isinstance(item.get("items"), list) else []
            summary[name] = {"description": desc, "count": len(items)}
        return summary

    def run(self, *, payload: RetrievalEvalRunRequest) -> RetrievalEvalRunResponse:
        items = self._resolve_items(payload)
        if not items:
            raise HTTPException(status_code=400, detail="评估集为空")

        max_items = int(getattr(settings, "SM_RETRIEVAL_EVAL_MAX_ITEMS", 50) or 50)
        if payload.limit:
            max_items = min(max_items, int(payload.limit))
        items = items[:max_items]

        results: List[RetrievalEvalItemResult] = []
        overlap_chunk_ratio_sum = 0.0
        overlap_doc_ratio_sum = 0.0
        latency_a_sum = 0
        latency_b_sum = 0
        chunk_count_a_sum = 0
        chunk_count_b_sum = 0
        doc_count_a_sum = 0
        doc_count_b_sum = 0

        for item in items:
            compare_payload = RetrievalCompareRequest(
                kb_id=payload.kb_id,
                query=item.query,
                top_k=payload.top_k,
                provider_a=payload.provider_a,
                provider_b=payload.provider_b,
                session_id=payload.session_id,
                focus_doc_ids=item.focus_doc_ids,
                index_mode=item.index_mode or payload.index_mode,
            )
            compare_resp = self.compare_service.handle(payload=compare_payload)
            panel = compare_resp.panel
            overlap = compare_resp.overlap

            overlap_chunk_ratio_sum += float(overlap.get("chunk_overlap_ratio") or 0.0)
            overlap_doc_ratio_sum += float(overlap.get("doc_overlap_ratio") or 0.0)

            provider_a_info = panel.get("provider_a") or {}
            provider_b_info = panel.get("provider_b") or {}
            latency_a_sum += int(provider_a_info.get("latency_ms") or 0)
            latency_b_sum += int(provider_b_info.get("latency_ms") or 0)

            chunk_count_a = int(provider_a_info.get("chunk_count") or len(compare_resp.a.chunks))
            chunk_count_b = int(provider_b_info.get("chunk_count") or len(compare_resp.b.chunks))
            chunk_count_a_sum += chunk_count_a
            chunk_count_b_sum += chunk_count_b

            doc_count_a = int(provider_a_info.get("doc_count") or 0)
            doc_count_b = int(provider_b_info.get("doc_count") or 0)
            doc_count_a_sum += doc_count_a
            doc_count_b_sum += doc_count_b

            results.append(
                RetrievalEvalItemResult(
                    query=item.query,
                    note=item.note,
                    overlap=overlap,
                    panel=panel,
                )
            )

        total = len(results)
        summary = {
            "items": total,
            "avg_chunk_overlap_ratio": overlap_chunk_ratio_sum / max(total, 1),
            "avg_doc_overlap_ratio": overlap_doc_ratio_sum / max(total, 1),
            "avg_latency_ms": {
                "a": int(latency_a_sum / max(total, 1)),
                "b": int(latency_b_sum / max(total, 1)),
            },
            "avg_chunk_count": {
                "a": int(chunk_count_a_sum / max(total, 1)),
                "b": int(chunk_count_b_sum / max(total, 1)),
            },
            "avg_doc_count": {
                "a": int(doc_count_a_sum / max(total, 1)),
                "b": int(doc_count_b_sum / max(total, 1)),
            },
        }

        return RetrievalEvalRunResponse(
            run_id=str(uuid.uuid4()),
            eval_set=payload.eval_set or "custom",
            total_items=total,
            provider_a=payload.provider_a,
            provider_b=payload.provider_b,
            summary=summary,
            items=results,
        )

    def _resolve_items(self, payload: RetrievalEvalRunRequest) -> List[RetrievalEvalItem]:
        if payload.items:
            return payload.items
        if payload.eval_set:
            data = self._load_eval_sets()
            raw = data.get(payload.eval_set)
            if not raw or not isinstance(raw, dict):
                raise HTTPException(status_code=404, detail="评估集不存在")
            items = raw.get("items") if isinstance(raw.get("items"), list) else []
            return [RetrievalEvalItem(**item) for item in items if isinstance(item, dict)]
        data = self._load_eval_sets()
        default = data.get("academic_basic") or {}
        items = default.get("items") if isinstance(default.get("items"), list) else []
        return [RetrievalEvalItem(**item) for item in items if isinstance(item, dict)]

    def _load_eval_sets(self) -> Dict[str, Any]:
        raw_path = getattr(settings, "SM_RETRIEVAL_EVAL_FILE", "conf/retrieval_eval_sets.json")
        path = raw_path
        if not os.path.isabs(raw_path):
            path = get_project_base_directory(raw_path)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}
