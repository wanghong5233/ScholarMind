"""
RL 训练数据模型
"""
from sqlalchemy import Column, String, Integer, Float, Boolean, Text, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import uuid

Base = declarative_base()


class TrainingEpisode(Base):
    """
    训练回合数据模型
    
    存储 Agent 执行的一个完整回合（episode）的数据，用于 RL 训练
    """
    __tablename__ = "training_episodes"
    
    episode_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, nullable=False, index=True)
    workspace_id = Column(String(255), nullable=True, index=True)
    
    # 用户意图和初始状态
    user_intent = Column(Text, nullable=False)
    initial_state = Column(JSON, nullable=False)  # AgentState 的 JSON 表示
    
    # 执行历史
    actions = Column(JSON, nullable=False)  # AgentStep 列表的 JSON 表示
    rewards = Column(JSON, nullable=False)  # 每个步骤的奖励列表
    
    # 最终状态和结果
    final_state = Column(JSON, nullable=False)  # 最终 AgentState 的 JSON 表示
    task_completed = Column(Boolean, nullable=False, default=False)
    
    # 反馈和评分
    user_feedback = Column(Float, nullable=True)  # 用户评分 0-10
    expert_rating = Column(Float, nullable=True)  # 专家评分 0-10
    
    # 统计信息
    total_reward = Column(Float, nullable=False, default=0.0)
    total_iterations = Column(Integer, nullable=False, default=0)
    
    # 时间戳
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "episode_id": self.episode_id,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "user_intent": self.user_intent,
            "initial_state": self.initial_state,
            "actions": self.actions,
            "rewards": self.rewards,
            "final_state": self.final_state,
            "task_completed": self.task_completed,
            "user_feedback": self.user_feedback,
            "expert_rating": self.expert_rating,
            "total_reward": self.total_reward,
            "total_iterations": self.total_iterations,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class TrainingMetrics(Base):
    """
    训练指标模型
    
    存储每次训练运行的指标数据
    """
    __tablename__ = "training_metrics"
    
    training_run_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    model_version = Column(String(255), nullable=False, index=True)
    
    # 训练数据统计
    episode_count = Column(Integer, nullable=False, default=0)
    training_episodes = Column(JSON, nullable=True)  # 使用的 episode_id 列表
    
    # 性能指标
    average_reward = Column(Float, nullable=False, default=0.0)
    task_completion_rate = Column(Float, nullable=False, default=0.0)
    average_iterations = Column(Float, nullable=False, default=0.0)
    
    # 训练指标
    training_loss = Column(Float, nullable=True)
    validation_loss = Column(Float, nullable=True)
    
    # 其他指标（JSON 格式存储）
    additional_metrics = Column(JSON, nullable=True)
    
    # 时间戳
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "training_run_id": self.training_run_id,
            "model_version": self.model_version,
            "episode_count": self.episode_count,
            "training_episodes": self.training_episodes,
            "average_reward": self.average_reward,
            "task_completion_rate": self.task_completion_rate,
            "average_iterations": self.average_iterations,
            "training_loss": self.training_loss,
            "validation_loss": self.validation_loss,
            "additional_metrics": self.additional_metrics,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

