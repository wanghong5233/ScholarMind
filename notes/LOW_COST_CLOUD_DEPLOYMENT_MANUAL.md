# 低成本云端部署技术手册（内部）

> 内部笔记：用于 ScholarMind 与 ScriptLens 的作品集/面试演示部署复用。  
> 不放入公开 README，不作为对外文档。

## 1. 场景定位

这是学生作品集和面试考核场景，不是商业运营场景。

目标：

- 低成本：阿里云大陆 ECS 2C2G，99 元/年
- 长期可访问：面试官可随时打开 Vercel 前端体验
- 本机退出公网链路：Cloudflare Tunnel 从本机迁到 ECS
- 可复用：后续 ScriptLens 复用同一台 ECS、PostgreSQL、Redis、Tunnel

不做：

- ICP 备案
- Caddy/Nginx 直连域名
- K8s
- RDS
- GPU、本地大模型、本地重型 parser

## 2. 统一架构

```mermaid
flowchart TD
    userBrowser["User Browser"] --> vercelFrontend["Vercel Frontend"]
    vercelFrontend --> cfEdge["Cloudflare Edge HTTPS"]
    cfEdge --> tunnel["cloudflared on ECS"]
    tunnel --> scholarmindApi["ScholarMind API"]
    tunnel --> scriptLensApi["ScriptLens API"]
    scholarmindApi --> postgres["PostgreSQL pgvector"]
    scriptLensApi --> postgres
    scholarmindApi --> redis["Redis Queue Cache"]
    scriptLensApi --> redis
    scholarmindApi --> fileVolume["PDF Workspace Volume"]
    scriptLensApi --> scriptVolume["ScriptLens Upload Volume"]
    scholarmindApi --> externalApis["LLM Parser Embedding Rerank APIs"]
    scriptLensApi --> externalApis
```

## 3. ECS 初始化

### 3.1 已购实例基线

当前共用云服务器配置如下，作为后续 ScholarMind、ScriptLens、个人主页等 MVP 项目的统一部署基线：

- 云厂商：阿里云 ECS
- 实例名：`portfolio-mvp-shanghai-01`
- 角色定位：作品集 / 面试项目 / MVP 共用后端主机
- 地域：`华东2（上海）`
- 实例规格：`ecs.e-c1m1.large`
- CPU / 内存：`2 vCPU / 2 GiB`
- 系统镜像：`Ubuntu 22.04 64位`
- 系统盘：`ESSD Entry 40 GiB`
- 带宽：固定 `3 Mbps`
- 公网入口：`Cloudflare Tunnel` 跑在 ECS 上
- 备案策略：暂不备案，不直接用 ECS 绑定大陆域名提供公网 HTTP 服务

不要写入仓库或公开文档：

- root 密码 / SSH 私钥
- ECS 公网 IP
- `.env.production`
- API keys
- `CF_TUNNEL_TOKEN`

### 3.2 推荐配置模板

后续如果再买机器，优先复用：

- 阿里云 ECS 经济型 e 实例
- 2 vCPU / 2GB RAM / 40GB ESSD / 3M 带宽
- Ubuntu 22.04 LTS

### 3.3 多项目目录规划

这台机器不是 ScholarMind 专用，宿主机目录按“应用代码 / 持久数据 / 备份 / 日志”拆分。`/opt` 是 Linux 约定中用于第三方或自维护应用的目录，适合放作品集项目代码与运维目录，不与系统包、用户 home 目录混用。

```bash
sudo mkdir -p /opt/apps/scholarmind
sudo mkdir -p /opt/apps/scriptlens
sudo mkdir -p /opt/apps/homepage
sudo mkdir -p /opt/data/scholarmind/storage
sudo mkdir -p /opt/data/scriptlens/storage
sudo mkdir -p /opt/data/homepage
sudo mkdir -p /opt/backups/scholarmind
sudo mkdir -p /opt/backups/scriptlens
sudo mkdir -p /opt/backups/postgres
sudo mkdir -p /opt/logs/scholarmind
sudo mkdir -p /opt/logs/scriptlens
sudo chown -R $USER:$USER /opt/apps /opt/data /opt/backups /opt/logs
```

