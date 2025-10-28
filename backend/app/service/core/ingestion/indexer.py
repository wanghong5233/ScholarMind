from __future__ import annotations

from typing import Dict, Iterable, Optional
from service.core.rag.utils.es_conn import ESConnection
from core.config import settings
import hashlib
import logging
import math
import time


class ESIndexer:
    def __init__(self, index_name: str | None = None) -> None:
        self.index_name = index_name or settings.ES_DEFAULT_INDEX
        self.es = ESConnection()

    def index(self, *, records: Iterable[Dict], kb_id: int, document_id: int, session_index: Optional[str] = None) -> None:
        docs = []
        records_list = list(records)  # 转换为列表以便访问相邻元素
        
        for i, r in enumerate(records_list):
            meta = dict(r.get("metadata", {}))
            meta["kb_id"] = str(kb_id)
            meta["document_id"] = str(document_id)
            meta["chunk_index"] = i  # 记录块在文档中的顺序
            
            # 生成幂等 chunk id（若上游未提供）：sha256(kb_id|doc_id|index|text[:2048])
            base_id = meta.get("id")
            if base_id:
                chunk_id = base_id
            else:
                text_for_id = (r.get("text", "") or "")[:2048]
                raw = f"{kb_id}|{document_id}|{i}|{text_for_id}".encode("utf-8", errors="ignore")
                chunk_id = hashlib.sha256(raw).hexdigest()
            
            # 生成相邻块的 ID
            prev_chunk_id = None
            next_chunk_id = None
            
            if i > 0:
                prev_r = records_list[i - 1]
                prev_base_id = prev_r.get("metadata", {}).get("id")
                if prev_base_id:
                    prev_chunk_id = prev_base_id
                else:
                    prev_text = (prev_r.get("text", "") or "")[:2048]
                    prev_raw = f"{kb_id}|{document_id}|{i-1}|{prev_text}".encode("utf-8", errors="ignore")
                    prev_chunk_id = hashlib.sha256(prev_raw).hexdigest()
            
            if i < len(records_list) - 1:
                next_r = records_list[i + 1]
                next_base_id = next_r.get("metadata", {}).get("id")
                if next_base_id:
                    next_chunk_id = next_base_id
                else:
                    next_text = (next_r.get("text", "") or "")[:2048]
                    next_raw = f"{kb_id}|{document_id}|{i+1}|{next_text}".encode("utf-8", errors="ignore")
                    next_chunk_id = hashlib.sha256(next_raw).hexdigest()
            
            # 添加相邻块 ID 到元数据
            if prev_chunk_id:
                meta["prev_chunk_id"] = prev_chunk_id
            if next_chunk_id:
                meta["next_chunk_id"] = next_chunk_id
            
            docs.append({
                "id": chunk_id,
                "text": r.get("text", ""),
                "vector": r.get("vector", []),
                **meta,
            })
        # 交给 ESConnection 批量写入（空列表则跳过）
        if docs:
            target_index = session_index or self.index_name
            # 可观测性：记录写入的索引名
            try:
                logging.getLogger('ragflow.es_conn').info(
                    f"Indexing {len(docs)} chunks into index '{target_index}' for kb_id={kb_id}, document_id={document_id}"
                )
            except Exception:
                pass

            # 分片与指数退避，缓解 429（coordinating bytes 限制）
            batch_size = getattr(settings, "ES_BULK_BATCH_SIZE", 500)
            max_retries = getattr(settings, "ES_BULK_MAX_RETRIES", 4)
            base_sleep = getattr(settings, "ES_BULK_BACKOFF_BASE_SECS", 1.0)

            total = len(docs)
            batches = math.ceil(total / max(1, batch_size))
            for b in range(batches):
                start = b * batch_size
                end = min(total, start + batch_size)
                slice_docs = docs[start:end]
                # 重试机制
                for attempt in range(max_retries + 1):
                    errs = self.es.insert(slice_docs, target_index)
                    if not errs:
                        break
                    # 仅对 429 or Timeout 退避（es_conn 会把异常转字符串）
                    err_str = "\n".join(errs)
                    if ("429" in err_str) or ("Timeout" in err_str) or ("time out" in err_str):
                        sleep_secs = base_sleep * (2 ** attempt)
                        try:
                            logging.getLogger('ragflow.es_conn').warning(
                                f"ES bulk retry due to transient error. attempt={attempt} sleep={sleep_secs}s batch={b+1}/{batches} size={len(slice_docs)}"
                            )
                        except Exception:
                            pass
                        time.sleep(sleep_secs)
                        continue
                    # 非可恢复错误，直接跳出重试
                    break
        else:
            try:
                import logging
                logging.getLogger('ragflow.es_conn').info(f"Indexing skipped: 0 chunks for kb_id={kb_id}, document_id={document_id}, index='{self.index_name}'")
            except Exception:
                pass


