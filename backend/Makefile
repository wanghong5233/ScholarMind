# Makefile，用于简化 Docker Compose 相关操作

# Compose 项目名（与 backend/.env 的 COMPOSE_PROJECT_NAME 保持一致）
# 如需覆盖：make PROJECT=xxx up
PROJECT ?= scholarmind
DC := docker compose -p $(PROJECT)

# 默认命令：显示帮助信息
.PHONY: help
help:
	@echo Usage: make [target]
	@echo ----------------------------------------
	@echo Common targets:
	@echo   up           - Start all services (NO build; avoids accidental rebuild/download)
	@echo   up-build     - Build images and start all services (may take time/disk)
	@echo   down         - Stop/remove containers and network (keep volumes)
	@echo   down-v       - DANGEROUS: down + remove volumes (data loss)
	@echo   stop         - Stop all services (keep containers)
	@echo   restart      - Restart all services
	@echo   build        - Build images for services
	@echo   ps           - Show service status
	@echo
	@echo Logs (tail from NOW):
	@echo   logs         - Follow logs for all services
	@echo   logs-api     - Follow logs for scholarmind_api
	@echo   logs-api-history - Show last 200 lines for scholarmind_api
	@echo   logs-studio  - Follow logs for doc_studio
	@echo   logs-research- Follow logs for deep_research
	@echo   logs-reranker- Follow logs for reranker
	@echo   logs-mineru  - Follow logs for mineru
	@echo   logs-grobid  - Follow logs for grobid
	@echo
	@echo Debug:
	@echo   shell        - Exec shell in scholarmind_api container
	@echo   shell-studio - Exec shell in doc_studio container
	@echo   shell-research- Exec shell in deep_research container
	@echo   db-shell     - Enter PostgreSQL (psql)
	@echo   redis-cli    - Enter redis-cli
	@echo
	@echo Doc Studio safe ops:
	@echo   restart-studio     - Restart doc_studio (no rebuild)
	@echo   rebuild-studio     - Build + recreate doc_studio only
	@echo DeepResearch safe ops:
	@echo   restart-research   - Restart deep_research (no rebuild)
	@echo   rebuild-research   - Build + recreate deep_research only
	@echo
	@echo Disk cleanup:
	@echo   docker-prune-soft  - Safe prune (system prune)
	@echo   docker-prune-hard  - Aggressive prune (remove unused images)
	@echo
	@echo DB:
	@echo   migrate      - Run alembic upgrade head in scholarmind_api


# (推荐) 构建并以后台模式启动所有服务
# --build: 在启动前强制重新构建镜像
# -d: 在后台 (detached mode) 运行
.PHONY: up
up:
	$(DC) up -d --no-build

.PHONY: up-build
up-build:
	$(DC) up -d --build

# (推荐) 停止并删除所有容器、网络及数据卷，实现彻底清理
# -v: 删除与容器关联的匿名数据卷
# --remove-orphans: 删除不再在 compose 文件中定义的服务容器
.PHONY: down
down:
	$(DC) down --remove-orphans

.PHONY: down-v
down-v:
	$(DC) down --remove-orphans -v

# 停止服务 (不删除容器)
.PHONY: stop
stop:
	$(DC) stop

# 重启所有服务
.PHONY: restart
restart:
	$(DC) restart

# 仅构建或重新构建服务镜像
.PHONY: build
build:
	$(DC) build

# 实时查看服务日志
# --tail=0: 只显示新日志，不显示历史
# -f: 持续跟踪日志输出
.PHONY: logs
logs:
	$(DC) logs --tail=0 -f

# (推荐) 仅实时查看后端API服务的日志
# --tail=0: 只显示新日志，不显示历史
.PHONY: logs-api
logs-api:
	$(DC) logs scholarmind_api --tail=0 -f

.PHONY: logs-api-history
logs-api-history:
	$(DC) logs scholarmind_api --tail=200

