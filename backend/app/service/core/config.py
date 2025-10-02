from pydantic_settings import BaseSettings
from typing import Literal, Optional
from pydantic import model_validator
from urllib.parse import quote_plus

class Settings(BaseSettings):
    """
    应用配置模型。
    使用 Pydantic 的 BaseSettings，可以自动从环境变量或 .env 文件中读取配置。
    这是整个应用的唯一配置来源。
    """
    # --- 核心基础设施配置 ---
    DATABASE_URL: str
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    JWT_SECRET_KEY: str

    # --- Elasticsearch 配置 ---
    # 从 .env 读取 ES_HOST (e.g., http://es01:9200)
    ES_HOST: str
    # 从 .env 读取 ELASTIC_PASSWORD
    ELASTIC_PASSWORD: Optional[str] = None
    # 这个字段将由下面的 validator 动态生成，供应用内部使用
    ES_URL: str = ""
    ES_DEFAULT_INDEX: str = "scholarmind_default"

    @model_validator(mode='after')
    def build_es_url(self) -> 'Settings':
        """
        在 Pydantic 完成 .env 文件加载后，这个函数会自动运行。
        它会检查是否存在 ELASTIC_PASSWORD，如果存在，就用它来构建
        一个包含 'elastic' 用户名和密码的完整 ES_URL。
        """
        if self.ELASTIC_PASSWORD:
            # 假设默认用户名为 'elastic'
            user_encoded = quote_plus("elastic")
            password_encoded = quote_plus(self.ELASTIC_PASSWORD)
            # 拆分协议和主机部分，以安全地插入认证信息
            if "://" in self.ES_HOST:
                protocol, host = self.ES_HOST.split("://")
                self.ES_URL = f"{protocol}://{user_encoded}:{password_encoded}@{host}"
            else:
                 # 如果没有协议头，则默认使用 http
                self.ES_URL = f"http://{user_encoded}:{password_encoded}@{self.ES_HOST}"
        else:
            self.ES_URL = self.ES_HOST
        return self

    # --- 云端服务API密钥配置 ---
    DASHSCOPE_API_KEY: Optional[str] = None
    DASHSCOPE_BASE_URL: Optional[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = "https://api.openai.com/v1"

    # --- 云端服务模型名称配置 ---
    DASHSCOPE_MODEL_NAME: str = "qwen-plus"
    OPENAI_MODEL_NAME: str = "gpt-4o"

    # --- 组件选择配置 ---
    SM_EMBEDDER_TYPE: Literal['local', 'dashscope'] = "local"
    SM_RERANKER_TYPE: Literal['local', 'dashscope'] = "local"
    SM_LLM_TYPE: Literal['local', 'dashscope', 'openai'] = "local"

    # --- RAG 流程超参数 ---
    SM_RAG_TOPK: int = 5
    SM_RETRIEVE_PAGE_SIZE: int = 5

    # --- 本地模型路径配置 ---
    LOCAL_EMBEDDER_PATH: str = "/models/bge-large-zh-v1.5"
    LOCAL_RERANKER_PATH: str = "/models/bge-reranker-large"
    LOCAL_LLM_PATH: str = "/models/Qwen1.5-14B-Chat"

    # --- 本地模型性能配置 ---
    SM_LOCAL_EMBEDDER_DEVICE: str = "cpu"
    SM_LOCAL_EMBEDDER_BATCH_SIZE: int = 32
    SM_LOCAL_RERANKER_DEVICE: str = "cpu"

    # --- 系统配置 ---
    LOG_LEVEL: str = "INFO"

    class Config:
        # 指定 .env 文件的编码
        env_file_encoding = 'utf-8'
        # pydantic-settings 会自动在项目根目录或当前工作目录寻找 .env 文件
        env_file = '.env'

# 创建一个全局可用的配置实例
settings = Settings()
