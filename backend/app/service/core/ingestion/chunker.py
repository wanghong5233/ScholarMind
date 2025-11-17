from __future__ import annotations

from typing import Iterable, List, Tuple, Dict, Any
from core.config import settings
from service.core.ingestion.constants import is_multimodal_metadata
from service.core.ingestion.interfaces import ParsedBlock, Chunker
from utils.get_logger import log


def _normalize_page_range(value: Any, fallback: Any = None) -> List[int]:
    pages: List[int] = []
    candidates = []
    if value is not None:
        candidates.append(value)
    if fallback is not None:
        candidates.append(fallback)
    for cand in candidates:
        if cand is None:
            continue
        if isinstance(cand, int):
            pages.append(int(cand))
        elif isinstance(cand, list):
            for item in cand:
                try:
                    pages.append(int(item))
                except Exception:
                    continue
        elif isinstance(cand, (tuple, set)):
            for item in cand:
                try:
                    pages.append(int(item))
                except Exception:
                    continue
    seen: List[int] = []
    for p in pages:
        if p not in seen:
            seen.append(p)
    return seen


def _merge_page_ranges(metas: List[Dict[str, Any]]) -> List[int]:
    combined: List[int] = []
    for meta in metas:
        if not isinstance(meta, dict):
            continue
        rng = _normalize_page_range(meta.get("page_range"), meta.get("page"))
        for p in rng:
            if p not in combined:
                combined.append(p)
    return combined


def _merge_short_chunks(chunks: List[ParsedBlock]) -> List[ParsedBlock]:
    """
    历史遗留函数：在结构化重构后不再合并短块，仅负责清理空文本。
    （保留函数签名，以兼容旧逻辑调用。）
    """
    if not chunks:
        return []
    return [c for c in chunks if (c.text or "").strip()]


def _is_multimodal_block(block: ParsedBlock) -> bool:
    return is_multimodal_metadata(block.metadata)


def _produce_chunk(
    block: ParsedBlock,
    text: str,
    index: int,
    total: int,
    start: int,
    end: int,
    override_metadata: Dict[str, Any] | None = None,
) -> ParsedBlock:
    """
    生成 chunk 时，完整保留所有结构化元数据，确保数据管道完整性。
    
    保留的关键元数据：
    - 结构信息: structure_path, structure_title, logical_type, element_type
    - 位置信息: page_range, page, bbox_list
    - 文档信息: document_title, document_name, doi
    - 分块信息: structure_chunk_index, structure_chunk_total, offset_start, offset_end
    - 其他: source, alignment_status, parser_engine, 等等
    """
    md = dict(block.metadata or {})
    if override_metadata:
        md.update(override_metadata)

    # 页码范围处理
    pages = _normalize_page_range(md.get("page_range"), md.get("page"))
    if pages:
        md["page_range"] = pages
        md.setdefault("page", pages[0])

    # 分块索引信息
    md["structure_chunk_index"] = index - 1  # 从 0 开始，方便前端显示
    md["structure_chunk_total"] = total
    md["offset_start"] = start
    md["offset_end"] = end
    
    # 结构化元数据（确保存在）
    md.setdefault("logical_type", (block.metadata or {}).get("logical_type"))
    md.setdefault("element_type", md.get("logical_type") or (block.metadata or {}).get("element_type"))
    md.setdefault("structure_path", (block.metadata or {}).get("structure_path"))
    if block.metadata.get("structure_title"):
        md.setdefault("structure_title", block.metadata.get("structure_title"))
    if block.metadata.get("title"):
        md.setdefault("title", block.metadata.get("title"))
    
    # 位置信息（bbox_list）- 关键！用于前端精确定位
    if block.metadata.get("bbox_list"):
        md.setdefault("bbox_list", block.metadata.get("bbox_list"))
    
    # 对齐状态和来源信息
    if block.metadata.get("alignment_status"):
        md.setdefault("alignment_status", block.metadata.get("alignment_status"))
    if block.metadata.get("source"):
        md.setdefault("source", block.metadata.get("source"))
    if block.metadata.get("parser_engine"):
        md.setdefault("parser_engine", block.metadata.get("parser_engine"))

    return ParsedBlock(text=text, metadata=md)


