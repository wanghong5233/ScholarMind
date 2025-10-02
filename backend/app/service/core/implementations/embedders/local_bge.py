from typing import List
from sentence_transformers import SentenceTransformer
from schemas.rag import Document, Chunk
from service.core.abstractions.embedder import BaseEmbedder
from service.core.config import settings
from utils.get_logger import log

class LocalBgeEmbedder(BaseEmbedder):
    """
    使用部署在本地的 BGE 模型来生成嵌入向量的实现类。
    它依赖于 `sentence-transformers` 库。
    """
    def __init__(self):
        try:
            # 从配置中获取模型路径和设备，并加载模型
            # "trust_remote_code=True" 是加载某些社区模型（如BGE）所必需的
            self.model = SentenceTransformer(
                settings.LOCAL_EMEMBEDDER_PATH, 
                trust_remote_code=True,
                device=settings.SM_LOCAL_EMBEDDER_DEVICE
            )
            self.batch_size = settings.SM_LOCAL_EMBEDDER_BATCH_SIZE
            log.info(f"LocalBgeEmbedder initialized with model from: {settings.LOCAL_EMBEDDER_PATH} on device: {settings.SM_LOCAL_EMBEDDER_DEVICE}")
        except Exception as e:
            log.error(f"Failed to load local BGE model: {e}", exc_info=True)
            # 如果模型加载失败，这是一个严重错误，应抛出异常使应用启动失败
            raise

    async def embed_documents(self, documents: List[Document]) -> List[Chunk]:
        """
        目前这个方法是一个占位符，因为文档分块的逻辑更适合放在一个专门的
        Chunker 服务中。在这里，我们假设文档已经被分块。
        在未来的重构中，我们会引入一个 Chunker 组件。
        """
        # TODO: Implement chunking logic here or in a dedicated Chunker service.
        log.warning("embed_documents is not fully implemented in LocalBgeEmbedder. It returns an empty list.")
        return []

    async def embed_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        """
        对一批文本块进行嵌入。
        """
        contents = [chunk.content for chunk in chunks]
        log.info(f"Embedding {len(contents)} chunks using local BGE model with batch size {self.batch_size}.")
        
        # 使用 sentence-transformers 的 encode 方法进行批量编码
        embeddings = self.model.encode(
            contents, 
            normalize_embeddings=True,
            batch_size=self.batch_size
        )
        
        # 将生成的向量更新回每个 Chunk 对象
        for chunk, embedding in zip(chunks, embeddings):
            chunk.embedding = embedding.tolist()
            
        return chunks

    async def embed_query(self, query: str) -> List[float]:
        """
        对单个查询进行嵌入。
        """
        log.info(f"Embedding query using local BGE model: '{query[:50]}...'")
        embedding = self.model.encode(query, normalize_embeddings=True)
        return embedding.tolist()