约定：

| 路径 | 内容 | 维护规则 |
|---|---|---|
| `/opt/apps/scholarmind` | ScholarMind 代码仓库 | 只放 git checkout，不放上传文件和密钥 |
| `/opt/apps/scriptlens` | ScriptLens 代码仓库 | 只放 git checkout |
| `/opt/apps/homepage` | 个人主页或博客后端 | 只放应用代码 |
| `/opt/data/scholarmind/storage` | ScholarMind 上传 PDF、在线导入 PDF、解析中间文件 | 由 `SM_STORAGE_ROOT` 挂载到容器 |
| `/opt/data/scriptlens/storage` | ScriptLens 上传文件 | 与 ScholarMind 文件隔离 |
| `/opt/data/homepage` | 个人主页持久化数据 | 与应用代码隔离 |
| `/opt/backups/scholarmind` | ScholarMind 文件备份 | 与 PostgreSQL dump 分开 |
| `/opt/backups/scriptlens` | ScriptLens 文件备份 | 与 ScholarMind 备份分开 |
| `/opt/backups/postgres` | PostgreSQL dump | 按应用名和日期命名 |
| `/opt/logs/scholarmind` | ScholarMind 自定义运维脚本日志 | Docker 日志仍由 json-file 管理 |
| `/opt/logs/scriptlens` | ScriptLens 自定义运维脚本日志 | 不混入应用仓库 |

目录边界：

| 类型 | 位置 | 是否提交 git |
|---|---|---|
| 代码 | `/opt/apps/<project>` | 是，来自远端仓库 |
| 运行配置 | `/opt/apps/<project>/backend/.env.production` | 否 |
| 上传文件 / 解析产物 | `/opt/data/<project>` | 否 |
| PostgreSQL 数据 | Docker named volume | 否 |
| 备份 | `/opt/backups` | 否 |
| 运维日志 | `/opt/logs` | 否 |

ScholarMind 生产环境必须设置：

```env
SM_STORAGE_ROOT=/opt/data/scholarmind/storage
```

### 3.4 系统初始化

初始化：

```bash
sudo timedatectl set-timezone Asia/Shanghai
sudo apt-get update
sudo apt-get install -y curl git ca-certificates gnupg lsb-release
```

安装 Docker：

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
docker compose version
```

配置 swap：

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

## 4. ScholarMind 后端迁移

```bash
git clone <你的仓库地址> scholarmind
cd scholarmind/backend
cp .env.production.example .env.production
```

`.env.production` 必填：

- `JWT_SECRET_KEY`
- `CF_TUNNEL_TOKEN`
- `SM_CORS_ALLOW_ORIGINS`
- `SM_ADMIN_CONSOLE_USERNAME`
- `SM_ADMIN_CONSOLE_PASSWORD`
- `SEMANTIC_SCHOLAR_API_KEY`
- `DASHSCOPE_API_KEY` / `OPENAI_API_KEY`
- `SM_LLAMA_PARSE_API_KEY`
- `SM_UNSTRUCTURED_API_KEY`
- `SM_VECTOR_STORE=pgvector`
- `SM_RERANKER_TYPE=dashscope`
- 不在 `.env.production` 显式设置 `SM_PARSER_ORDER`（使用代码默认 `llamaparse,unstructured_api,pymupdf`）
- `SM_STORAGE_ROOT=/opt/data/scholarmind/storage`
- `SM_DEMO_ENTRY_ENABLED=true`
- `SM_DEMO_USERNAME=testuser`
- `DOC_STUDIO_SERVICE_URL=http://doc_studio:8003`
- `DEEP_RESEARCH_SERVICE_URL=http://deep_research:8004`

公网访问契约：

