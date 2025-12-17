"""
配置管理模块
管理 Agent 服务的配置项
"""
from pydantic_settings import BaseSettings
from typing import Optional
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
    
    # RL 训练模型配置（可选，如果使用 RL 训练的模型）
    RL_MODEL_ENABLED: bool = False  # 是否启用 RL 训练模型
    RL_MODEL_PATH: Optional[str] = None  # RL 训练模型的本地路径
    RL_MODEL_BASE: str = "qwen-7b"  # RL 训练的基础模型（Qwen-7B/LLaMA-2-7B）
    
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 4096
    
    # RAG 服务配置
    RAG_SERVICE_URL: str = "http://scholarmind_api:8000"
    
    # Agent 配置
    AGENT_MAX_ITERATIONS: int = 10
    AGENT_TIMEOUT: int = 300  # 秒
    AGENT_WORKSPACE_CACHE_TTL: int = 60  # 秒
    AGENT_WORKSPACE_CACHE_SIZE: int = 16  # 缓存条目数
    
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

