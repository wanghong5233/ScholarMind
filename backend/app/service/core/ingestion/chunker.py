from __future__ import annotations

from typing import Iterable, List
from core.config import settings
from service.core.ingestion.interfaces import ParsedBlock, Chunker
from utils.get_logger import log


def _merge_short_chunks(chunks: List[ParsedBlock]) -> List[ParsedBlock]:
    """Merge or drop overly short chunks to避免检索阶段产生无意义片段 (如单词级)。"""
    min_chars = max(int(getattr(settings, "SM_CHUNK_MIN_FILTER_CHARS", 20) or 0), 0)
    if min_chars <= 0 or not chunks:
        return [c for c in chunks if (c.text or "").strip()]

    merged: List[ParsedBlock] = []
    pending: List[str] = []

    for chunk in chunks:
        text = (chunk.text or "").strip()
        if not text:
            continue

        if len(text) < min_chars:
            pending.append(text)
            # 优先合入上一块，避免丢失信息
            if merged:
                last = merged.pop()
                combined = (last.text or "").rstrip()
                if combined:
                    combined += "\n"
                combined += "\n".join(pending)
                merged.append(ParsedBlock(text=combined, metadata=last.metadata))
                pending.clear()
            continue

        if pending:
            text = "\n".join(pending + [text])
            pending.clear()

        merged.append(ParsedBlock(text=text, metadata=chunk.metadata))

    if pending:
        if merged:
            last = merged.pop()
            combined = (last.text or "").rstrip()
            if combined:
                combined += "\n"
            combined += "\n".join(pending)
            merged.append(ParsedBlock(text=combined, metadata=last.metadata))
        else:
            merged.append(ParsedBlock(text="\n".join(pending), metadata={}))

    return merged


