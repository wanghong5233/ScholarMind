import os
import sys
from contextvars import ContextVar
from loguru import logger

# 创建一个 ContextVar，用于在异步任务中安全地传递 request_id
# 提供了默认值，以防在非请求上下文中使用 logger
request_id_var: ContextVar[str] = ContextVar("request_id", default="<no_request_id>")

def configure_logger():
    """
    配置 loguru 日志记录器。
    - 移除默认的处理器，以完全控制日志格式。
    - 添加一个新的处理器，将日志输出为 JSON 格式。
    - 使用 patch 方法为每条日志记录动态添加 request_id。
    - 从环境变量读取日志级别，默认为 INFO。
    """
    # 移除默认的 loguru 处理器
    logger.remove()

    # 从环境变量获取日志级别，如果没有设置，则默认为 "INFO"
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # 添加一个新的处理器，输出到标准错误流 (stderr)
    logger.add(
        sys.stderr,          # 指定日志输出的目标
        level=log_level,     # 设置日志级别
        format="{message}",  # 使用自定义格式，这里只输出消息本身，因为序列化器会处理结构
        serialize=True,      # 关键配置：将日志记录序列化为 JSON
        backtrace=True,      # 在异常日志中包含完整的堆栈跟踪
        diagnose=True,       # 添加详细的异常诊断信息
    )

    # 使用 patch 为日志记录动态添加额外数据
    def patch_record_with_request_id(record):
        """
        修补日志记录，将 ContextVar 中的 request_id 添加进去。
        """
        record["extra"]["request_id"] = request_id_var.get()

    # 将 patch 函数应用到 logger
    logger.configure(patcher=patch_record_with_request_id)

    return logger

# 在模块加载时执行配置，并导出一个可直接使用的 logger 实例
# 这使得其他模块可以直接 from utils.get_logger import logger 来使用
log = configure_logger()