from typing import List
from sentence_transformers.cross_encoder import CrossEncoder
from schemas.rag import Chunk
from service.core.abstractions.reranker import BaseReranker
from service.core.config import settings
from utils.get_logger import log

class LocalBgeReranker(BaseReranker):
    """
    使用部署在本地的 BGE Reranker 模型进行重排序的实现类。
    """
    def __init__(self):
        try:
            # 从配置中加载本地 reranker 模型和设备
            self.model = CrossEncoder(
                settings.LOCAL_RERANKER_PATH,
                device=settings.SM_LOCAL_RERANKER_DEVICE,
            )
            log.info(f"LocalBgeReranker initialized with model from: {settings.LOCAL_RERANKER_PATH} on device: {settings.SM_LOCAL_RERANKER_DEVICE}")
        except Exception as e:
            log.error(f"Failed to load local BGE reranker model: {e}", exc_info=True)
            raise

    async def rerank(self, query: str, chunks: List[Chunk]) -> List[Chunk]:
        """
        根据查询对检索到的文本块列表进行重排序。
        """
        if not chunks:
            return []

        # 创建 cross-encoder 需要的句子对：[(query, chunk_content), ...]
        sentence_pairs = [(query, chunk.content) for chunk in chunks]
        log.info(f"Reranking {len(chunks)} chunks with local BGE reranker.")

        # 使用模型的 predict 方法计算每个句子对的相关性得分
        scores = self.model.predict(sentence_pairs)

        # 将分数和原始文本块打包，然后按分数降序排序
        scored_chunks = list(zip(scores, chunks))
        scored_chunks.sort(key=lambda x: x[0], reverse=True)

        # 提取排序后的文本块
        reranked_chunks = [chunk for score, chunk in scored_chunks]

        return reranked_chunks
