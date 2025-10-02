from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from router import chat_rt
from router import user_rt
from router import history_rt
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
        log.error(f"Request failed with exception: {e}", exc_info=True)
        # 重新抛出异常，以便 FastAPI 的异常处理机制能捕获它
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
    # 对于所有未预料到的错误，返回一个通用的500错误
    # 使用 exc_info=True 来记录完整的堆栈跟踪
    log.error(f"Unhandled exception caught: {exc}", exc_info=True)
    
    generic_error = APIException() # 使用默认的 500 错误
    
    return JSONResponse(
        status_code=generic_error.status_code,
        content=generic_error.to_dict(),
    )


# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源，生产环境中应该设置具体的源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)

app.include_router(chat_rt.router)
app.include_router(user_rt.router)
app.include_router(history_rt.router)

if __name__=='__main__':
    import uvicorn
    # 在本地开发时，为了让日志配置生效，需要在这里进行配置
    # 在生产环境（如使用 Gunicorn + Uvicorn worker），通常在启动命令中配置日志
    from utils.get_logger import configure_logger
    configure_logger()
    uvicorn.run(app, host="0.0.0.0", port=8000)
    