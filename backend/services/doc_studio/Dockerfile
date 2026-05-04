# Doc Studio Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN sed -i \
    -e 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
    -e 's|http://deb.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g' \
    /etc/apt/sources.list.d/debian.sources

# 安装系统依赖和完整的 LaTeX 编译环境
# 包含学术论文所需的所有常用宏包（IEEEtran、algorithm、subfig 等）
# 包含中文支持：xelatex、lualatex、ctex、xeCJK 等
RUN apt-get update && apt-get install -y \
    git \
    wget \
    curl \
    # TeX Live 核心
    texlive-base \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-latex-extra \
    # 参考文献支持
    texlive-bibtex-extra \
    biber \
    # 出版商模板（IEEEtran, ACM, Springer 等）
    texlive-publishers \
    # 科学/数学包（algorithm, amsmath 等）
    texlive-science \
    # 字体支持（包含 Times、TeX Gyre 等完整字体）
    texlive-fonts-recommended \
    texlive-fonts-extra \
    fonts-texgyre \
    # 添加更多字体支持
    texlive-font-utils \
    lmodern \
    # 多语言支持
    texlive-xetex \
    texlive-luatex \
    texlive-lang-chinese \
    texlive-lang-english \
    # 图片处理工具
    ghostscript \
    imagemagick \
    && rm -rf /var/lib/apt/lists/*

# 配置 pip 镜像源 —— 阿里云 PyPI（ECS 内网访问，免公网带宽）
# 与 backend/app/Dockerfile 保持一致；apt 源已是阿里云（见上方 sed 那一段）。
RUN pip config set global.index-url "https://mirrors.aliyun.com/pypi/simple/" && \
    pip config set install.trusted-host "mirrors.aliyun.com" && \
    pip install --upgrade pip setuptools wheel

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

# 启动服务
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
