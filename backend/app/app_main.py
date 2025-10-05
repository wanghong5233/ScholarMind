from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from router import chat_rt
from router import user_rt
from router import history_rt
from router import knowledgebase_rt
# from router import document_upload_rt
import os
import time
import uuid
from utils.get_logger import log, request_id_var
from exceptions.base import APIException

# 从环境变量获取 root_path
root_path = os.getenv("ROOT_PATH", "")

app = FastAPI(root_path=root_path)

# 定义请求处理中间件
@app.middleware("http")
async def dispatch(request: Request, call_next):
    # 为每个请求生成唯一的 request_id
    request_id = str(uuid.uuid4())
    
    # 将 request_id 设置到 context var 中
    request_id_var.set(request_id)
    
    # 记录请求开始的日志
    log.info(f"Request started: {request.method} {request.url.path}")
    
    start_time = time.time()
    
    try:
        response = await call_next(request)
        # 在响应头中添加 request_id，方便前端调试
        response.headers["X-Request-ID"] = request_id
    except Exception as e:
        # Pass exception object directly to loguru to handle safely
        log.error("Request failed with an unhandled exception:", exception=e)
        raise e
    finally:
        process_time = (time.time() - start_time) * 1000
        log.info(f"Request finished in {process_time:.2f}ms. Status code: {response.status_code if 'response' in locals() else 'N/A'}")

    return response

# 注册自定义API异常处理器
@app.exception_handler(APIException)
async def api_exception_handler(request: Request, exc: APIException):
    log.error(
        f"API Exception caught: {exc.message}",
        exc_info=True,
        extra={"error_code": exc.code, "status_code": exc.status_code}
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
        headers=exc.headers,
    )

# 注册全局未捕获异常处理器
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # 使用安全的方式记录异常，避免字符串格式化引发 KeyError
    log.error("Unhandled exception caught:", exception=exc)
    return JSONResponse(
        status_code=500,
        content={
            "code": 50000,
            "message": "Internal Server Error",
            "data": None
        }
    )


# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源，生产环境中应该设置具体的源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)

# 包含各个模块的路由，并为它们设置统一的前缀和标签
# 这有助于API文档的组织和URL的结构化
app.include_router(chat_rt.router, prefix="/api/chat", tags=["Chat"])
app.include_router(user_rt.router, prefix="/api/users", tags=["Users"])
app.include_router(history_rt.router, prefix="/api/history", tags=["History"])
# app.include_router(document_upload_rt.router, prefix="/api/document-upload", tags=["Document Upload"])
app.include_router(knowledgebase_rt.router, prefix="/api/knowledgebases", tags=["Knowledge Bases"])

if __name__=='__main__':
    import uvicorn
    # 在本地开发时，为了让日志配置生效，需要在这里进行配置
    # 在生产环境（如使用 Gunicorn + Uvicorn worker），通常在启动命令中配置日志
    from utils.get_logger import configure_logger
    configure_logger()
    uvicorn.run(app, host="0.0.0.0", port=8000)
    