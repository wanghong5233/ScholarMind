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

    # Auth / JWT（用于调用主 RAG 服务需要认证的接口）
    JWT_SECRET_KEY: Optional[str] = None
    JWT_ACCESS_TOKEN_EXPIRE_DAYS: int = 30
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


# 全局配置实例
settings = Settings()

