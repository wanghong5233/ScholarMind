"""
LaTeX 编辑 Agent 微服务
提供独立的 Agent 服务，支持 LaTeX 文档编辑、引用管理等功能
"""
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import logging
import uuid

from utils.trace import set_trace_id, get_trace_id, clear_trace_id


class HealthFilter(logging.Filter):
    """过滤掉 /health 健康检查的访问日志，但保留其他请求。"""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        msg = str(record.getMessage())
        return "/health" not in msg


class TraceIdFilter(logging.Filter):
    """为日志注入 trace_id 字段"""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        record.trace_id = get_trace_id() or "-"
        return True


# 配置业务日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [trace_id=%(trace_id)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("latex_agent_service")
logging.getLogger().addFilter(TraceIdFilter())

# 单独降低 uvicorn.access 对 /health 的噪音
uvicorn_access = logging.getLogger("uvicorn.access")
uvicorn_access.setLevel(logging.INFO)
uvicorn_access.addFilter(HealthFilter())

app = FastAPI(
    title="LaTeX Agent Service",
    version="1.0.0",
    description="LaTeX 编辑 Agent 微服务，提供智能引用管理、文档编辑等功能"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Trace ID 中间件
@app.middleware("http")
async def attach_trace_id(request, call_next):
    incoming_trace_id = request.headers.get("X-Trace-Id")
    trace_id = incoming_trace_id or str(uuid.uuid4())
    set_trace_id(trace_id)
    try:
        response = await call_next(request)
    finally:
        clear_trace_id()
    response.headers["X-Trace-Id"] = trace_id
    return response


# 依赖：获取用户 ID（从 header 或 token）
async def get_user_id(x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    """
    从请求头获取用户 ID
    主应用负责认证，Agent Service 信任主应用传递的用户信息
    """
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")
    try:
        return int(x_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-User-Id format")


# 健康检查端点（禁用日志）
@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "service": "latex-agent",
        "version": "1.0.0"
    }


# 导入路由
from router import agent_rt
from router import training_rt

app.include_router(agent_rt.router, prefix="/api", tags=["Agent"])
app.include_router(agent_rt.general_router, prefix="/api", tags=["Agent"])
app.include_router(training_rt.router, prefix="/api", tags=["RL Training"])


if __name__ == "__main__":
    import uvicorn
    from config import settings
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)