| 类型 | 当前值 | 不变式 |
|---|---|---|
| 前端正式入口 | `https://scholarmind.wh5233.me` | Vercel 绑定此域名，面试官从这里进入产品 |
| Demo 入口 | `https://scholarmind.wh5233.me/demo` | `/demo` 调用 `users/demo-entry`，登录到云端公共 `testuser` |
| 后端 API origin | `https://api-scholarmind.wh5233.me` | Cloudflare Tunnel 暴露主 API，不直接暴露 ECS 端口 |
| 前端 API base | `https://api-scholarmind.wh5233.me/api` | `VITE_API_BASE` 必须带 `/api` |
| 后端 CORS origin | `https://scholarmind.wh5233.me` | `SM_CORS_ALLOW_ORIGINS` 只写 origin，不写 `/demo` 或 `/testuser` |
| Tunnel origin service | `http://scholarmind_api:8000` | Cloudflare `Published application routes` 指向 Docker Compose service name |
| 公网健康检查 | `https://api-scholarmind.wh5233.me/health` | 用于验证 Cloudflare 到 API 的完整链路 |

`demo-scholarmind.wh5233.me` 不作为新部署入口。已有 Vercel 域名可删除，或跳转到 `https://scholarmind.wh5233.me/demo`。

首次部署先构建主 API 镜像，再启动 ScholarMind MVP 全量服务（主站 API、Doc Studio、Deep Research、PostgreSQL、Redis、Cloudflare Tunnel）：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build scholarmind_api doc_studio deep_research
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

检查：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail 100 scholarmind_api
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail 100 doc_studio
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail 100 deep_research
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail 100 cloudflared
curl -sS http://127.0.0.1:8000/health
curl -sS https://api-scholarmind.wh5233.me/health
```

## 5. Vercel 对接

Vercel 生产域名使用：

```text
https://scholarmind.wh5233.me
```

前端环境变量：

```env
VITE_API_BASE=https://api-scholarmind.wh5233.me/api
VITE_DEMO_ENTRY_ENABLED=true
VITE_DEMO_USERNAME=testuser
VITE_ENABLE_ADMIN_UI=false
```

访问入口：

| 链接 | 用途 |
|---|---|
| `https://scholarmind.wh5233.me` | 正常登录入口 |
| `https://scholarmind.wh5233.me/demo` | 一键进入公共 `testuser` |
| `https://api-scholarmind.wh5233.me/health` | 后端公网健康检查 |

当前前端已实现 `/demo` 路由，未实现 `/testuser` 路由。`testuser` 是账号名，不是 URL path。使用 `/testuser` 作为入口需要新增前端路由别名，当前 MVP 不需要。

修改 Vercel 环境变量后重新部署。

## 6. 2C2G 资源约束

必须遵守：

- API worker=1
- 不使用 `--reload`
- PostgreSQL 使用低内存参数
- Redis 设置 `maxmemory`
- Docker 日志限制 `max-size` / `max-file`
- 上传大小限制
- Demo 数据预解析入库

### 6.1 云端构建网络约束

ECS 上的 Docker build 使用 ECS 自己的公网出口，不走本机 VPN。生产 Dockerfile 必须显式配置国内镜像源。

| 构建阶段 | 必须配置 | 原因 |
|---|---|---|
| Docker base image | Docker daemon `registry-mirrors` | 避免 Docker Hub 直连超时 |
| Debian / Ubuntu apt | `mirrors.aliyun.com` 或同等级国内源 | 避免 `deb.debian.org` 在 ECS 上长时间 retry |
| Python pip | `mirrors.aliyun.com/pypi/simple/` | ECS 内网链路优先，避免公网 PyPI / 跨网镜像拖慢构建 |

2C2G 首次部署构建必须拆成“清空运行态 → 前台 build → 启动服务”。构建命令不包 `timeout`，不使用 `nohup`，不追加后台 `&`。

