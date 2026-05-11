# ScholarMind 后端

> 项目入口见根目录 [`README.md`](../README.md)。本文件**只承载后端独有的开发与运维细节**，不复述项目愿景与整体架构。

## 启动

### 默认栈（轻量）

```bash
docker compose up -d --build
```

启动 `scholarmind_api`（8000）、`scholarmind_db`（PostgreSQL + pgvector，5432）、`scholarmind_redis`（6379）。

PDF 解析器顺序默认走云端轻量链路：`llamaparse,unstructured_api,unstructured,pymupdf`。

### 重型本地栈（可选）

`MinerU` / `Grobid` / 本地 Reranker 默认关闭。需要完整本地 demo 时：

```bash
docker compose --profile heavy-local up -d --build
```

### 公网 Tunnel（可选）

为规避不稳定的 QUIC/UDP，`docker-compose.yml` 内置一个 pinned 版本、强制 `http2`（TCP）的 `cloudflared` 服务（profile `public`）：

```bash
# 1. 在 .env 中设置 CF_TUNNEL_TOKEN=<your_token>
# 2. 启动后端 + tunnel
docker compose --profile public up -d --build
```

健康检查：

```bash
docker compose logs -f cloudflared
curl http://localhost:8000/health
curl https://api-scholarmind.wh5233.me/health
```

## Tunnel 自动恢复（Windows）

为防止 cloudflared 偶发抖动，提供 Windows Task Scheduler 看门狗（每分钟轮询）：

```bash
# 1. 在 .env 中设置：SM_PUBLIC_HEALTH_URL=https://api-scholarmind.wh5233.me/health
# 2. 安装任务（首次）
powershell -ExecutionPolicy Bypass -File .\scripts\install_tunnel_watchdog_task.ps1

# 3. 查询任务状态
schtasks /Query /TN "ScholarMind-Tunnel-Watchdog" /V /FO LIST

# 4. 看日志
type .\.watchdog\tunnel_watchdog.log
```

触发重启的条件（三者均满足）：

- 本地 `/health` 健康
- 公网 `/health` 连续 3 次失败
- 冷却窗口 + 当日重启次数上限均允许

避免 restart storm 同时保持公网 demo 可用。

## 常用操作

```bash
docker compose ps                                       # 服务状态
docker compose logs -f scholarmind_api                  # 跟踪 API 日志
docker compose exec scholarmind_api bash                # 进入 API 容器
docker compose exec scholarmind_db psql -U postgres -d scholarmind  # 进入数据库
```

## 数据库迁移（Alembic）

```bash
docker compose exec scholarmind_api alembic upgrade head           # 执行待迁移
docker compose exec scholarmind_api alembic revision -m "<msg>"    # 新建迁移
```

详细规约见 [`app/alembic/README.md`](./app/alembic/README.md)。

## 向量存储（pgvector）

默认 `SM_VECTOR_STORE=pgvector`，Elasticsearch 已退出默认栈。

| 迁移脚本 | 作用 |
|---|---|
| `20_add_pgvector_rag_chunks.py` | `rag_chunks` 表 + 向量索引 + 文本索引（BM25 风格全文检索） |
| `21_add_pgvector_ltm_facts.py` | `ltm_facts` 表，长期记忆召回 |

镜像基于 `pgvector/pgvector:pg15`，自动启用 `vector` 扩展。

## 进一步阅读

| 文档 | 用途 |
|---|---|
| [项目入口（根 README）](../README.md) | 项目愿景、架构总览、demo 入口 |
| [`docs/LOW_COST_CLOUD_DEPLOYMENT_MANUAL.md`](../docs/LOW_COST_CLOUD_DEPLOYMENT_MANUAL.md) | 2C2G ECS 部署坑点档案与不变量 |
| [`docs/LLM_PARAMETER_BASELINE.md`](../docs/LLM_PARAMETER_BASELINE.md) | LLM 三服务参数基线清单 |
| [`docs/LLM_POLICY_ROLLOUT_PLAYBOOK.md`](../docs/LLM_POLICY_ROLLOUT_PLAYBOOK.md) | LLM 策略灰度与回滚 |
| [`app/alembic/README.md`](./app/alembic/README.md) | 数据库 schema 管理 |
| `http://localhost:8000/docs` | Swagger 交互式 API 文档（运行时可用） |
