# DeepResearch Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 阿里云 PyPI 镜像 —— ECS 内网访问，避免走公网导致 build 拖慢/超时。
# 与 backend/app/Dockerfile 保持同源策略。
RUN pip config set global.index-url "https://mirrors.aliyun.com/pypi/simple/" && \
    pip config set install.trusted-host "mirrors.aliyun.com" && \
    pip install --upgrade pip setuptools wheel

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PORT=8004

EXPOSE 8004

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004"]
