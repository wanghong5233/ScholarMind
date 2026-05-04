# MinerU PDF 解析服务 Dockerfile
# 基于 MinerU 官方镜像或从源码构建
# 注意：当前生产/演示走 LlamaParse 远程解析，本镜像不参与默认部署，
#       仅在 SM_PARSER_ORDER 包含 mineru 且需要本地 MinerU 时使用。
FROM python:3.10-slim

WORKDIR /app

# APT 源：阿里云 Debian mirror（ECS 内网，与其他服务保持一致）
# 注意：python:3.10-slim 基于 Debian 11 (bullseye)；改 sources.list 用 bullseye
RUN rm -f /etc/apt/sources.list.d/debian.sources 2>/dev/null || true && \
    echo "deb http://mirrors.aliyun.com/debian/ bullseye main contrib non-free" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ bullseye-updates main contrib non-free" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security bullseye-security main contrib non-free" >> /etc/apt/sources.list

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    git \
    wget \
    curl \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libglib2.0-dev \
    && rm -rf /var/lib/apt/lists/*

# pip 镜像源：阿里云内网
RUN pip config set global.index-url "https://mirrors.aliyun.com/pypi/simple/" && \
    pip config set install.trusted-host "mirrors.aliyun.com" && \
    pip install --upgrade pip

# 安装 MinerU（官方推荐方式）
# 使用 pip 安装 mineru[core]，包含除 vLLM 外的所有核心功能
# 注意：如果有GPU，PyTorch会自动使用；如果没有GPU，会自动降级到CPU
RUN pip install --no-cache-dir "mineru[core]"

# MinerU 模型会在首次运行时自动下载
# 设置环境变量使用 ModelScope 镜像源（国内用户推荐）
ENV MINERU_MODEL_SOURCE=modelscope

# 安装 FastAPI 用于 HTTP 包装
RUN pip install --no-cache-dir fastapi uvicorn python-multipart

# 复制 HTTP 服务代码
COPY mineru_service.py /app/mineru_service.py

# 创建工作目录
RUN mkdir -p /app/temp

# 暴露端口
EXPOSE 8001

# 启动服务
CMD ["uvicorn", "mineru_service:app", "--host", "0.0.0.0", "--port", "8001"]

