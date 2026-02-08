"""
RL 训练相关 API 路由
"""
from fastapi import APIRouter, HTTPException, Depends, Header, Query
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

from dependencies import get_db_session
from models.training import TrainingEpisode, TrainingMetrics
from sqlalchemy.orm import Session
from sqlalchemy import desc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/training", tags=["RL Training"])


# 请求/响应模型
class TrainingEpisodeResponse(BaseModel):
    """训练回合响应"""
    episode_id: str
    user_id: int
    workspace_id: Optional[str]
    user_intent: str
    task_completed: bool
    total_reward: float
    total_iterations: int
    user_feedback: Optional[float]
    expert_rating: Optional[float]
    created_at: str


class TrainingMetricsResponse(BaseModel):
    """训练指标响应"""
    training_run_id: str
    model_version: str
    episode_count: int
    average_reward: float
    task_completion_rate: float
    average_iterations: float
    training_loss: Optional[float]
    validation_loss: Optional[float]
    created_at: str


class UserFeedbackRequest(BaseModel):
    """用户反馈请求"""
    feedback: float  # 0-10
    episode_id: str


class ExpertRatingRequest(BaseModel):
    """专家评分请求"""
    rating: float  # 0-10
    episode_id: str


# 依赖：获取用户 ID
async def get_user_id(x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    """从请求头获取用户 ID"""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")
    try:
        return int(x_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-User-Id format")


@router.get("/episodes", response_model=List[TrainingEpisodeResponse])
async def list_episodes(
    user_id: int = Depends(get_user_id),
    workspace_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db_session)
):
    """
    获取训练回合列表
    
    支持按用户、工作区筛选
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    
    query = db.query(TrainingEpisode).filter(TrainingEpisode.user_id == user_id)
    
    if workspace_id:
        query = query.filter(TrainingEpisode.workspace_id == workspace_id)
    
    episodes = query.order_by(desc(TrainingEpisode.created_at)).offset(offset).limit(limit).all()
    
    return [TrainingEpisodeResponse(**ep.to_dict()) for ep in episodes]


@router.get("/episodes/{episode_id}", response_model=Dict[str, Any])
async def get_episode(
    episode_id: str,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db_session)
):
    """
    获取单个训练回合的详细信息
    
    包括完整的执行历史、奖励序列等
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    
    episode = db.query(TrainingEpisode).filter(
        TrainingEpisode.episode_id == episode_id,
        TrainingEpisode.user_id == user_id
    ).first()
    
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    
    return episode.to_dict()


@router.post("/episodes/{episode_id}/feedback")
async def submit_feedback(
    episode_id: str,
    payload: UserFeedbackRequest,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db_session)
):
    """
    提交用户反馈
    
    用户对 Agent 执行结果的评分（0-10）
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    
    episode = db.query(TrainingEpisode).filter(
        TrainingEpisode.episode_id == episode_id,
        TrainingEpisode.user_id == user_id
    ).first()
    
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    
    if not 0 <= payload.feedback <= 10:
        raise HTTPException(status_code=400, detail="Feedback must be between 0 and 10")
    
    episode.user_feedback = payload.feedback
    db.commit()
    
    logger.info(f"User feedback submitted: episode={episode_id}, feedback={payload.feedback}")
    
    return {"success": True, "episode_id": episode_id, "feedback": payload.feedback}


@router.post("/episodes/{episode_id}/expert-rating")
async def submit_expert_rating(
    episode_id: str,
    payload: ExpertRatingRequest,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db_session)
):
    """
    提交专家评分
    
    专家对 Agent 执行结果的评分（0-10）
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    
    episode = db.query(TrainingEpisode).filter(
        TrainingEpisode.episode_id == episode_id
    ).first()
    
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    
    if not 0 <= payload.rating <= 10:
        raise HTTPException(status_code=400, detail="Rating must be between 0 and 10")
    
    episode.expert_rating = payload.rating
    db.commit()
    
    logger.info(f"Expert rating submitted: episode={episode_id}, rating={payload.rating}")
    
    return {"success": True, "episode_id": episode_id, "rating": payload.rating}


@router.get("/metrics", response_model=List[TrainingMetricsResponse])
async def list_metrics(
    model_version: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db_session)
):
    """
    获取训练指标列表
    
    支持按模型版本筛选
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    
    query = db.query(TrainingMetrics)
    
    if model_version:
        query = query.filter(TrainingMetrics.model_version == model_version)
    
    metrics = query.order_by(desc(TrainingMetrics.created_at)).offset(offset).limit(limit).all()
    
    return [TrainingMetricsResponse(**m.to_dict()) for m in metrics]


@router.post("/metrics")
async def create_metrics(
    payload: Dict[str, Any],
    db: Session = Depends(get_db_session)
):
    """
    创建训练指标记录
    
    用于记录一次训练运行的指标
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    
    metrics = TrainingMetrics(
        model_version=payload.get("model_version", "unknown"),
        episode_count=payload.get("episode_count", 0),
        training_episodes=payload.get("training_episodes"),
        average_reward=payload.get("average_reward", 0.0),
        task_completion_rate=payload.get("task_completion_rate", 0.0),
        average_iterations=payload.get("average_iterations", 0.0),
        training_loss=payload.get("training_loss"),
        validation_loss=payload.get("validation_loss"),
        additional_metrics=payload.get("additional_metrics")
    )
    
    db.add(metrics)
    db.commit()
    
    logger.info(f"Training metrics created: run_id={metrics.training_run_id}, model={metrics.model_version}")
    
    return {"success": True, "training_run_id": metrics.training_run_id}

