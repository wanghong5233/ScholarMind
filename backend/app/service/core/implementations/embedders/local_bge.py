# ============================================================================
# 状态：暂未启用（预留扩展点，面向未来）
#
# 定位：面向未来的 embedding 独立微服务 / 领域微调方向。属于工厂模式刻意
#       保留的实现分支，不是废弃代码。
#
# 当前为何不打包：
#   - 本文件顶层 `from sentence_transformers import SentenceTransformer` 会
#     把 torch + nvidia-* 拉进镜像（约 7-8 GB）。
#   - 演示阶段为了把 scholarmind_api 从 ~11GB 压到 ~4GB，
#     requirements.txt 暂不安装 sentence-transformers / torch。
#   - components_factory.get_embedder() 中对本类的 import 与分支已注释。
#   - 因此本文件不会在运行时被 import；若误改配置 SM_EMBEDDER_TYPE=local，
#     工厂会抛出明确错误，提示按"启用步骤"恢复。
#
# 启用步骤（要做本地 embedding 微调 / 独立微服务时）：
#   1) 把 sentence-transformers / torch 加回 backend/app/requirements.txt
#   2) 取消 components_factory.py 中对 LocalBgeEmbedder 的 import 和分支注释
#   3) 重建 scholarmind_api 镜像
# ============================================================================

from typing import List
from sentence_transformers import SentenceTransformer  # noqa: F401  (inactive path; see file header)
from schemas.rag import Document, Chunk
from service.core.abstractions.embedder import BaseEmbedder
from core.config import settings
from utils.get_logger import log

class LocalBgeEmbedder(BaseEmbedder):
    """
    使用部署在本地的 BGE 模型来生成嵌入向量的实现类。
    依赖 `sentence-transformers`。

    状态：当前镜像未启用此实现（详见文件头说明）。这是面向未来的预留扩展点，
         在重新启用 sentence-transformers / torch 后即可恢复使用。
    """
    def __init__(self):
        try:
            # 从配置中获取模型路径和设备，并加载模型
            # "trust_remote_code=True" 是加载某些社区模型（如BGE）所必需的
            self.model = SentenceTransformer(
                settings.LOCAL_EMBEDDER_PATH,
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