class RecursiveCharacterChunker(Chunker):
    """递归字符分块器（兜底方案）
    
    学术 RAG 最佳实践：
    - target_chars: 800 (约 512 tokens，适合学术论文的段落长度)
    - overlap: 100 (约 12.5%，保证上下文连续性)
    """
    def __init__(self, target_chars: int = 800, overlap: int = 100) -> None:
        self.target_chars = target_chars
        self.overlap = overlap

    def _min_chunk_chars(self) -> int:
        return max(int(getattr(settings, "SM_CHUNK_MIN_CHARS", 200)), 100)

    def _find_chunk_boundary(self, text: str, start: int, preferred_end: int) -> int:
        """
        在首选终点附近寻找更自然的切分点（段落/句末），否则退回到首选位置。
        """
        length = len(text)
        preferred_end = min(length, max(start + self._min_chunk_chars(), preferred_end))
        back_window = max(int(getattr(settings, "SM_CHUNK_BREAK_BACK_WINDOW", 180)), 50)
        forward_window = max(int(getattr(settings, "SM_CHUNK_BREAK_FORWARD_WINDOW", 120)), 20)
        markers = ["\n\n", "\n", "。", "！", "？", ".", "!", "?"]

        # 1) 向后（优先选择靠近目标长度的断点）
        search_start = max(start + 1, preferred_end - back_window)
        snippet = text[search_start:preferred_end]
        for marker in markers:
            idx = snippet.rfind(marker)
            if idx != -1 and (search_start + idx) > start:
                return search_start + idx + len(marker)

        # 2) 向前（若后向没有命中，则允许稍超出目标长度）
        search_end = min(length, preferred_end + forward_window)
        snippet = text[preferred_end:search_end]
        for marker in markers:
            idx = snippet.find(marker)
            if idx != -1:
                pos = preferred_end + idx + len(marker)
                if pos - start >= self._min_chunk_chars():
                    return pos

        # 3) 兜底：直接使用首选终点
        return preferred_end

    def chunk(self, *, blocks: Iterable[ParsedBlock]) -> List[ParsedBlock]:
        # 结构优先：先按结构块迭代，内部再做长度切分
        block_list: List[ParsedBlock] = [b for b in blocks if (b.text or "").strip()]
        if getattr(settings, "SM_SEMANTIC_CHUNKING_ENABLED", False):
            # 从配置读取 SOTA 参数
            target = getattr(settings, "SM_CHUNK_TARGET_CHARS", 800)
            min_chars = getattr(settings, "SM_CHUNK_MIN_CHARS", 200)
            max_chars = getattr(settings, "SM_CHUNK_MAX_CHARS", 1200)
            sim_threshold = getattr(settings, "SM_SEMANTIC_SIMILARITY_THRESHOLD", 0.72)
            
            try:
                log.info(
                    f"SemanticAwareChunker enabled: input_blocks={len(block_list)} "
                    f"target={target} min={min_chars} max={max_chars} sim_threshold={sim_threshold}"
                )
            except Exception:
                pass
            chunks = SemanticAwareChunker(
                target_chars=target,
                min_chunk_chars=min_chars,
                max_chunk_chars=max_chars,
                similarity_threshold=sim_threshold
            ).chunk(blocks=block_list)
            try:
                log.info(
                    f"SemanticAwareChunker output_chunks={len(chunks)}"
                )
            except Exception:
                pass
            return chunks
        results: List[ParsedBlock] = []
        for block in block_list:
            text = (block.text or "").strip()
            if not text:
                continue
            # Chunker 不负责过滤多模态块，只负责切分
            # 多模态块直接保留，由 Indexer 统一过滤
            pieces = self._split_block(text)
            total = len(pieces)
            for idx, (chunk_text, start, end) in enumerate(pieces, start=1):
                results.append(
                    _produce_chunk(
                        block=block,
                        text=chunk_text,
                        index=idx,
                        total=total,
                        start=start,
                        end=end,
                    )
                )

        results = _merge_short_chunks(results)

        try:
            log.info(
                f"RecursiveCharacterChunker output_chunks={len(results)} input_blocks={len(block_list)} target_chars={self.target_chars}"
            )
        except Exception:
            pass
        return results

    def _split_block(self, text: str) -> List[Tuple[str, int, int]]:
        """按 target/overlap 在单个结构块内部切分，返回 (chunk_text, start, end) 列表。"""
        pieces: List[Tuple[str, int, int]] = []
        if not text:
            return pieces
        start = 0
        length = len(text)
        target = max(self.target_chars, 200)
        overlap = min(self.overlap, target - 50) if target > 50 else 0
        while start < length:
            preferred_end = min(length, start + target)
            boundary = self._find_chunk_boundary(text, start, preferred_end)
            chunk_txt = text[start:boundary].strip()
            if chunk_txt:
                pieces.append((chunk_txt, start, boundary))
            if boundary >= length:
                break
            next_start = boundary - overlap
            if next_start <= start:
                next_start = boundary
            start = next_start
        return pieces



