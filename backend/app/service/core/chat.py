import json
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from database.db_models import Session as SessionModel, Message as MessageModel
from database.db_session import get_db
from utils import logger
import redis
import os
from sqlalchemy import text
from fastapi import HTTPException


def get_redis_client():
    """获取 Redis 客户端"""
    redis_host = os.getenv('REDIS_HOST', 'redis')
    redis_port = int(os.getenv('REDIS_PORT', 6379))
    redis_db = int(os.getenv('REDIS_DB', 0))
    return redis.Redis(host=redis_host, port=redis_port, db=redis_db, decode_responses=True)

def get_quick_parse_content(session_id: str) -> str:
    """从 Redis 获取快速解析的文档内容"""
    try:
        redis_client = get_redis_client()
        content = redis_client.get(session_id)
        if content:
            logger.info(f"从 Redis 获取到快速解析内容，session_id: {session_id}, 长度: {len(content)}")
            return content
        else:
            logger.info(f"Redis 中未找到快速解析内容，session_id: {session_id}")
            return None
    except Exception as e:
        logger.error(f"从 Redis 获取快速解析内容失败: {str(e)}")
        return None
        
def write_chat_to_db(session_id: str, user_question: str, model_answer: str, retrieval_content: List[Dict], recommended_questions: List[str], think: str):
    """
    将完整的对话记录写入数据库。
    """
    db = next(get_db())
    try:
        documents_json = json.dumps(retrieval_content, ensure_ascii=False)
        
        new_message = MessageModel(
            session_id=session_id,
            user_question=user_question,
            model_answer=model_answer,
            documents=documents_json,
            recommended_questions=recommended_questions,
            think=think
        )
        db.add(new_message)
        db.commit()
        logger.info(f"Chat history for session '{session_id}' successfully written to the database.")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to write chat history to database for session '{session_id}': {e}", exc_info=True)
    finally:
        db.close()

def update_session_name(session_id: str, question: str, user_id: str):
    """
    如果会话还没有标题，使用当前问题的摘要作为标题。
    """
    db: Session = next(get_db())
    try:
        session = db.query(SessionModel).filter(SessionModel.session_id == session_id, SessionModel.user_id == user_id).first()
        if session and not session.session_name:
            # 简化处理：直接截取问题作为标题
            summary = question[:50] + "..." if len(question) > 50 else question
            session.session_name = summary
            db.commit()
            logger.info(f"Session '{session_id}' name updated to '{summary}'")
    except Exception as e:
        logger.error(f"Error updating session name for session '{session_id}': {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()

