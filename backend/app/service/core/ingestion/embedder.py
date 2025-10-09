from __future__ import annotations

from typing import Dict, Iterable, List
from service.core.ingestion.interfaces import ParsedBlock, Embedder
from service.core.rag.nlp.model import generate_embedding
import logging


class DummyEmbedder(Embedder):
    """
    占位嵌入器：返回零向量，便于先打通接口。
    后续替换为本地/云端真实嵌入器。
    """

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim

    def embed(self, *, chunks: Iterable[ParsedBlock]) -> List[Dict[str, object]]:
        vec = [0.0] * self.dim
        records: List[Dict[str, object]] = []
        for c in chunks:
            records.append({
                "text": c.text,
                "vector": vec,
                "metadata": c.metadata,
            })
        return records


class SimpleAPIEmbedder(Embedder):
    """
    简易真实嵌入器：调用现有 generate_embedding(texts) 接口批量生成向量。
    作为第一版可用实现，后续可替换为本地/云端更高性能的实现。
    """

    def __init__(self, batch_size: int = 10) -> None:
        self.batch_size = batch_size

    def embed(self, *, chunks: Iterable[ParsedBlock]) -> List[Dict[str, object]]:
        logger = logging.getLogger("ingestion.embedder")
        chunk_list = list(chunks)
        texts = [c.text or "" for c in chunk_list]
        if not texts:
            logger.info("SimpleAPIEmbedder: no texts to embed (0 chunks)")
            return []
        try:
            embeddings = generate_embedding(texts) or []
        except Exception as e:
            logger.error(f"SimpleAPIEmbedder: generate_embedding failed: {e}")
            embeddings = []
        logger.info(f"SimpleAPIEmbedder: chunks={len(chunk_list)} embeddings_len={len(embeddings)}")
        # 兜底：若返回不足，填充空向量
        dim = len(embeddings[0]) if embeddings and embeddings[0] is not None else 1024
        records: List[Dict[str, object]] = []
        for idx, c in enumerate(chunk_list):
            emb = embeddings[idx] if idx < len(embeddings) else None
            vec = emb if emb is not None else ([0.0] * dim)
            records.append({
                "text": c.text,
                "vector": vec,
                "metadata": c.metadata,
            })
        logger.info(f"SimpleAPIEmbedder: records_built={len(records)} dim={dim}")
        return records


