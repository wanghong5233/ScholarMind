"""
配置管理模块
管理 Agent 服务的配置项
"""
from pydantic_settings import BaseSettings
from typing import Optional, Dict
import os


class Settings(BaseSettings):
    """应用配置"""
    
    # 服务配置
    SERVICE_NAME: str = "latex-agent"
    SERVICE_VERSION: str = "1.0.0"
    PORT: int = 8003
    HOST: str = "0.0.0.0"
    
    # LLM 配置（使用和主API服务相同的环境变量名）
    DASHSCOPE_API_KEY: Optional[str] = None  # 从 .env 加载
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DASHSCOPE_MODEL_NAME: str = "qwen-plus"  # 基础模型（API 调用）
    OPENAI_API_KEY: Optional[str] = None  # 可选：OpenAI API Key
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL_NAME: str = "gpt-4o"

    # LLM 请求超时与健康策略
    LLM_REQUEST_TIMEOUT: int = 60
    LLM_FALLBACK_ENABLED: bool = True
    LLM_FALLBACK_ALLOW_EXPLICIT_PROVIDER: bool = True
    LLM_HEALTH_FAILURE_THRESHOLD: int = 3
    LLM_HEALTH_COOLDOWN_SECONDS: int = 90
    
    # RL 训练模型配置（可选，如果使用 RL 训练的模型）
    RL_MODEL_ENABLED: bool = False  # 是否启用 RL 训练模型
    RL_MODEL_PATH: Optional[str] = None  # RL 训练模型的本地路径
    RL_MODEL_BASE: str = "qwen-7b"  # RL 训练的基础模型（Qwen-7B/LLaMA-2-7B）
    
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 4096

    # LLM 成本统计（默认 0，按需在环境变量配置）
    # LLM_COST_CONFIG 示例：
    # {
    #   "dashscope": {"qwen-plus": {"input": 0.0, "output": 0.0}, "default": {"input": 0.0, "output": 0.0}},
    #   "openai": {"gpt-4o": {"input": 0.0, "output": 0.0}}
    # }
    LLM_COST_CONFIG: Dict[str, Dict[str, Dict[str, float]]] = {}
    LLM_COST_PER_1K_INPUT_TOKENS: float = 0.0
    LLM_COST_PER_1K_OUTPUT_TOKENS: float = 0.0
    
    # RAG 服务配置
    RAG_SERVICE_URL: str = "http://scholarmind_api:8000"
    
    # Agent 配置
    AGENT_MAX_ITERATIONS: int = 10
    AGENT_TIMEOUT: int = 300  # 秒
    AGENT_WORKSPACE_CACHE_TTL: int = 60  # 秒
    AGENT_WORKSPACE_CACHE_SIZE: int = 16  # 缓存条目数
    AGENT_HISTORY_MAX_ENTRIES: int = 500  # 历史记录最大条数（0 表示不限制）
    AGENT_HISTORY_MAX_BYTES: int = 0  # 历史记录最大磁盘占用（0 表示不限制）
    AGENT_WORKSPACE_LOCK_TTL: int = 600  # 工作区锁最大持续时间（秒）

    # Web Search 配置
    ENABLE_WEB_SEARCH: bool = False
    WEB_SEARCH_PROVIDER: str = "tavily"
    WEB_SEARCH_API_KEY: Optional[str] = None
    WEB_SEARCH_BASE_URL: str = "https://api.tavily.com/search"
    WEB_SEARCH_MAX_RESULTS: int = 5
    WEB_SEARCH_TIMEOUT: int = 20
    
    # 工作区配置
    WORKSPACES_ROOT: str = "/app/workspaces"
    
    # 数据库配置（用于 RL 训练数据存储）
    DATABASE_URL: Optional[str] = None
    
    # RL 训练数据收集配置
    RL_TRAINING_ENABLED: bool = False  # 是否启用 RL 训练数据收集
    RL_COLLECT_ALL_EPISODES: bool = False  # 是否收集所有回合（False 时只收集明确标记的）
    
    # 日志配置
    LOG_LEVEL: str = "INFO"
    
    # ==================== LLM Prompt 日志配置 ====================
    # 用于调试 Agent 决策流程，查看发送给 LLM 的完整上下文
    LOG_FULL_PROMPT: bool = True  # 是否在日志中输出完整 Prompt/响应
    
    # 工具参数详情日志配置（影响日志大小）
    # - True: 输出每个工具的完整 JSON Schema（参数类型、描述、required 等）
    #   优点：可以看到 LLM 接收到的完整工具定义
    #   缺点：日志量大（13个工具 × ~20行 = ~260行），且这些 Schema 是固定的，调试价值低
    # - False: 只输出工具名称和简短描述
    #   优点：日志简洁（13个工具 × 1行 = 13行），减少 60-70% 的日志量
    #   缺点：看不到参数定义细节（但可以在代码中查看）
    # 
    # 🎯 推荐设置：
    # - 开发/调试工具问题时：True
    # - 正常使用/生产环境：False（默认）
    LOG_PROMPT_INCLUDE_TOOL_PARAMS: bool = False  # 是否在 Prompt 日志中包含工具参数详情

    # Auth / JWT（用于调用主 RAG 服务需要认证的接口）
    JWT_SECRET_KEY: Optional[str] = None
    JWT_ACCESS_TOKEN_EXPIRE_DAYS: int = 30
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


# 全局配置实例
settings = Settings()


def refresh_settings() -> Settings:
    """Reload settings from environment into the existing instance."""

    updated = Settings()
    for name in settings.model_fields:
        setattr(settings, name, getattr(updated, name))
    return settings

