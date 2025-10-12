FROM python:3.11.7-slim

# 方案1：使用更稳定的镜像源（清华大学镜像源）
RUN echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list

# 或者使用官方源作为备用（如果镜像源有问题，请取消注释下面几行）
# RUN echo "deb http://deb.debian.org/debian bookworm main" > /etc/apt/sources.list && \
#     echo "deb http://security.debian.org/debian-security bookworm-security main" >> /etc/apt/sources.list && \
#     echo "deb http://deb.debian.org/debian bookworm-updates main" >> /etc/apt/sources.list

# 安装必要的构建工具和依赖，增加错误处理
RUN apt-get update --fix-missing || apt-get update && \
    apt-get install -y --no-install-recommends --fix-missing \
    build-essential \
    python3-dev \
    make \
    curl \
    libgl1 \
    libgl1-mesa-glx \
    libglu1-mesa \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 更新 pip 并配置镜像源
RUN pip config set global.extra-index-url "https://mirrors.cernet.edu.cn/pypi/web/simple"
RUN pip install --upgrade pip setuptools wheel

# 设置工作目录
WORKDIR /app

# 拷贝依赖文件并安装
# 注意：因为构建上下文改为了 backend/ 目录，所以源路径需要加上 app/ 前缀
COPY app/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝代码
# 将 app/ 目录下的所有内容拷贝到容器的 /app 目录
COPY app/ /app

# 预置 NLTK 数据，确保容器重建后无需再次下载
ENV NLTK_DATA=/usr/local/nltk_data
RUN python - <<'PY'
import nltk
for p in ['punkt','punkt_tab','wordnet','omw-1.4']:
    try:
        nltk.download(p, download_dir='/usr/local/nltk_data')
    except Exception as e:
        print('nltk download failed:', p, e)
PY

# 修改文件权限
RUN chmod -R 755 /app
RUN chmod +x /app/start.sh

# 设置启动脚本为入口点
ENTRYPOINT ["/app/start.sh"]
