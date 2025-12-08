# LaTeX Agent Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖和完整的 LaTeX 编译环境
# 包含学术论文所需的所有常用宏包（IEEEtran、algorithm、subfig 等）
RUN apt-get update && apt-get install -y \
    git \
    wget \
    curl \
    texlive-base \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-bibtex-extra \
    texlive-publishers \
    texlive-science \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    && rm -rf /var/lib/apt/lists/*

# 配置 pip 国内镜像源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.extra-index-url "https://mirrors.cernet.edu.cn/pypi/web/simple"

# 升级 pip
RUN pip install --upgrade pip setuptools wheel

# 复制依赖文件
COPY requirements.txt /app/requirements.txt

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制服务代码
COPY . /app/

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PORT=8003
ENV RAG_SERVICE_URL=http://scholarmind_api:8000

# 暴露端口
EXPOSE 8003

# 启动服务（使用默认 INFO 日志级别，方便调试）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]


# 复制依赖文件

COPY requirements.txt /app/requirements.txt



# 安装 Python 依赖

RUN pip install --no-cache-dir -r requirements.txt



# 复制服务代码

COPY . /app/



# 设置环境变量

ENV PYTHONUNBUFFERED=1

ENV PYTHONPATH=/app
ENV PORT=8003

ENV RAG_SERVICE_URL=http://scholarmind_api:8000



# 暴露端口

EXPOSE 8003



# 启动服务（使用默认 INFO 日志级别，方便调试）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]