```bash
cd /opt/apps/scholarmind/backend
docker compose -f docker-compose.prod.yml down -v --remove-orphans
docker system prune -a -f --volumes
docker buildx prune -a -f
docker compose -f docker-compose.prod.yml --env-file .env.production build scholarmind_api doc_studio deep_research
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

Workbench / SSH 不稳定时，使用 `tmux` 承载前台构建；`tmux` 只保存终端会话，不改变 Docker build 的前台可观测性。

ScriptLens 复用同一台 ECS 时也遵守此规则：生产 Dockerfile 不依赖本机 VPN，不直接使用默认 Docker Hub、`deb.debian.org` 或默认 PyPI 作为唯一来源。

## 6.x Cloudflare Tunnel 稳定性

公网入口走 cloudflared on ECS。当前部署形态：

| 维度 | 当前配置 | 说明 |
|---|---|---|
| 镜像版本 | `cloudflare/cloudflared:2026.2.0` 固定 | 配合 `--no-autoupdate`，避免自更新引入抖动 |
| 协议 | `--protocol http2` | 使用 TCP/443，规避家用宽带/部分机房 UDP 抖动 |
| 边连接 | cloudflared 默认与 Cloudflare 维持 4 条长连接 | 单条断不影响公网可达 |
| 日志 | `--loglevel info` | 出故障可在 Cloudflare 控制台 + 容器日志双向定位 |
| 指标 | `--metrics 0.0.0.0:2000`（仅 docker 网络内） | 提供 `/ready`、`/metrics`，便于排查 |
| 内存上限 | 128M | 防止异常增长拖垮 2G ECS |
| 进程恢复 | `restart: unless-stopped` | 容器异常退出由 Docker 拉起 |
| 健康真值 | Cloudflare Zero Trust 控制台 Tunnel 状态 | distroless 镜像无 curl/wget，docker healthcheck 不可靠 |

排错命令：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail 200 cloudflared
docker compose -f docker-compose.prod.yml --env-file .env.production exec scholarmind_api \
  python -c "import urllib.request as u; print(u.urlopen('http://cf_tunnel_scholarmind:2000/ready', timeout=3).read())"
curl -sS https://api-scholarmind.wh5233.me/health
```

可选 HA 升级：在 Zero Trust 控制台同一 Tunnel token 下再启一个 cloudflared 实例（同机或异机）即可自动负载均衡，单实例 crash 时公网零中断。当前 2C2G 单机阶段不强制开启。

## 7. Demo 与多租户边界

公网演示采用公共账号模型：`demo-entry` 只把访问者登录到 `SM_DEMO_USERNAME`，不改变该账号的产品权限。

演示阶段必须满足：

- 保留真实登录系统
- 登录页提供一键 Demo，直接进入公共 `testuser`
- 公共 `testuser` 拥有普通账号的完整功能，包括上传、删除、回退、继续对话
- 公共 `testuser` 的论文库、知识库、对话历史来自云端 PostgreSQL 与 `SM_STORAGE_ROOT`
- 云端不会自动继承本地数据；需要显式迁移或恢复 demo 基线
- 面试前按备份恢复公共 `testuser`，保证演示数据可重复

暂不做：

- 完整账号中心
- 找回密码
- 复杂 RBAC
- 组织空间和计费系统
- 为每位访问者克隆独立 demo workspace
- 将 `demo-entry` token 做成只读或禁用核心产品功能

## 8. ScriptLens 复用模板

ScriptLens 不另买服务器。

复用：

- 同一台 ECS
- 同一 PostgreSQL / Redis
- 同一 cloudflared
- 前端继续 Vercel

隔离：

- 独立 database 或 schema，例如 `scriptlens`
- 独立上传 volume
- 独立 API hostname，例如 `api-scriptlens.wh5233.me`

最小 compose 示例：

```yaml
services:
  scriptlens_api:
    image: your-registry/scriptlens:latest
    container_name: scriptlens_api
    hostname: scriptlens_api
    env_file:
      - .env.scriptlens.production
    environment:
      DATABASE_URL: postgresql://postgres:${POSTGRES_PASSWORD}@scholarmind_db:5432/scriptlens
      REDIS_HOST: scholarmind_redis
      REDIS_PORT: 6379
      REDIS_DB: 0
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
      CORS_ALLOW_ORIGINS: https://your-scriptlens-frontend.vercel.app
      MAX_UPLOAD_SIZE_MB: 30
      ENABLE_DEMO_ENTRY: "true"
      DEMO_USERNAME: testuser
    command:
      [
        "uvicorn",
        "main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8005",
        "--workers",
        "1",
      ]
    volumes:
      - scriptlens_storage:/app/storage
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8005/health', timeout=5).read()"]
      interval: 30s
      timeout: 10s
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    networks:
      - scholarmind_net

volumes:
  scriptlens_storage:
    name: scriptlens_storage

networks:
  scholarmind_net:
    external: true
    name: scholarmind_net
```