class RecursiveCharacterChunker(Chunker):
    """递归字符分块器（兜底方案）
    
    学术 RAG 最佳实践：
    - target_chars: 800 (约 512 tokens，适合学术论文的段落长度)
    - overlap: 100 (约 12.5%，保证上下文连续性)
    """
    def __init__(self, target_chars: int = 800, overlap: int = 100) -> None:
        self.target_chars = target_chars
        self.overlap = overlap

    def chunk(self, *, blocks: Iterable[ParsedBlock]) -> List[ParsedBlock]:
        # 为了稳定统计与日志，这里转换为列表（上游 parse 已返回 List，额外开销可接受）
        block_list: List[ParsedBlock] = list(blocks)
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
        for b in block_list:
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
        results = _merge_short_chunks(results)

        try:
            log.info(
                f"RecursiveCharacterChunker output_chunks={len(results)} input_blocks={len(block_list)} target_chars={self.target_chars}"
            )
        except Exception:
            pass
        return results



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

        pieces: List[ParsedBlock] = []
        start = 0
        length = len(text)
        while start < length:
            end = min(length, start + target)
            chunk_txt = text[start:end]
            pieces.append(ParsedBlock(text=chunk_txt, metadata=dict(block.metadata)))
            if end >= length:
                break
            start = end - overlap
            if start < 0:
                start = 0

        return pieces

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
        """块级语义合并：保留预合并块的完整性，只在块边界做跨块合并。
        
        适用场景：MinerU 等已做结构化预合并的解析器输出。
        策略：
        1. 保留每个预合并块的完整性（不重新切分）
        2. 计算每个块的整体 embedding
        3. 基于块间语义相似度 + 长度阈值，决定是否跨块合并
        4. 优先保留结构化信息（如表格/公式不参与合并）
        """
        try:
            log.info(f"SemanticAwareChunker.block_level_mode: preserving pre-merged structure")
        except Exception:
            pass
        
        # 过滤空块
        valid_blocks = [b for b in blocks if (b.text or "").strip()]
        if not valid_blocks:
            return []
        
        # 计算每个块的 embedding（整体，不切分句子）
        try:
            from service.core.ingestion.embedder import SimpleAPIEmbedder
            emb = SimpleAPIEmbedder()
            recs = emb.embed(chunks=valid_blocks)
            block_vecs = [r.get("vector") or [] for r in recs]
        except Exception:
            # 如果 embedding 失败，直接返回原块（不合并）
            log.warning("SemanticAwareChunker.block_level: embedding failed, returning original blocks")
            return valid_blocks
        
        # 块级合并
        results: List[ParsedBlock] = []
        current_block: ParsedBlock | None = None
        current_vec: List[float] | None = None
        
        for i, block in enumerate(valid_blocks):
            vec = block_vecs[i] if i < len(block_vecs) else []
            
            # 跳过多模态元素（表格/公式/图表），不参与合并
            element_type = block.metadata.get("element_type", "paragraph")
            if element_type in ("table_json", "equation_latex", "figure_summary"):
                if current_block:
                    results.append(current_block)
                    current_block = None
                    current_vec = None
                results.append(block)
                continue
            
            # 第一个块
            if current_block is None:
                current_block = block
                current_vec = vec
                continue
            
            # 计算块间相似度
            sim = self._cos(current_vec or [], vec)
            current_len = len(current_block.text or "")
            next_len = current_len + 1 + len(block.text or "")
            
            # 合并条件（参数化，可通过配置开关调优）：
            # 1. 语义相似度 ≥ settings.SM_SEMANTIC_SIMILARITY_THRESHOLD（默认 0.60）
            # 2. 合并后长度 ≤ settings.SM_BLOCK_LEVEL_MAX_CHARS（默认 10000）
            # 3. 长度优先：current_len < settings.SM_BLOCK_LEVEL_LEN_MERGE_BELOW（默认 5000）
            # 4. 跨页合并：由 settings.SM_BLOCK_LEVEL_ALLOW_CROSS_PAGE 控制（默认不允许）
            from core.config import settings
            semantic_threshold = getattr(settings, "SM_SEMANTIC_SIMILARITY_THRESHOLD", 0.60)
            max_chars = getattr(settings, "SM_BLOCK_LEVEL_MAX_CHARS", 10000)
            len_merge_below = getattr(settings, "SM_BLOCK_LEVEL_LEN_MERGE_BELOW", 5000)
            allow_cross_page = getattr(settings, "SM_BLOCK_LEVEL_ALLOW_CROSS_PAGE", False)

            semantic_merge = sim >= semantic_threshold
            length_merge = current_len < len_merge_below
            size_ok = next_len <= max_chars
            same_page = (current_block.metadata.get("page") == block.metadata.get("page"))
            page_ok = allow_cross_page or same_page
            
            should_merge = size_ok and page_ok and (semantic_merge or length_merge)
            
            if should_merge:
                # 合并块
                current_block = ParsedBlock(
                    text=current_block.text + "\n" + block.text,
                    metadata=current_block.metadata.copy()
                )
                # 更新 embedding（简单平均）
                if current_vec and vec and len(current_vec) == len(vec):
                    current_vec = [(a + b) / 2 for a, b in zip(current_vec, vec)]
            else:
                # 输出当前块，开始新块
                results.append(current_block)
                current_block = block
                current_vec = vec
        
        # 输出最后一个块
        if current_block:
            results.append(current_block)

        # 确保块级结果不会超过 max_chunk_chars；过长则回退到句级/滑窗切分
        final_results: List[ParsedBlock] = []
        for block in results:
            text = (block.text or "").strip()
            if not text:
                continue
            if len(text) <= self.max_chunk_chars:
                final_results.append(block)
                continue

            # 回退策略：按句子切分，保持 target/max 约束；若分句失败则使用滑窗
            sents = self._split_sentences(text)
            if not sents:
                final_results.extend(self._split_block_sliding(block))
                continue

            buf: List[str] = []
            for sent in sents:
                tentative = ("\n".join(buf) + ("\n" if buf else "") + sent) if buf else sent
                if len(tentative) > self.max_chunk_chars and buf:
                    final_results.append(ParsedBlock(text="\n".join(buf), metadata=dict(block.metadata)))
                    buf = [sent]
                    continue
                buf.append(sent)
            if buf:
                final_results.append(ParsedBlock(text="\n".join(buf), metadata=dict(block.metadata)))

        try:
            log.info(f"SemanticAwareChunker.block_level: input={len(valid_blocks)} output={len(final_results)} merged={len(valid_blocks)-len(final_results)}")

            # 调试：打印前2个块级合并后的 chunk
            for i, block in enumerate(final_results[:2]):
                log.info(f"[DEBUG_BLOCK_LEVEL_CHUNK_{i+1}] element_type={block.metadata.get('element_type')} "
                         f"page={block.metadata.get('page')} len={len(block.text)} chars={len(block.text)} "
                         f"text_preview={block.text[:100]}...")
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
            # 块级语义合并：保留预合并块的完整性，只在块边界做跨块合并
            block_level = self._chunk_block_level(_blocks)
            return _merge_short_chunks(block_level)
        
        # 原有逻辑：句子级语义分块（适用于非预合并场景）
        for b in _blocks:
            text = (b.text or "").strip()
            if not text:
                continue
            sents = self._split_sentences(text)
            if not sents:
                # 当分句失败但文本非空时，回退为单块，确保产出可用 chunk
                try:
                    log.info("SemanticAwareChunker.sents_empty_fallback: using single-block fallback")
                except Exception:
                    pass
                results.append(ParsedBlock(text=text, metadata=dict(b.metadata)))
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
        results = _merge_short_chunks(results)

        try:
            log.info(
                f"SemanticAwareChunker.finish blocks={len(_blocks)} chunks={len(results)}"
            )
        except Exception:
            pass
        return results
