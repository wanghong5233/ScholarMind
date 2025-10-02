from typing import List
from llama_index.postprocessor.dashscope_rerank import DashScopeRerank
from llama_index.core.schema import Node, NodeWithScore
from schemas.rag import Chunk
from service.core.abstractions.reranker import BaseReranker
from service.core.config import settings
from utils.get_logger import log

class DashScopeReranker(BaseReranker):
    """
    使用阿里云通义千问 (DashScope) Rerank API 进行重排序的实现类。
    """
    def __init__(self):
        if not settings.DASHSCOPE_API_KEY:
            raise ValueError("DASHSCOPE_API_KEY is not set in the environment.")
        
        # DashScopeRerank 库在初始化时并不需要 top_n，top_n 在调用时指定
        self.reranker = DashScopeRerank(api_key=settings.DASHSCOPE_API_KEY)
        log.info("DashScopeReranker initialized.")

    async def rerank(self, query: str, chunks: List[Chunk]) -> List[Chunk]:
        if not chunks:
            return []
        
        log.info(f"Reranking {len(chunks)} chunks with DashScope Rerank API.")

        # 将我们的 Chunk 对象转换为 DashScopeRerank 库所需的 NodeWithScore 对象
        nodes_to_rerank = [
            NodeWithScore(node=Node(text=chunk.content, extra_info={"original_chunk": chunk}))
            for chunk in chunks
        ]
        
        # 调用 reranker 的 postprocess_nodes 方法
        # top_n 设置为 len(chunks) 以确保对所有传入的块进行打分和排序
        reranked_nodes = self.reranker.postprocess_nodes(
            nodes_to_rerank, 
            query_str=query,
            top_n=len(chunks)
        )

        # 从返回的 Node 对象中提取出我们原始的 Chunk 对象
        reranked_chunks = [node.node.extra_info["original_chunk"] for node in reranked_nodes]

        return reranked_chunks