## 9. 上线验收清单

### 基础

- [ ] ECS 已购买并初始化
- [ ] 实例名已改为 `portfolio-mvp-shanghai-01`
- [ ] 宿主机目录已创建：`/opt/apps`、`/opt/data`、`/opt/backups`、`/opt/logs`
- [ ] Docker / Compose 可用
- [ ] 2GB swap 已生效
- [ ] `.env.production` 已配置且未提交

### ScholarMind

- [ ] `docker compose -f docker-compose.prod.yml --env-file .env.production build scholarmind_api doc_studio deep_research` 成功
- [ ] `docker compose -f docker-compose.prod.yml --env-file .env.production up -d` 成功
- [ ] `scholarmind_api` / `scholarmind_db` / `scholarmind_redis` / `cloudflared` 均为 `Up`
- [ ] `SM_VECTOR_STORE=pgvector`
- [ ] `SM_STORAGE_ROOT=/opt/data/scholarmind/storage`
- [ ] 重服务未启动：Elasticsearch / MinerU / Grobid / local reranker
- [ ] `/health` 本地和公网均可访问
- [ ] Vercel 前端可登录、问答、显示 citation
- [ ] Demo 入口可用
- [ ] 用户菜单可退出登录

### ScriptLens

- [ ] 独立 database/schema
- [ ] 独立文件 volume
- [ ] cloudflared 新 hostname
- [ ] 前端 Vercel API base URL 已切换
- [ ] 一键 demo 与预置剧本可用

### 回退

- [ ] 旧本机 tunnel 方案保留
- [ ] Cloudflare hostname 可快速切回
- [ ] 10 分钟内可恢复演示

## 10. 部署经验

### 10.1 环境边界

| 误判 | 经验 |
|---|---|
| 本机能访问等于云端能访问 | ECS 使用自己的公网出口，不继承本机 VPN、代理和 DNS |
| 本地数据库等于云端数据库 | 云端 PostgreSQL 是独立实例，账号、演示数据、上传文件都需要显式创建或迁移 |
| 本地代码修改等于线上生效 | 前端由 Vercel build 生效，后端由 ECS Docker 镜像生效，二者发布链路不同 |
| 仓库文件等于运行配置 | `.env.production`、密钥、上传文件、数据库数据都在宿主机或 Docker volume，不在 git 中 |

### 10.2 云端构建

| 风险 | 规则 |
|---|---|
| Docker Hub 超时 | 配置 Docker daemon `registry-mirrors` 后再拉基础镜像和第三方镜像 |
| APT 长时间无输出 | 生产 Dockerfile 显式配置国内 Debian / Ubuntu 镜像源 |
| PyPI wheel 查找失败 | 先排查镜像源和网络，不直接判断依赖版本不存在 |
| 外部数据下载卡住 | 不在 Docker build 阶段下载 NLTK、模型权重、parser 数据等非必要资源 |
| 长构建不可见 | 使用前台 `docker compose build`；SSH 不稳定时用 `tmux` 保持同一个前台会话 |
| 小内存构建 | 2C2G 主机必须配置 swap，API worker=1，重服务默认关闭 |

### 10.3 公网链路

| 组件 | 不变式 |
|---|---|
| Cloudflare Tunnel upstream | 指向 Docker Compose service name：`http://<service_name>:<port>` |
| Linux Docker 网络 | 不使用 Docker Desktop 专属的 `host.docker.internal` |
| Tunnel DNS 记录 | Cloudflare 自动生成的 `Tunnel` 记录保持 `Proxied` |
| Vercel 前端 DNS | 指向 Vercel 的 CNAME 保持 `DNS only` |
| CORS | 只写 origin，例如 `https://<frontend-domain>`，不写 path |
| 健康检查 | 先验证公网 `/health`，再验证前端业务请求 |

### 10.4 发布边界

