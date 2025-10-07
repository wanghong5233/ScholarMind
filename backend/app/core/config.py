from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from typing import Literal, Optional
from pydantic import model_validator
from urllib.parse import quote_plus


class Settings(BaseSettings):
    """
    应用配置类，使用 Pydantic-settings 自动从环境变量加载配置。
    单一配置入口，避免多处加载 .env 造成的时序冲突。
    """
    # Semantic Scholar
    semantic_scholar_api_key: str | None = Field(None, env="SEMANTIC_SCHOLAR_API_KEY")

    # Database
    DATABASE_URL: str | None = None

    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # Auth / Root path
    JWT_SECRET_KEY: Optional[str] = None
    ROOT_PATH: str = ""

    # Elasticsearch
    ES_HOST: str = "http://localhost:9200"
    ELASTIC_PASSWORD: Optional[str] = None
    ES_URL: str = ""
    # 兼容旧代码（等全仓清理后可移除）
    ELASTICSEARCH_URL: Optional[str] = None
    ES_DEFAULT_INDEX: str = "scholarmind_default"

    @model_validator(mode="after")
    def build_es_url(self) -> "Settings":
        if self.ELASTIC_PASSWORD:
            user_encoded = quote_plus("elastic")
            password_encoded = quote_plus(self.ELASTIC_PASSWORD)
            if "://" in self.ES_HOST:
                protocol, host = self.ES_HOST.split("://", 1)
                self.ES_URL = f"{protocol}://{user_encoded}:{password_encoded}@{host}"
            else:
                self.ES_URL = f"http://{user_encoded}:{password_encoded}@{self.ES_HOST}"
        else:
            self.ES_URL = self.ES_HOST
        # 同步兼容字段
        self.ELASTICSEARCH_URL = self.ES_URL
        return self

    # DashScope / OpenAI 兼容
    DASHSCOPE_API_KEY: Optional[str] = None
    DASHSCOPE_BASE_URL: Optional[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = "https://api.openai.com/v1"

    # 模型名称
    DASHSCOPE_MODEL_NAME: str = "qwen-plus"
    OPENAI_MODEL_NAME: str = "gpt-4o"

    # 组件选择
    SM_EMBEDDER_TYPE: Literal["local", "dashscope"] = "local"
    SM_RERANKER_TYPE: Literal["local", "dashscope"] = "local"
    SM_LLM_TYPE: Literal["local", "dashscope", "openai"] = "local"

    # RAG 超参数
    SM_RAG_TOPK: int = 5
    SM_RETRIEVE_PAGE_SIZE: int = 5

    # 本地模型路径与设备
    LOCAL_EMBEDDER_PATH: str = "/models/bge-large-zh-v1.5"
    LOCAL_RERANKER_PATH: str = "/models/bge-reranker-large"
    LOCAL_LLM_PATH: str = "/models/Qwen1.5-14B-Chat"
    SM_LOCAL_EMBEDDER_DEVICE: str = "cpu"
    SM_LOCAL_EMBEDDER_BATCH_SIZE: int = 32
    SM_LOCAL_RERANKER_DEVICE: str = "cpu"

    # 其他
    RAGFLOW_BASE_URL: Optional[str] = None
    RAG_PROJECT_BASE: Optional[str] = None
    RAG_DEPLOY_BASE: Optional[str] = None
    LOG_LEVEL: str = "INFO"

    # Upload limits
    MAX_UPLOAD_SIZE_MB: int = 200

    class Config:
        env_file_encoding = "utf-8"
        env_file = ".env"


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()
