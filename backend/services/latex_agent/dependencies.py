"""
依赖注入模块
管理 Agent 服务的依赖项（LLM Client、Tool Registry、Agent 实例等）
"""
from fastapi import Depends
from typing import Any, Dict, Optional
import logging

from service.llm_client import LLMClient
from service.tool_registry import create_tool_registry
from service.agent_service import LaTeXEditAgent
from service.tools.base_tool import ToolRegistry  # 直接从 base_tool 导入，避免循环
from service.reward_calculator import RewardCalculator
from service.training_data_collector import TrainingDataCollector
from config import settings

logger = logging.getLogger(__name__)

# 全局单例实例（在实际应用中可以使用更优雅的依赖注入方式）
_llm_client: Optional[LLMClient] = None
_tool_registry: Optional[ToolRegistry] = None
_reward_calculator: Optional[RewardCalculator] = None
_agent: Optional[LaTeXEditAgent] = None


def get_llm_client() -> LLMClient:
    """
    获取 LLM 客户端实例（单例模式）
    
    Returns:
        LLMClient 实例
    """
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
        logger.info("Initialized LLM client")
    return _llm_client


def refresh_llm_client() -> Dict[str, Any]:
    """Refresh LLM client configuration in-place."""

    llm_client = get_llm_client()
    if hasattr(llm_client, "refresh_config"):
        return llm_client.refresh_config()
    return {"status": "skipped"}


def get_tool_registry() -> ToolRegistry:
    """
    获取工具注册表实例（单例模式）
    
    Returns:
        ToolRegistry 实例
    """
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = create_tool_registry()
        logger.info("Initialized tool registry")
    return _tool_registry


def get_reward_calculator() -> RewardCalculator:
    """
    获取奖励计算器实例（单例模式）
    
    Returns:
        RewardCalculator 实例
    """
    global _reward_calculator
    if _reward_calculator is None:
        _reward_calculator = RewardCalculator()
        logger.info("Initialized reward calculator")
    return _reward_calculator


# 全局数据库引擎和会话工厂（单例模式）
_db_engine = None
_db_sessionmaker = None


def get_db_engine():
    """
    获取数据库引擎（单例模式，使用连接池）
    
    Returns:
        数据库引擎实例
    """
    global _db_engine
    if _db_engine is None and settings.DATABASE_URL:
        try:
            from sqlalchemy import create_engine
            _db_engine = create_engine(
                settings.DATABASE_URL,
                pool_pre_ping=True,  # 连接前检查连接是否有效
                pool_size=5,  # 连接池大小
                max_overflow=10,  # 最大溢出连接数
                echo=False  # 是否打印 SQL
            )
            logger.info("Initialized database engine with connection pool")
        except Exception as e:
            logger.error(f"Failed to create database engine: {e}")
    return _db_engine


def get_db_sessionmaker():
    """
    获取数据库会话工厂（单例模式）
    
    Returns:
        会话工厂函数
    """
    global _db_sessionmaker
    if _db_sessionmaker is None:
        engine = get_db_engine()
        if engine:
            from sqlalchemy.orm import sessionmaker
            _db_sessionmaker = sessionmaker(bind=engine)
            logger.info("Initialized database sessionmaker")
    return _db_sessionmaker


def get_agent(
    llm_client: LLMClient = Depends(get_llm_client),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    reward_calculator: RewardCalculator = Depends(get_reward_calculator)
) -> LaTeXEditAgent:
    """
    获取 Agent 实例（依赖注入）
    
    Args:
        llm_client: LLM 客户端（通过依赖注入获取）
        tool_registry: 工具注册表（通过依赖注入获取）
        reward_calculator: 奖励计算器（通过依赖注入获取）
        
    Returns:
        LaTeXEditAgent 实例
    """
    global _agent
    if _agent is None:
        # 创建训练数据收集器（如果配置了数据库）
        training_collector = None
        if settings.DATABASE_URL:
            try:
                # 使用会话工厂，而不是直接创建会话
                session_factory = get_db_sessionmaker()
                if session_factory:
                    training_collector = TrainingDataCollector(
                        reward_calculator=reward_calculator,
                        db_session_factory=session_factory
                    )
                    logger.info("Initialized training data collector with database")
            except Exception as e:
                logger.warning(f"Failed to initialize training data collector: {e}")
        
        _agent = LaTeXEditAgent(llm_client, tool_registry, training_collector)
        logger.info("Initialized LaTeX Edit Agent")
    return _agent


def get_db_session():
    """
    获取数据库会话（用于训练数据存储）
    
    使用连接池管理数据库连接，会话在请求级别创建和销毁
    """
    if not settings.DATABASE_URL:
        logger.debug("DATABASE_URL not configured, training data collection disabled")
        yield None
        return
    
    session_factory = get_db_sessionmaker()
    if not session_factory:
        logger.warning("Database sessionmaker not available")
        yield None
        return
    
    db = session_factory()
    try:
        yield db
    except Exception as e:
        logger.error(f"Database session error: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()

