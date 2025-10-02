from service.core.config import settings
from service.core.abstractions.embedder import BaseEmbedder
from service.core.abstractions.reranker import BaseReranker
from service.core.abstractions.llm import BaseLLM
from service.core.abstractions.vector_store import BaseVectorStore
from exceptions.base import ModelNotFoundError

# 导入具体的实现类
from service.core.implementations.embedders.local_bge import LocalBgeEmbedder
from service.core.implementations.embedders.dashscope import DashScopeEmbedder
from service.core.implementations.rerankers.local_bge import LocalBgeReranker
from service.core.implementations.rerankers.dashscope import DashScopeReranker
from service.core.implementations.llms.local import LocalLlm
from service.core.implementations.llms.dashscope import DashScopeLlm
from service.core.implementations.llms.openai import OpenAiLlm
from service.core.implementations.vector_stores.elasticsearch import ElasticsearchVectorStore

# 这是一个简单的“注册表”模式，用于缓存已创建的组件实例（单例）
_embedder_instance = None
_reranker_instance = None
_llm_instance = None
_vector_store_instance = None

def get_embedder() -> BaseEmbedder:
    """
    组件工厂函数：根据配置返回一个 BaseEmbedder 的单例。
    """
    global _embedder_instance
    if _embedder_instance is None:
        if settings.SM_EMBEDDER_TYPE == "local":
            _embedder_instance = LocalBgeEmbedder()
        elif settings.SM_EMBEDDER_TYPE == "dashscope":
            _embedder_instance = DashScopeEmbedder()
        else:
            raise ModelNotFoundError(model_name=settings.SM_EMBEDDER_TYPE, message="Unknown embedder type configured.")
    return _embedder_instance

def get_reranker() -> BaseReranker:
    """
    组件工厂函数：根据配置返回一个 BaseReranker 的单例。
    """
    global _reranker_instance
    if _reranker_instance is None:
        if settings.SM_RERANKER_TYPE == "local":
            _reranker_instance = LocalBgeReranker()
        elif settings.SM_RERANKER_TYPE == "dashscope":
            _reranker_instance = DashScopeReranker()
        else:
            raise ModelNotFoundError(model_name=settings.SM_RERANKER_TYPE, message="Unknown reranker type configured.")
    return _reranker_instance

def get_llm() -> BaseLLM:
    """
    组件工厂函数：根据配置返回一个 BaseLLM 的单例。
    """
    global _llm_instance
    if _llm_instance is None:
        if settings.SM_LLM_TYPE == "local":
            _llm_instance = LocalLlm()
        elif settings.SM_LLM_TYPE == "dashscope":
            _llm_instance = DashScopeLlm()
        elif settings.SM_LLM_TYPE == "openai":
            _llm_instance = OpenAiLlm()
        else:
            raise ModelNotFoundError(model_name=settings.SM_LLM_TYPE, message="Unknown LLM type configured.")
    return _llm_instance

def get_vector_store() -> BaseVectorStore:
    """
    组件工厂函数：返回一个 BaseVectorStore 的单例。
    （目前我们只计划一种实现，但工厂模式提供了未来的扩展性）
    """
    global _vector_store_instance
    if _vector_store_instance is None:
        # 在这里填入 ElasticsearchVectorStore 或其他实现
        _vector_store_instance = ElasticsearchVectorStore()
    return _vector_store_instance
