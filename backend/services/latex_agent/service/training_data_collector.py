"""
训练数据收集服务
用于收集和存储 Agent 执行数据，用于 RL 训练
"""
from typing import Dict, Any, Optional, List
from dataclasses import asdict
import logging
import uuid
from datetime import datetime

from service.agent_service import AgentState, AgentStep
from service.reward_calculator import RewardCalculator
from models.training import TrainingEpisode, TrainingMetrics

logger = logging.getLogger(__name__)


class TrainingDataCollector:
    """
    训练数据收集器
    
    负责收集 Agent 执行数据，计算奖励，并保存到数据库
    """
    
    def __init__(
        self, 
        reward_calculator: Optional[RewardCalculator] = None, 
        db_session=None,
        db_session_factory=None
    ):
        """
        初始化数据收集器
        
        Args:
            reward_calculator: 奖励计算器实例
            db_session: 数据库会话（已废弃，使用 db_session_factory）
            db_session_factory: 数据库会话工厂函数（可选，用于创建会话）
        """
        self.reward_calculator = reward_calculator or RewardCalculator()
        self.db_session = db_session  # 向后兼容
        self.db_session_factory = db_session_factory
        self._current_episode: Optional[Dict[str, Any]] = None
    
    def start_episode(
        self,
        user_intent: str,
        initial_state: AgentState,
        user_id: int,
        workspace_id: Optional[str] = None
    ) -> str:
        """
        开始一个新的训练回合
        
        Args:
            user_intent: 用户意图
            initial_state: 初始 Agent 状态
            user_id: 用户 ID
            workspace_id: 工作区 ID（可选）
            
        Returns:
            episode_id: 回合 ID
        """
        episode_id = str(uuid.uuid4())
        
        self._current_episode = {
            "episode_id": episode_id,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "user_intent": user_intent,
            "initial_state": self._state_to_dict(initial_state),
            "actions": [],
            "rewards": [],
            "final_state": None,
            "task_completed": False,
            "user_feedback": None,
            "expert_rating": None,
            "total_reward": 0.0,
            "total_iterations": 0,
            "created_at": datetime.utcnow()
        }
        
        logger.info(f"Started training episode: {episode_id}, user={user_id}, intent={user_intent[:50]}...")
        return episode_id
    
    def record_action(
        self,
        step: AgentStep,
        state_before: AgentState,
        state_after: AgentState,
        user_intent: str,
        task_completed: bool = False
    ):
        """
        记录一个执行步骤和奖励
        
        Args:
            step: Agent 执行步骤
            state_before: 执行前的状态
            state_after: 执行后的状态
            user_intent: 用户意图
            task_completed: 任务是否完成
        """
        if not self._current_episode:
            logger.warning("No active episode, cannot record action")
            return
        
        # 计算奖励
        reward = self.reward_calculator.calculate_reward(
            step=asdict(step),
            state_before=self._state_to_dict(state_before),
            state_after=self._state_to_dict(state_after),
            user_intent=user_intent,
            task_completed=task_completed
        )
        
        # 记录步骤和奖励
        self._current_episode["actions"].append(asdict(step))
        self._current_episode["rewards"].append(reward)
        self._current_episode["total_reward"] += reward
        self._current_episode["total_iterations"] += 1
        
        logger.debug(f"Recorded action: {step.type.value}, reward={reward:.2f}")
    
    def finish_episode(
        self,
        final_state: AgentState,
        task_completed: bool,
        user_feedback: Optional[float] = None,
        expert_rating: Optional[float] = None
    ) -> Optional[str]:
        """
        完成一个训练回合并保存
        
        Args:
            final_state: 最终状态
            task_completed: 任务是否完成
            user_feedback: 用户反馈（0-10）
            expert_rating: 专家评分（0-10）
            
        Returns:
            episode_id: 如果保存成功返回 episode_id，否则返回 None
        """
        if not self._current_episode:
            logger.warning("No active episode to finish")
            return None
        
        # 更新最终状态
        self._current_episode["final_state"] = self._state_to_dict(final_state)
        self._current_episode["task_completed"] = task_completed
        self._current_episode["user_feedback"] = user_feedback
        self._current_episode["expert_rating"] = expert_rating
        
        episode_id = self._current_episode["episode_id"]
        
        # 保存到数据库
        db_session = None
        try:
            # 优先使用会话工厂（推荐方式）
            if self.db_session_factory:
                db_session = self.db_session_factory()
            elif self.db_session:
                db_session = self.db_session
            
            if db_session:
                episode = TrainingEpisode(**self._current_episode)
                db_session.add(episode)
                db_session.commit()
                logger.info(f"Saved training episode: {episode_id}, total_reward={self._current_episode['total_reward']:.2f}")
            else:
                logger.debug(f"Episode finished but not saved (no db_session or db_session_factory): {episode_id}")
        except Exception as e:
            logger.error(f"Failed to save training episode: {e}", exc_info=True)
            if db_session:
                db_session.rollback()
            return None
        finally:
            # 如果使用会话工厂创建的会话，需要关闭
            if db_session and self.db_session_factory and db_session != self.db_session:
                db_session.close()
        
        # 清空当前回合
        self._current_episode = None
        
        return episode_id
    
    def _state_to_dict(self, state: AgentState) -> Dict[str, Any]:
        """将 AgentState 转换为字典"""
        return {
            "workspace_id": state.workspace_id,
            "user_id": state.user_id,
            "current_document": state.current_document,
            "citation_mappings": state.citation_mappings,
            "workspace_files": state.workspace_files,
            "workspace_config": state.workspace_config,
            "execution_history_length": len(state.execution_history)
        }
    
    def get_current_episode(self) -> Optional[Dict[str, Any]]:
        """获取当前回合数据（用于调试）"""
        return self._current_episode.copy() if self._current_episode else None

