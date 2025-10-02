from typing import List, Tuple
from elasticsearch import AsyncElasticsearch, NotFoundError
from elasticsearch.helpers import async_bulk
from schemas.rag import Chunk
from service.core.abstractions.vector_store import BaseVectorStore
from service.core.config import settings
from utils.get_logger import log
from exceptions.base import VectorStoreError

class ElasticsearchVectorStore(BaseVectorStore):
    """
    使用 Elasticsearch 作为向量存储的实现类。
    """
    def __init__(self):
        try:
            self.client = AsyncElasticsearch(hosts=[settings.ES_URL])
            self.default_index = settings.ES_DEFAULT_INDEX
            log.info(f"ElasticsearchVectorStore initialized, connected to: {settings.ES_URL}")
        except Exception as e:
            log.error(f"Failed to connect to Elasticsearch: {e}", exc_info=True)
            raise

    async def _create_index_if_not_exists(self, index_name: str):
        # 这是一个简化的索引创建逻辑，实际项目中 mapping.json 会更复杂
        if not await self.client.indices.exists(index=index_name):
            log.info(f"Index '{index_name}' not found. Creating it now.")
            try:
                await self.client.indices.create(
                    index=index_name,
                    body={
                        "mappings": {
                            "properties": {
                                "vector": {"type": "dense_vector", "dims": 1024},  # 假设 embedding 维度为1024
                                "document_id": {"type": "keyword"},
                                "content": {"type": "text"}
                            }
                        }
                    }
                )
            except Exception as e:
                log.error(f"Failed to create index '{index_name}': {e}", exc_info=True)
                raise VectorStoreError(operation="create_index")

    async def add_chunks(self, chunks: List[Chunk], index_name: str = None) -> List[str]:
        index = index_name or self.default_index
        await self._create_index_if_not_exists(index)

        actions = [
            {
                "_index": index,
                "_id": chunk.chunk_id,
                "_source": {
                    "vector": chunk.embedding,
                    "document_id": chunk.document_id,
                    "content": chunk.content,
                    "metadata": chunk.metadata
                }
            } for chunk in chunks if chunk.embedding is not None
        ]

        try:
            success, failed = await async_bulk(self.client, actions)
            if failed:
                log.error(f"Elasticsearch bulk insert failed for {len(failed)} documents.")
                raise VectorStoreError(operation="add_chunks", message=f"{len(failed)} chunks failed to be added.")
            log.info(f"Successfully added {success} chunks to index '{index}'.")
            return [chunk.chunk_id for chunk in chunks]
        except Exception as e:
            log.error(f"Error during Elasticsearch bulk operation: {e}", exc_info=True)
            raise VectorStoreError(operation="add_chunks")

    async def search(self, query_embedding: List[float], top_k: int, index_name: str = None) -> List[Tuple[Chunk, float]]:
        index = index_name or self.default_index
        if not await self.client.indices.exists(index=index):
            log.warning(f"Search attempted on non-existent index '{index}'. Returning empty results.")
            return []

        knn_query = {
            "field": "vector",
            "query_vector": query_embedding,
            "k": top_k,
            "num_candidates": top_k * 10
        }

        try:
            response = await self.client.search(
                index=index,
                knn=knn_query,
                size=top_k,
                source=["document_id", "content", "metadata"]
            )

            results = []
            for hit in response["hits"]["hits"]:
                source = hit["_source"]
                chunk = Chunk(
                    chunk_id=hit["_id"],
                    document_id=source.get("document_id"),
                    content=source.get("content"),
                    metadata=source.get("metadata", {})
                )
                results.append((chunk, hit["_score"]))
            return results
        except Exception as e:
            log.error(f"Elasticsearch k-NN search failed: {e}", exc_info=True)
            raise VectorStoreError(operation="search")

    async def delete_by_document_id(self, document_id: str, index_name: str = None) -> None:
        index = index_name or self.default_index
        if not await self.client.indices.exists(index=index):
            return

        query = {"query": {"term": {"document_id": document_id}}}
        try:
            await self.client.delete_by_query(index=index, body=query)
            log.info(f"Successfully deleted chunks for document_id '{document_id}' from index '{index}'.")
        except NotFoundError:
            log.warning(f"No chunks found for document_id '{document_id}' in index '{index}' to delete.")
        except Exception as e:
            log.error(f"Failed to delete chunks by document_id: {e}", exc_info=True)
            raise VectorStoreError(operation="delete_by_document_id")
