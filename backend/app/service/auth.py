from utils.database import get_db, SessionLocal
from models.user import User
from utils.password import verify_password
from sqlalchemy.exc import SQLAlchemyError
from exceptions.auth import AuthError
from fastapi_jwt import JwtAccessBearerCookie
import secrets
from datetime import timedelta
import os
import logging
from fastapi import Depends

# JWT配置
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'default_secret_key') + 'happy'

# 从请求头或cookie中读取访问令牌（优先从请求头读取）
access_security = JwtAccessBearerCookie(
    secret_key=JWT_SECRET_KEY,
    auto_error=True,
    access_expires_delta=timedelta(days=2)  # 访问令牌有效期为2天
)

def create_token(user_id: int, user_name: str, salting: str = ""):
    # 生成token的主体部分，包含用户名和随机盐值
    subject = {
        "user_id": user_id,
        "user_name": user_name,
        "salting": secrets.token_hex(16)
    }
    
    # 创建新的访问令牌
    access_token = access_security.create_access_token(subject=subject)
    
    return access_token


def authenticate(username: str, password: str) -> str:
    """
    认证用户
    
    Args:
        username (str): 用户名
        password (str): 明文密码
    
    Returns:
        str: 认证成功返回token，失败返回None
    
    Raises:
        AuthError: 认证失败时抛出
    """
    db = next(get_db())
    try:
        # 查询用户
        user = db.query(User).filter(User.username == username).first()
        
        if not user:
            raise AuthError("认证失败")
        
        # 验证密码
        if not verify_password(password, user.password_hash):
            raise AuthError("认证失败")
        
        # 如果需要生成token，可以在这里实现
        # return create_token(user.id)
        return create_token(user.id, user.username)
    
    except SQLAlchemyError as e:
        raise AuthError("认证失败") from e
    finally:
        db.close()

def register_user(username: str, password: str):
    """
    注册新用户
    
    Args:
        username (str): 用户名
        password (str): 明文密码
    
    Raises:
        AuthError: 如果用户名已存在或注册失败
    """
    from utils.password import hash_password
    
    # 配置日志
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    logger.info(f"开始注册用户: {username}")
    db = next(get_db())
    try:
        # 检查用户名是否已存在
        logger.info("检查用户名是否已存在...")
        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            logger.warning(f"用户名 {username} 已存在")
            raise AuthError("用户名已存在")
        
        # 对密码进行哈希处理
        logger.info("开始密码哈希处理...")
        password_hash = hash_password(password)
        logger.info("密码哈希处理完成")
        
        # 创建新用户
        logger.info("创建新用户记录...")
        new_user = User(username=username, password_hash=password_hash)
        db.add(new_user)
        
        # 提交事务
        logger.info("提交数据库事务...")
        db.commit()
        logger.info(f"用户 {username} 注册成功")
        
    except SQLAlchemyError as e:
        logger.error(f"数据库操作失败: {str(e)}")
        db.rollback()
        raise AuthError(f"注册失败: {str(e)}")
    except Exception as e:
        logger.error(f"注册过程中发生未知错误: {str(e)}")
        db.rollback()
        raise AuthError(f"注册失败: {str(e)}")
    finally:
        db.close()
        logger.info("数据库连接已关闭")

def get_current_user(subject: dict = Depends(access_security)):
    """
    FastAPI 依赖项，用于获取当前认证的用户。
    它会验证JWT，并从数据库中检索用户信息。
    """
    db = next(get_db())
    try:
        user_id = subject.get("user_id")
        if user_id is None:
            raise AuthError("无效的Token")
        
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise AuthError("用户不存在")
        
        return user
    except Exception as e:
        # 重新抛出 AuthError 以便全局异常处理器可以捕获
        raise AuthError(str(e))
    finally:
        db.close()