# 实时查看 Doc Studio 服务日志
# 默认只从当前时刻开始看（不展示历史），方便区分“本轮”测试产生的日志
# docker compose logs doc_studio --tail=0 -f
.PHONY: logs-agent
logs-agent:
	$(DC) logs doc_studio --tail=0 -f

.PHONY: logs-studio
logs-studio: logs-agent

# 实时查看 MinerU 日志
# --tail=0: 只显示新日志，不显示历史
.PHONY: logs-mineru
logs-mineru:
	$(DC) logs mineru --tail=0 -f

.PHONY: logs-research
logs-research:
	$(DC) logs deep_research --tail=0 -f

.PHONY: logs-reranker
logs-reranker:
	$(DC) logs reranker --tail=0 -f

.PHONY: logs-grobid
logs-grobid:
	$(DC) logs grobid --tail=0 -f

# 查看服务容器状态
.PHONY: ps
ps:
	$(DC) ps

# 进入后端服务的容器
.PHONY: shell
shell:
	$(DC) exec scholarmind_api sh

# 进入 Doc Studio 服务容器
.PHONY: shell-agent
shell-agent:
	$(DC) exec doc_studio sh

.PHONY: shell-studio
shell-studio: shell-agent

.PHONY: shell-research
shell-research:
	$(DC) exec deep_research sh

# 进入PostgreSQL数据库容器
# psql -U [用户名] -d [数据库名]
.PHONY: db-shell
db-shell:
	$(DC) exec scholarmind_db psql -U postgres -d scholarmind

# 进入Redis容器并启动redis-cli
.PHONY: redis-cli
redis-cli:
	$(DC) exec redis redis-cli

# 在后端容器内执行数据库迁移
.PHONY: migrate
migrate:
	$(DC) exec scholarmind_api alembic upgrade head

# =========================
# Doc Studio 安全操作命令
# =========================

# 仅重启 Doc Studio 服务容器（快速重启）
# 使用场景：只改了 Python 代码或配置，不需要重新构建镜像
.PHONY: restart-agent
restart-agent:
	$(DC) restart doc_studio

.PHONY: restart-studio
restart-studio: restart-agent

.PHONY: restart-research
restart-research:
	$(DC) restart deep_research

# 强制重启 Doc Studio（彻底重启，清除 Python 缓存）
# 使用场景：restart-agent 后代码未生效时使用
.PHONY: force-restart-agent
force-restart-agent:
	$(DC) stop doc_studio
	$(DC) start doc_studio

# 仅重新构建 Doc Studio 镜像
# 注意：这里使用 docker build + 指定路径，完全绕过 compose 的自动 build 计划
# 优点：不会意外触发 mineru / reranker 等其它大镜像的构建
.PHONY: build-agent-image
build-agent-image:
	$(DC) build doc_studio

# 仅启动/更新 Doc Studio 服务
# 前提：镜像已经存在（例如刚刚执行过 build-agent-image）
.PHONY: up-agent
up-agent:
	$(DC) up -d --no-build doc_studio

# 安全重建 Doc Studio：先构建镜像，再启动服务
# 推荐习惯：需要重建 Agent 时只调用这个目标
.PHONY: rebuild-agent
rebuild-agent: build-agent-image up-agent

.PHONY: rebuild-studio
rebuild-studio:
	$(DC) build doc_studio
	$(DC) up -d --no-build --force-recreate --no-deps doc_studio

.PHONY: rebuild-research
rebuild-research:
	$(DC) build deep_research
	$(DC) up -d --no-build --force-recreate --no-deps deep_research

# =========================
# Docker 磁盘空间清理命令
# =========================

# 相对安全的清理：删除未使用的容器 / 网络 / 镜像缓存（不删除正在使用的镜像）
.PHONY: docker-prune-soft
docker-prune-soft:
	docker system prune -f

# 更激进的清理：在 soft 的基础上，删除所有“未被容器使用”的镜像
# 使用前请确认没有特别难以重新构建的镜像
.PHONY: docker-prune-hard
docker-prune-hard:
	docker system prune -f
	docker image prune -a -f

