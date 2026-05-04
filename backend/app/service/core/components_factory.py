from core.config import settings
from service.core.abstractions.embedder import BaseEmbedder
from service.core.abstractions.reranker import BaseReranker
from service.core.abstractions.llm import BaseLLM
from service.core.abstractions.vector_store import BaseVectorStore
from exceptions.base import ModelNotFoundError

# 导入具体的实现类
# ----------------------------------------------------------------------------
# LocalBgeEmbedder（SM_EMBEDDER_TYPE=local）：暂未启用，预留扩展点
#   定位：面向未来的 embedding 独立微服务 / 领域微调方向，是工厂模式刻意
#         保留的实现分支，并非废弃代码。
#   当前为何不启用：local_bge.py 顶层 `from sentence_transformers import
#         SentenceTransformer` 会把 torch + nvidia-* 拉进镜像（约 7-8 GB）。
#         为了把 scholarmind_api 从 ~11GB 压到 ~4GB，演示阶段先不打包这条
#         链路。生产/演示统一走 DashScope（远程 API）。
#   何时启用：要做本地 embedding 微调或独立微服务时，按下方"启用步骤"恢复。
#   启用步骤：
#     1) 取消下方 `from ...local_bge import LocalBgeEmbedder` 注释
#     2) 取消 get_embedder() 内 `elif SM_EMBEDDER_TYPE == "local"` 分支注释
#     3) 把 torch + sentence-transformers 加回 backend/app/requirements.txt
#     4) 重建 scholarmind_api 镜像
# ----------------------------------------------------------------------------
# from service.core.implementations.embedders.local_bge import LocalBgeEmbedder  # 暂未启用，见上方说明
from service.core.implementations.embedders.dashscope import DashScopeEmbedder
from service.core.implementations.rerankers.http_reranker import HttpReranker
from service.core.implementations.rerankers.dashscope import DashScopeReranker
from service.core.implementations.llms.local import LocalLlm
from service.core.implementations.llms.dashscope import DashScopeLlm
from service.core.implementations.llms.openai import OpenAiLlm

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
        if settings.SM_EMBEDDER_TYPE == "dashscope":
            _embedder_instance = DashScopeEmbedder()
        # SM_EMBEDDER_TYPE=local 暂未启用（预留扩展点，详见模块顶部说明）
        # elif settings.SM_EMBEDDER_TYPE == "local":
        #     _embedder_instance = LocalBgeEmbedder()
        else:
            raise ModelNotFoundError(
                model_name=settings.SM_EMBEDDER_TYPE,
                message=(
                    f"Embedder type '{settings.SM_EMBEDDER_TYPE}' is not enabled in this image. "
                    "Currently only 'dashscope' is built in. "
                    "To enable 'local', see the activation steps in components_factory.py header."
                ),
            )
    return _embedder_instance

def get_reranker() -> BaseReranker:
    """
    组件工厂函数：根据配置返回一个 BaseReranker 的单例。
    
    支持两种模式（微服务架构）：
    - "local": 通过 HTTP API 调用本地部署的独立精排服务（默认，解耦部署）
    - "dashscope": 使用阿里云 DashScope API（云端服务）
    
    注意：在微服务架构中，"local" 表示本地独立服务，通过 HTTP 调用，不是耦合部署。
    """
    global _reranker_instance
    if _reranker_instance is None:
        if settings.SM_RERANKER_TYPE == "local":
            _reranker_instance = HttpReranker()  # local = 本地独立服务（HTTP调用）
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
    """
    global _vector_store_instance
    if _vector_store_instance is None:
        vector_store = str(getattr(settings, "SM_VECTOR_STORE", "pgvector") or "pgvector").strip().lower()
        if vector_store == "pgvector":
            from service.core.implementations.vector_stores.pgvector import PgVectorVectorStore

            _vector_store_instance = PgVectorVectorStore()
        elif vector_store == "elasticsearch":
            from service.core.implementations.vector_stores.elasticsearch import ElasticsearchVectorStore

            _vector_store_instance = ElasticsearchVectorStore()
        else:
            raise ModelNotFoundError(model_name=vector_store, message="Unknown vector store type configured.")
    return _vector_store_instance
