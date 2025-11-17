# BGE Reranker 服务 Dockerfile (CPU 版本)
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    git \
    wget \
    curl \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 配置 pip 国内镜像源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.extra-index-url "https://mirrors.cernet.edu.cn/pypi/web/simple"

# 升级 pip
RUN pip install --upgrade pip setuptools wheel

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

