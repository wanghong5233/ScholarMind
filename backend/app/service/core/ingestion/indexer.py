from __future__ import annotations

from typing import Dict, Iterable
from service.core.rag.utils.es_conn import ESConnection
from core.config import settings
import hashlib


class ESIndexer:
    def __init__(self, index_name: str | None = None) -> None:
        self.index_name = index_name or settings.ES_DEFAULT_INDEX
        self.es = ESConnection()

    def index(self, *, records: Iterable[Dict], kb_id: int, document_id: int) -> None:
        docs = []
        for i, r in enumerate(records):
            meta = dict(r.get("metadata", {}))
            meta["kb_id"] = str(kb_id)
            meta["document_id"] = str(document_id)
            # 生成幂等 chunk id（若上游未提供）：sha256(kb_id|doc_id|index|text[:2048])
            base_id = meta.get("id")
            if base_id:
                chunk_id = base_id
            else:
                text_for_id = (r.get("text", "") or "")[:2048]
                raw = f"{kb_id}|{document_id}|{i}|{text_for_id}".encode("utf-8", errors="ignore")
                chunk_id = hashlib.sha256(raw).hexdigest()
            docs.append({
                "id": chunk_id,
                "text": r.get("text", ""),
                "vector": r.get("vector", []),
                **meta,
            })
        # 交给 ESConnection 批量写入（空列表则跳过）
        if docs:
            # 可观测性：记录写入的索引名
            try:
                import logging
                logging.getLogger('ragflow.es_conn').info(f"Indexing {len(docs)} chunks into index '{self.index_name}' for kb_id={kb_id}, document_id={document_id}")
            except Exception:
                pass
            _ = self.es.insert(docs, self.index_name)
        else:
            try:
                import logging
                logging.getLogger('ragflow.es_conn').info(f"Indexing skipped: 0 chunks for kb_id={kb_id}, document_id={document_id}, index='{self.index_name}'")
            except Exception:
                pass