| 改动类型 | 生效方式 |
|---|---|
| 前端 UI | commit + push 后由 Vercel 自动 build |
| Vite 环境变量 | build-time 注入，修改后必须重新部署前端 |
| 后端代码 | ECS `git pull` 后重新 build / restart 容器 |
| 后端环境变量 | 修改 ECS `.env.production` 后重启相关服务 |
| 数据库内容 | 通过注册、导入、迁移或 restore 修改，不随代码发布改变 |
| 文件存储 | 通过挂载目录和备份策略管理，不随代码发布改变 |

### 10.5 演示账号

| 规则 | 说明 |
|---|---|
| 公共账号是普通账号 | 演示账号不做只读、不做功能阉割，权限与普通用户一致 |
| 快捷入口只做登录 | 入口只负责登录公共账号，登录后不显示 Demo UI |
| 数据独立于本地 | 云端演示账号、论文、会话、文件不会自动继承本地环境 |
| 演示数据可恢复 | 面试前通过数据库 dump 和文件备份恢复稳定基线 |

### 10.6 操作纪律

| 场景 | 规则 |
|---|---|
| 命令失败 | 先判断是否已改变系统状态，再给后续命令 |
| 长时间无输出 | 不盲等，先看日志、网络源、下载源和构建阶段 |
| 控制台 UI 变化 | 先确认当前页面属于 DNS、Tunnel、Vercel 还是 ECS，不跨产品套用入口名称 |
| Git 脏文件 | 提交前看 `git diff`，不要只看 `git status` 文件名 |
| 误改无关文件 | 不混入部署提交，先确认 diff，再单独 restore 或单独提交 |
| 密钥 | `.env.production`、API keys、Tunnel token 不写入 git |

### 10.7 低配 ECS 首次部署纪律

| 维度 | 约束 | 规则 |
|---|---|---|
| 数据状态 | 首次部署没有生产数据 | 可以删除容器、镜像、volume、build cache，先换取确定的资源基线 |
| 内存 | 2C2G 不能同时承载旧服务和 Docker build | build 前停止并清空运行态，build 完再启动服务 |
| 磁盘 | 40GB 系统盘不适合保留多代大镜像 | 首次部署不保留旧镜像，不做灰度镜像切换 |
| 可观测性 | 后台日志和管道输出会引入缓冲误判 | 构建在前台运行；需要断线恢复时用 `tmux` |
| 终止语义 | `timeout` 会向 Docker compose 客户端发信号并取消 build | 不用 `timeout` 包裹 Docker build / up |
| 网络 | ECS 不继承本机 VPN、代理和 DNS | Dockerfile 内显式配置 apt / pip 国内源 |

## 11. 本地↔Prod 配置对齐表

> 教训：曾经因为 `SM_LLM_MODEL_AUX` 本地是 `gpt-5-mini`、ECS 是 `qwen-turbo`，导致同一句问题在本地能触发 RAG，在 ECS 走纯 LLM 直答（意图识别走 AUX，AUX 不稳定 → 路由失败）。

把所有 env 变量分成两类：

- **业务关键变量**：必须 ECS 与本地完全一致，否则"线上有 bug 但本地复现不了"。
- **环境关键变量**：必须不一致，反映 host / 网络 / CORS / 数据卷的真实差异。

### 11.1 业务关键变量（必须对齐）

| 变量 | 期望值 | 来源 |
|---|---|---|
| `SM_LLM_TYPE` | `openai` | 团队统一选择（gpt-5.2 主答） |
| `OPENAI_MODEL_NAME` | `gpt-5.2` | 主答模型 |
| `SM_LLM_MODEL_ANSWER` | `gpt-5.2` | 显式声明，避免回退 |
| `SM_LLM_MODEL_AUX` | `gpt-5-mini` | 意图识别走这个，错位会让 RAG 不触发 |
| `SM_LLM_MODEL_GRAPH` | `gpt-5-mini` | 图谱抽取 |
| `SM_LLM_MODEL_SUMMARY` | `gpt-5-mini` | 对话摘要 |
| `DASHSCOPE_MODEL_NAME` | `qwen3-max` | DashScope 兜底用，禁止 qwen-plus |
| `SM_DASHSCOPE_RERANK_MODEL` | `qwen3-rerank` | 精排 |
| `SM_EMBEDDER_TYPE` | `dashscope` | embedding 用 DashScope |
| `SM_EMBEDDING_MODEL` | `text-embedding-v3` | 1024 维 |
| `SM_RERANKER_TYPE` | `dashscope` | 走云端 API |
| `SM_VECTOR_STORE` | `pgvector` | 不再用 ES |
| `SM_RAG_TOPK` | `8` | 受 MIN=4/MAX=8 夹紧，给上限 |
| `SM_RETRIEVE_PAGE_SIZE` | `8` | 与 topK 同步 |
| `SM_PARSER_ORDER` | **不在 .env 配置** | 让代码默认 `llamaparse,unstructured_api,pymupdf` 生效 |

