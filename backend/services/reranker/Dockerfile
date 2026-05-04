# BGE Reranker 服务 Dockerfile (CPU 版本)
# 注意：当前生产/演示走 DashScope 远程精排，本镜像不参与默认部署，
#       仅在切换到本地精排（SM_RERANKER_TYPE=local + 启动 reranker 服务）时使用。
FROM python:3.11-slim

WORKDIR /app

# APT 源：阿里云 Debian mirror（ECS 内网，与其他服务保持一致）
RUN rm -f /etc/apt/sources.list.d/debian.sources && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    git \
    wget \
    curl \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# pip 镜像源：阿里云内网
RUN pip config set global.index-url "https://mirrors.aliyun.com/pypi/simple/" && \
    pip config set install.trusted-host "mirrors.aliyun.com" && \
    pip install --upgrade pip setuptools wheel

# 安装精排服务依赖（CPU版本）
RUN pip install --no-cache-dir \
    fastapi==0.115.0 \
    uvicorn==0.31.0 \
    sentence-transformers==5.0.0 \
    transformers>=4.40.0 \
    pydantic==2.10.1 \
    numpy \
    torch==2.5.1 \
    huggingface_hub>=0.20.0

# 复制服务代码
COPY reranker_service.py /app/reranker_service.py

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV RERANKER_MODEL_PATH=/models/bge-reranker-large
ENV RERANKER_USE_QUANTIZATION=false

# 暴露端口
EXPOSE 8002

# 启动服务
CMD ["uvicorn", "reranker_service:app", "--host", "0.0.0.0", "--port", "8002"]

