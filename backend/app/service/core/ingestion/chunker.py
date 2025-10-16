from __future__ import annotations

from typing import Iterable, List
from core.config import settings
from service.core.ingestion.interfaces import ParsedBlock, Chunker


class RecursiveCharacterChunker(Chunker):
    def __init__(self, target_chars: int = 2000, overlap: int = 200) -> None:
        self.target_chars = target_chars
        self.overlap = overlap

    def chunk(self, *, blocks: Iterable[ParsedBlock]) -> List[ParsedBlock]:
        if getattr(settings, "SM_SEMANTIC_CHUNKING_ENABLED", False):
            return SemanticAwareChunker(target_chars=self.target_chars).chunk(blocks=blocks)
        results: List[ParsedBlock] = []
        for b in blocks:
            text = b.text or ""
            if not text:
                continue
            start = 0
            while start < len(text):
                end = min(len(text), start + self.target_chars)
                chunk_txt = text[start:end]
                results.append(ParsedBlock(text=chunk_txt, metadata=dict(b.metadata)))
                if end >= len(text):
                    break
                start = end - self.overlap
                if start < 0:
                    start = 0
        return results



class SemanticAwareChunker(Chunker):
    """基于句向量相似度突变的语义感知分块。
    - 先按句子切分
    - 计算相邻句向量余弦相似度
    - 相似度低于阈值或累计长度达到上限时切块
    """

    def __init__(self, target_chars: int = 2000, similarity_threshold: float = 0.75) -> None:
        self.target_chars = target_chars
        self.similarity_threshold = similarity_threshold

    def _split_sentences(self, text: str) -> List[str]:
        # 简易句切分（兼容中英）
        import re
        s = re.split(r"(?<=[。！？!?.])\s+|\n+", text.strip())
        return [t.strip() for t in s if t and t.strip()]

    def _embed(self, sents: List[str]) -> List[List[float]]:
        # 复用已有 Embedder（本地或API），以确保维度一致
        try:
            from service.core.ingestion.embedder import SimpleAPIEmbedder
            emb = SimpleAPIEmbedder()
            # 复用其内部批处理接口：构造伪 chunks
            chunks = [ParsedBlock(text=si, metadata={}) for si in sents]
            recs = emb.embed(chunks=chunks)
            return [r.get("vector") or [] for r in recs]
        except Exception:
            return [[] for _ in sents]

    def _cos(self, a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 1.0
        import math
        da = math.sqrt(sum(x * x for x in a))
        db = math.sqrt(sum(x * x for x in b))
        if da == 0 or db == 0:
            return 1.0
        dot = sum(x * y for x, y in zip(a, b))
        return max(-1.0, min(1.0, dot / (da * db)))

    def chunk(self, *, blocks: Iterable[ParsedBlock]) -> List[ParsedBlock]:
        results: List[ParsedBlock] = []
        for b in blocks:
            text = (b.text or "").strip()
            if not text:
                continue
            sents = self._split_sentences(text)
            if not sents:
                continue
            embs = self._embed(sents)
            buf: List[str] = []
            buf_vecs: List[List[float]] = []
            last_vec: List[float] | None = None
            for i, s in enumerate(sents):
                cur = s
                if not buf:
                    buf.append(cur)
                    v0 = embs[i] if i < len(embs) else None
                    if isinstance(v0, list) and v0:
                        buf_vecs.append(v0)
                        last_vec = v0
                    else:
                        last_vec = None
                    continue
                cur_vec = embs[i] if i < len(embs) else None
                sim = self._cos(last_vec or [], cur_vec or [])
                # 以 token 预算优先，字符预算兜底
                try:
                    from service.core.rag.service import RAGService as _RS
                    est = _RS()._estimate_tokens("\n".join(buf) + ("\n" if buf else "") + cur)
                    will_overflow = est >= max(int(getattr(settings, "SM_HISTORY_MAX_TOKENS", 2048) or 2048) // 2, self.target_chars)
                except Exception:
                    will_overflow = (sum(len(x) for x in buf) + 1 + len(cur)) >= self.target_chars
                if sim < self.similarity_threshold or will_overflow:
                    # 汇总当前块向量为 pre_embedding
                    md = dict(b.metadata)
                    if buf_vecs:
                        try:
                            import math
                            dim = len(buf_vecs[0])
                            acc = [0.0] * dim
                            for vv in buf_vecs:
                                if len(vv) == dim:
                                    for j in range(dim):
                                        acc[j] += float(vv[j])
                            md["pre_embedding"] = [x / max(len(buf_vecs), 1) for x in acc]
                        except Exception:
                            pass
                    results.append(ParsedBlock(text="\n".join(buf), metadata=md))
                    buf = [cur]
                    buf_vecs = [cur_vec] if isinstance(cur_vec, list) and cur_vec else []
                else:
                    buf.append(cur)
                    if isinstance(cur_vec, list) and cur_vec:
                        buf_vecs.append(cur_vec)
                last_vec = cur_vec
            if buf:
                md = dict(b.metadata)
                if buf_vecs:
                    try:
                        dim = len(buf_vecs[0])
                        acc = [0.0] * dim
                        for vv in buf_vecs:
                            if len(vv) == dim:
                                for j in range(dim):
                                    acc[j] += float(vv[j])
                        md["pre_embedding"] = [x / max(len(buf_vecs), 1) for x in acc]
                    except Exception:
                        pass
                results.append(ParsedBlock(text="\n".join(buf), metadata=md))
        return results