**全局禁用名单**（不允许出现在任何 env / 代码 / 前端选择器）：
- `qwen-plus` — 输出不稳定
- `qwen-turbo` — 同上，且更廉价但更差

### 11.2 环境关键变量（必须不一致）

| 变量 | 本地 | ECS |
|---|---|---|
| `DATABASE_URL` | `localhost:5432` | `scholarmind_db:5432`（容器名）|
| `REDIS_HOST` | `localhost` | `scholarmind_redis` |
| `SM_STORAGE_ROOT` | 本地任意路径 | `/opt/data/scholarmind/storage` |
| `SM_CORS_ALLOW_ORIGINS` | `*` 或 `localhost:5173` | `https://scholarmind.wh5233.me` |
| `JWT_SECRET_KEY` | 任意 dev key | 强随机，长期保留 |
| `SM_ADMIN_CONSOLE_PASSWORD` | dev | 强密码 |
| `CF_TUNNEL_TOKEN` | 不需要 | 真实 token |
| `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / `SM_LLAMA_PARSE_API_KEY` | 个人 key | 团队/服务 key |

### 11.3 单文件维护原则

- 业务关键变量在 `backend/.env.production.example` 显式列全（不依赖代码默认）。
- 升级/调整业务变量时：**先改 `.env.production.example`（入仓库）→ 同步本地 `.env` → ssh ECS 改 `/opt/apps/scholarmind/.env.production` → 重启容器**。
- 任何"本地能跑、ECS 不能跑"的问题，第一步对照本表逐项 diff，再开始翻日志。

### 11.4 ECS 端 .env.production 维护清单（不在 git 里）

文件路径：`/opt/apps/scholarmind/.env.production`

| 字段 | 必填 | 说明 |
|---|---|---|
| `POSTGRES_PASSWORD` | ✓ | 与 DATABASE_URL 中密码一致 |
| `JWT_SECRET_KEY` | ✓ | 强随机，泄漏需重发所有 token |
| `OPENAI_API_KEY` | ✓ | 主答 LLM |
| `DASHSCOPE_API_KEY` | ✓ | embedding + rerank |
| `SM_LLAMA_PARSE_API_KEY` | ✓ | 主解析器 |
| `SM_UNSTRUCTURED_API_KEY` | 推荐 | llamaparse 失败时备用 |
| `CF_TUNNEL_TOKEN` | ✓ | 公网入口 |
| `SEMANTIC_SCHOLAR_API_KEY` | 推荐 | 在线检索 |
| `WEB_SEARCH_API_KEY` / `TAVILY_API_KEY` | 可选 | DeepResearch 网络搜索 |
| 11.1 表全部业务变量 | ✓ | 与 `.env.production.example` 一一对齐 |
| 11.2 表 ECS 侧变量 | ✓ | 与本地必然不同 |

## 12. 常见故障

- `pgvector` 扩展报错：确认数据库镜像是 `pgvector/pgvector:pg15`
- `demo-entry` 不可用：检查 `SM_DEMO_ENTRY_ENABLED=true` 与 `testuser` 是否存在
- 401 跳登录循环：检查 Vercel API base URL 和 CORS
- OOM：确认 swap、worker=1、日志限额、上传限制
- tunnel 不通：检查 `CF_TUNNEL_TOKEN` 是否过期，cloudflared 日志是否鉴权失败
