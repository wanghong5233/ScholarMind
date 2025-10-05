from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from exceptions.auth import  AuthError
from service.auth import authenticate, register_user, get_current_user
from models.user import User as UserModel
from pydantic import BaseModel
import httpx
import asyncio

router = APIRouter()


# 定义登录请求体的 Pydantic 模型
class LoginRequest(BaseModel):
    username: str
    password: str

# 用户认证接口
@router.post("/login")
async def login(request: LoginRequest):
    """
    用户认证接口，用于登录系统。

    接收用户名和密码，通过认证后返回一个用于后续请求的JWT access token。

    - **请求体**: `LoginRequest` 模型，包含 `username` 和 `password`。
    - **成功响应**: 返回包含 `access_token` 和 `token_type` 的JSON对象。
    - **失败响应**:
        - 401 Unauthorized: 认证失败（用户名或密码错误）。
        - 500 Internal Server Error: 其他服务器内部错误。
    """
    try:
        # 调用 authenticate 函数进行认证
        token = authenticate(request.username, request.password)
        return {"access_token": token, "token_type": "bearer"}
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

# 定义请求体的 Pydantic 模型
class RegisterRequest(BaseModel):
    username: str
    password: str

# 用户注册接口
@router.post("/register")
async def register(request: RegisterRequest):
    """
    新用户注册接口。

    接收用户名和密码，创建新用户。如果用户名已存在，将返回错误。

    - **请求体**: `RegisterRequest` 模型，包含 `username` 和 `password`。
    - **成功响应**: 返回一个表示注册成功的消息。
    - **失败响应**:
        - 400 Bad Request: 注册失败（如用户名已存在）。
        - 500 Internal Server Error: 其他服务器内部错误。
    """
    try:
        # 调用 register_user 函数进行注册
        register_user(request.username, request.password)
        return {"message": "User registered successfully"}
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

# 定义 STS Token 请求体的 Pydantic 模型
class STSTokenRequest(BaseModel):
    appid: str
    accessKey: str

# STS Token 接口
# 这是一个相对独立的功能，用于获取字节跳动语音服务的临时访问凭证。
@router.post("/sts-token")
async def get_sts_token(request: STSTokenRequest):
    """
    获取字节跳动语音服务的临时安全凭证 (STS Token)。

    此接口作为一个代理，将请求转发至字节跳动的STS服务，用于获取
    客户端访问语音服务所需的临时授权。

    - **请求体**: `STSTokenRequest` 模型，包含 `appid` 和 `accessKey`。
    - **成功响应**: 直接返回字节跳动STS API的原始响应。
    - **失败响应**:
        - 408 Request Timeout: 请求STS服务超时。
        - 503 Service Unavailable: 请求STS服务失败。
        - 500 Internal Server Error: 其他服务器内部错误。
    """
    try:
        # 构造请求头
        headers = {
            "Authorization": f"Bearer; {request.accessKey}",
            "Content-Type": "application/json"
        }
        
        # 构造请求体
        body = {
            "appid": request.appid,
            "duration": 300
        }
        
        # 调用 ByteDance STS Token API
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://openspeech.bytedance.com/api/v1/sts/token",
                headers=headers,
                json=body,
                timeout=30.0
            )
            
            # 返回原始响应
            return response.json()
            
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail="Request timeout when calling STS token API"
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Error calling STS token API: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

# 用于测试热更新的接口
@router.get("/test-hot-reload")
async def test_hot_reload():
    """一个简单的测试接口，用于验证Docker卷挂载实现的代码热更新功能。"""
    return {"message": "热更新成功！ 第3版！"}

# Pydantic模型，用于API响应
class User(BaseModel):
    id: int
    username: str

    class Config:
        orm_mode = True

# 获取当前用户的接口
@router.get("/users/me", response_model=User)
async def read_users_me(current_user: UserModel = Depends(get_current_user)):
    """
    获取当前认证用户的个人信息。

    通过依赖注入的 `get_current_user` 函数来验证JWT，并返回
    当前用户的详细信息。

    - **依赖**: `get_current_user`，处理token验证并提供用户信息。
    - **成功响应**: 返回当前用户的 `User` 模型数据。
    - **失败响应**:
        - 401 Unauthorized: 如果token无效或已过期。
    """
    return current_user