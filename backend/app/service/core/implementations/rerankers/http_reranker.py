"""
HTTP Reranker 实现类
通过 HTTP API 调用独立的精排服务
"""
from typing import List
import httpx
from schemas.rag import Chunk
from service.core.abstractions.reranker import BaseReranker
from core.config import settings
from utils.get_logger import log


class HttpReranker(BaseReranker):
    """
    通过 HTTP API 调用本地独立精排服务的实现类（微服务架构）。
    
    在微服务架构中，"local" 模式表示本地部署的独立服务，通过 HTTP 调用。
    
    优势：
    - 服务解耦：精排服务可独立部署和扩展
    - 资源隔离：GPU 资源独立管理
    - 易于维护：精排服务升级不影响主服务
    """
    def __init__(self):
        self.endpoint = getattr(settings, "SM_RERANKER_ENDPOINT", None)
        if not self.endpoint:
            raise ValueError("SM_RERANKER_ENDPOINT is not set. Please configure the reranker service endpoint.")
        
        # 移除末尾的斜杠
        self.endpoint = self.endpoint.rstrip("/")
        self.rerank_url = f"{self.endpoint}/rerank"
        self.health_url = f"{self.endpoint}/health"
        self.request_timeout = float(getattr(settings, "SM_RERANKER_HTTP_TIMEOUT", 60) or 60)
        self.health_timeout = float(getattr(settings, "SM_RERANKER_HEALTH_TIMEOUT", 5) or 5)
        self._service_hint = (
            "请确认 reranker 服务已启动：`docker-compose up -d reranker` "
            f"并可通过 {self.health_url} 访问"
        )
        
        self._perform_health_check()

    def _perform_health_check(self) -> None:
        """启动时执行一次健康检查，便于快速发现配置问题。"""
        try:
            with httpx.Client(timeout=self.health_timeout) as client:
                response = client.get(self.health_url)
                response.raise_for_status()
                health_data = response.json()
                log.info(
                    "HttpReranker initialized. Endpoint=%s, GPU=%s, ModelLoaded=%s",
                    self.endpoint,
                    health_data.get("gpu_available", False),
                    health_data.get("model_loaded", False),
                )
        except httpx.HTTPStatusError as exc:
            log.warning(
                "Reranker service health check returned status %s. %s",
                exc.response.status_code,
                self._service_hint,
            )
        except httpx.RequestError as exc:
            log.warning(
                "Failed to reach reranker service: %s. %s",
                exc,
                self._service_hint,
            )

    async def rerank(self, query: str, chunks: List[Chunk]) -> List[Chunk]:
        """
        通过 HTTP API 对检索到的文本块列表进行重排序。
        """
        if not chunks:
            return []

        # 准备请求数据
        request_data = {
            "query": query,
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "content": chunk.content,
                    "metadata": chunk.metadata or {},
                }
                for chunk in chunks
            ],
            "batch_size": 32,  # 默认批处理大小
        }

        log.info(f"[HTTP_RERANK_REQUEST] query='{query[:60]}...' chunks={len(chunks)} endpoint={self.rerank_url}")

        try:
            async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                response = await client.post(
                    self.rerank_url,
                    json=request_data,
                )
                response.raise_for_status()
                result = response.json()

            reranked_chunks_data = result.get("reranked_chunks", [])
            scores = result.get("scores", [])

            # 构建 chunk_id 到 Chunk 的映射
            chunk_map = {chunk.chunk_id: chunk for chunk in chunks}

            # 按照返回的顺序重建 Chunk 列表，并将分数添加到metadata中
            reranked_chunks = []
            for idx, chunk_data in enumerate(reranked_chunks_data):
                chunk_id = chunk_data.get("chunk_id")
                if chunk_id in chunk_map:
                    chunk = chunk_map[chunk_id]
                    # 将精排分数添加到metadata中，便于后续使用
                    if idx < len(scores):
                        if chunk.metadata is None:
                            chunk.metadata = {}
                        chunk.metadata["rerank_score"] = float(scores[idx])
                    reranked_chunks.append(chunk)
                else:
                    log.warning(f"Chunk {chunk_id} not found in original chunks")

            log.info(
                f"[HTTP_RERANK_COMPLETE] Top score: {scores[0] if scores else 0:.4f}, "
                f"Score range: [{min(scores) if scores else 0:.4f}, {max(scores) if scores else 0:.4f}], "
                f"Reranked chunks: {len(reranked_chunks)}/{len(chunks)}"
            )

            return reranked_chunks

        except httpx.HTTPStatusError as exc:
            log.error(
                "HTTP reranker request failed with status %s: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
        except httpx.RequestError as exc:
            log.error("HTTP reranker request error: %s", exc)
        except Exception as exc:
            log.error("Unexpected error in HTTP reranker: %s", exc, exc_info=True)

        # 降级：返回原始顺序
        log.warning("Falling back to original chunk order due to reranker service failure. %s", self._service_hint)
        return chunks