class SemanticAwareChunker(Chunker):
    """基于句向量相似度突变的语义感知分块（学术 RAG SOTA 方案）
    
    核心策略：
    - 先按句子切分（保留语义完整性）
    - 计算相邻句向量余弦相似度
    - 相似度低于阈值或累计长度达到上限时切块
    
    学术 RAG 最佳实践参数：
    - target_chars: 800 (约 512 tokens，适配 OpenAI/Qwen embedding 最佳窗口)
    - min_chunk_chars: 200 (约 128 tokens，避免碎片化)
    - max_chunk_chars: 1200 (约 768 tokens，硬上限防止溢出)
    - similarity_threshold: 0.72 (学术文本主题连贯性强，阈值适中)
    """

    def __init__(
        self, 
        target_chars: int = 800, 
        min_chunk_chars: int = 200,
        max_chunk_chars: int = 1200,
        similarity_threshold: float = 0.72
    ) -> None:
        self.target_chars = target_chars
        self.min_chunk_chars = min_chunk_chars
        self.max_chunk_chars = max_chunk_chars
        self.similarity_threshold = similarity_threshold

    def _split_sentences(self, text: str) -> List[str]:
        # 简易句切分（兼容中英）
        import re
        s = re.split(r"(?<=[。！？!?.])\s+|\n+", text.strip())
        return [t.strip() for t in s if t and t.strip()]

    def _split_block_sliding(self, block: ParsedBlock) -> List[ParsedBlock]:
        """滑窗兜底：当块长度远超 max_chunk_chars 时使用固定窗口切分。"""
        text = (block.text or "").strip()
        if not text:
            return []

        target = getattr(settings, "SM_CHUNK_TARGET_CHARS", self.target_chars)
        overlap = getattr(settings, "SM_CHUNK_OVERLAP_CHARS", 150)
        target = max(target, 400)  # IEEE 首段较长，兜底窗口稍大
        overlap = max(min(overlap, target // 3), 50)

        raw_pieces: List[Tuple[str, int, int]] = []
        start = 0
        length = len(text)
        while start < length:
            end = min(length, start + target)
            chunk_txt = text[start:end]
            raw_pieces.append((chunk_txt, start, end))
            if end >= length:
                break
            start = end - overlap
            if start < 0:
                start = 0

        total = len(raw_pieces)
        return [
            _produce_chunk(block=block, text=chunk_txt, index=idx + 1, total=total, start=piece_start, end=piece_end)
            for idx, (chunk_txt, piece_start, piece_end) in enumerate(raw_pieces)
        ]

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
    
    def _chunk_block_level(self, blocks: List[ParsedBlock]) -> List[ParsedBlock]:
        """块级保护模式：保持已有结构块，仅对过长块做内部切分。"""
        valid_blocks = [b for b in blocks if (b.text or "").strip()]
        if not valid_blocks:
            return []

        final_results: List[ParsedBlock] = []
        for block in valid_blocks:
            text = (block.text or "").strip()
            if not text:
                continue

            # Chunker 不负责过滤多模态块，只负责切分
            # 多模态块直接保留，由 Indexer 统一过滤

            if len(text) <= self.max_chunk_chars:
                final_results.append(
                    _produce_chunk(
                        block=block,
                        text=text,
                        index=1,
                        total=1,
                        start=0,
                        end=len(text),
                    )
                )
                continue

            # 回退策略：按句子切分，保持 target/max 约束；若分句失败则使用滑窗
            sents = self._split_sentences(text)
            if not sents:
                final_results.extend(self._split_block_sliding(block))
                continue

            chunk_texts: List[str] = []
            buf: List[str] = []
            for sent in sents:
                tentative = ("\n".join(buf) + ("\n" if buf else "") + sent) if buf else sent
                if len(tentative) > self.max_chunk_chars and buf:
                    chunk_texts.append("\n".join(buf))
                    buf = [sent]
                    continue
                buf.append(sent)
            if buf:
                chunk_texts.append("\n".join(buf))

            total = len(chunk_texts)
            for idx, chunk_text in enumerate(chunk_texts, start=1):
                final_results.append(
                    _produce_chunk(
                        block=block,
                        text=chunk_text,
                        index=idx,
                        total=total,
                        start=0,
                        end=len(chunk_text),
                    )
                )

        try:
            log.info(
                f"SemanticAwareChunker.block_level: input={len(valid_blocks)} output={len(final_results)}"
            )
        except Exception:
            pass
        return final_results

    def chunk(self, *, blocks: Iterable[ParsedBlock]) -> List[ParsedBlock]:
        # 保障可重复遍历与统计
        _blocks: List[ParsedBlock] = list(blocks)
        results: List[ParsedBlock] = []
        try:
            log.info(
                f"SemanticAwareChunker.start blocks={len(_blocks)} target_chars={self.target_chars} sim_threshold={self.similarity_threshold}"
            )
        except Exception:
            pass
        
        # 【优化】检查是否来自 MinerU 预合并（通过 metadata 中的 parser_engine）
        # 如果是预合并块，则采用"块级语义合并"而非"句子级重新切分"
        is_pre_merged = any(b.metadata.get("parser_engine") == "mineru" for b in _blocks)
        
        if is_pre_merged:
            block_level = self._chunk_block_level(_blocks)
            return _merge_short_chunks(block_level)
        
        # 原有逻辑：句子级语义分块（适用于非预合并场景）
        for b in _blocks:
            text = (b.text or "").strip()
            # Chunker 不负责过滤多模态块，只负责切分
            # 多模态块直接保留，由 Indexer 统一过滤
            if not text:
                continue
            sents = self._split_sentences(text)
            if not sents:
                # 当分句失败但文本非空时，回退为单块，确保产出可用 chunk
                try:
                    log.info("SemanticAwareChunker.sents_empty_fallback: using single-block fallback")
                except Exception:
                    pass
                results.append(
                    _produce_chunk(
                        block=b,
                        text=text,
                        index=1,
                        total=1,
                        start=0,
                        end=len(text),
                    )
                )
                continue
            embs = self._embed(sents)
            buf: List[str] = []
            buf_vecs: List[List[float]] = []
            last_vec: List[float] | None = None
            chunk_payloads: List[Tuple[str, Dict[str, Any]]] = []

            def _flush_buffer():
                if not buf:
                    return
                override_md: Dict[str, Any] = {}
                if buf_vecs:
                    try:
                        dim = len(buf_vecs[0])
                        acc = [0.0] * dim
                        for vv in buf_vecs:
                            if len(vv) == dim:
                                for j in range(dim):
                                    acc[j] += float(vv[j])
                        override_md["pre_embedding"] = [x / max(len(buf_vecs), 1) for x in acc]
                    except Exception:
                        pass
                chunk_payloads.append(("\n".join(buf), override_md))

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
                # 计算当前缓冲区长度
                buf_len = sum(len(x) for x in buf)
                next_len = buf_len + 1 + len(cur)
                
                # 三级切分策略（SOTA 实践）
                # 1. 硬上限：超过 max_chunk_chars 必须切分
                force_split = next_len >= self.max_chunk_chars
                # 2. 软目标：达到 target_chars 且相似度低于阈值时切分
                soft_split = (next_len >= self.target_chars) and (sim < self.similarity_threshold)
                # 3. 最小保护：未达到 min_chunk_chars 不切分（除非硬上限）
                can_split = (buf_len >= self.min_chunk_chars) or force_split
                
                should_split = can_split and (force_split or soft_split)
                
                if should_split:
                    _flush_buffer()
                    buf = [cur]
                    buf_vecs = [cur_vec] if isinstance(cur_vec, list) and cur_vec else []
                else:
                    buf.append(cur)
                    if isinstance(cur_vec, list) and cur_vec:
                        buf_vecs.append(cur_vec)
                last_vec = cur_vec
            if buf:
                _flush_buffer()

            total = len(chunk_payloads)
            for idx, (chunk_text, override_md) in enumerate(chunk_payloads, start=1):
                results.append(
                    _produce_chunk(
                        block=b,
                        text=chunk_text,
                        index=idx,
                        total=total,
                        start=0,
                        end=len(chunk_text),
                        override_metadata=override_md,
                    )
                )
        results = _merge_short_chunks(results)

        try:
            log.info(
                f"SemanticAwareChunker.finish blocks={len(_blocks)} chunks={len(results)}"
            )
        except Exception:
            pass
        return results
