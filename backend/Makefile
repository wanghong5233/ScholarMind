# Makefile，用于简化 Docker Compose 相关操作

# 默认命令：显示帮助信息
.PHONY: help
help:
	@echo "用法: make [command]"
	@echo "----------------------------------------"
	@echo "常用命令:"
	@echo "  up        - (推荐) 构建镜像并以后台模式启动所有服务"
	@echo "  down      - (推荐) 停止并删除所有容器、网络及数据卷，实现彻底清理"
	@echo "  stop      - 停止所有服务 (不删除容器)"
	@echo "  restart   - 重启所有服务"
	@echo "  build     - 仅构建或重新构建服务的镜像"
	@echo "  logs      - 实时查看所有服务的日志输出"
	@echo "  logs-api  - (推荐) 仅实时查看后端API服务的日志"
	@echo "  logs-agent - 实时查看 Doc Studio 服务日志"
	@echo "  logs-mineru - 实时查看 MinerU 服务日志"
	@echo "  ps        - 查看当前服务的容器状态"
	@echo ""
	@echo "调试命令:"
	@echo "  shell     - 进入后端服务 (backend) 的容器"
	@echo "  shell-agent - 进入 Doc Studio 服务容器"
	@echo "  db-shell  - 进入 PostgreSQL 数据库容器并连接到 scholarmind_db"
	@echo "  redis-cli - 进入 Redis 容器并启动 redis-cli"
	@echo ""
	@echo "Doc Studio 安全命令（只操作 doc_studio，不影响其它服务）:"
	@echo "  restart-agent       - 仅重启 Doc Studio 容器（不重新构建镜像）"
	@echo "  build-agent-image   - 在 services/doc_studio 目录下重新构建镜像"
	@echo "  up-agent            - 仅启动/更新 Doc Studio 服务"
	@echo "  rebuild-agent       - 先 build-agent-image 再 up-agent，安全重建 Doc Studio"
	@echo ""
	@echo "磁盘清理命令（请在确认无重要数据后使用）:"
	@echo "  docker-prune-soft   - 清理未使用的容器/网络/镜像缓存（相对安全）"
	@echo "  docker-prune-hard   - 在 soft 基础上删除所有未被使用的镜像（回收更多空间）"
	@echo ""
	@echo "数据库命令:"
	@echo "  migrate   - 在后端容器内执行数据库迁移 (alembic upgrade head)"


# (推荐) 构建并以后台模式启动所有服务
# --build: 在启动前强制重新构建镜像
# -d: 在后台 (detached mode) 运行
.PHONY: up
up:
	docker compose up -d

# (推荐) 停止并删除所有容器、网络及数据卷，实现彻底清理
# -v: 删除与容器关联的匿名数据卷
# --remove-orphans: 删除不再在 compose 文件中定义的服务容器
.PHONY: down
down:
	docker compose down --remove-orphans

# 停止服务 (不删除容器)
.PHONY: stop
stop:
	docker compose stop

# 重启所有服务
.PHONY: restart
restart:
	docker compose restart

# 仅构建或重新构建服务镜像
.PHONY: build
build:
	docker compose build

# 实时查看服务日志
# --tail=0: 只显示新日志，不显示历史
# -f: 持续跟踪日志输出
.PHONY: logs
logs:
	docker compose logs --tail=0 -f

# (推荐) 仅实时查看后端API服务的日志
# --tail=0: 只显示新日志，不显示历史
.PHONY: logs-api
logs-api:
	docker compose logs scholarmind_api --tail=0 -f

# 实时查看 Doc Studio 服务日志
# 默认只从当前时刻开始看（不展示历史），方便区分“本轮”测试产生的日志
# docker compose logs doc_studio --tail=0 -f
.PHONY: logs-agent
logs-agent:
	docker compose logs doc_studio --tail=0 -f

# 实时查看 MinerU 日志
# --tail=0: 只显示新日志，不显示历史
.PHONY: logs-mineru
logs-mineru:
	docker compose logs mineru --tail=0 -f

# 查看服务容器状态
.PHONY: ps
ps:
	docker compose ps

# 进入后端服务的容器
.PHONY: shell
shell:
	docker compose exec scholarmind_api bash

# 进入 Doc Studio 服务容器
.PHONY: shell-agent
shell-agent:
	docker compose exec doc_studio bash

# 进入PostgreSQL数据库容器
# psql -U [用户名] -d [数据库名]
.PHONY: db-shell
db-shell:
	docker compose exec postgres psql -U user -d scholarmind_db

# 进入Redis容器并启动redis-cli
.PHONY: redis-cli
redis-cli:
	docker compose exec redis redis-cli

# 在后端容器内执行数据库迁移
.PHONY: migrate
migrate:
	docker compose exec backend alembic upgrade head

# =========================
# Doc Studio 安全操作命令
# =========================

# 仅重启 Doc Studio 服务容器（快速重启）
# 使用场景：只改了 Python 代码或配置，不需要重新构建镜像
.PHONY: restart-agent
restart-agent:
	docker compose restart doc_studio

# 强制重启 Doc Studio（彻底重启，清除 Python 缓存）
# 使用场景：restart-agent 后代码未生效时使用
.PHONY: force-restart-agent
force-restart-agent:
	docker compose stop doc_studio
	docker compose start doc_studio

# 仅重新构建 Doc Studio 镜像
# 注意：这里使用 docker build + 指定路径，完全绕过 compose 的自动 build 计划
# 优点：不会意外触发 mineru / reranker 等其它大镜像的构建
.PHONY: build-agent-image
build-agent-image:
	cd services/doc_studio && docker build -t backend-doc_studio .

# 仅启动/更新 Doc Studio 服务
# 前提：镜像已经存在（例如刚刚执行过 build-agent-image）
.PHONY: up-agent
up-agent:
	docker compose up -d doc_studio

# 安全重建 Doc Studio：先构建镜像，再启动服务
# 推荐习惯：需要重建 Agent 时只调用这个目标
.PHONY: rebuild-agent
rebuild-agent: build-agent-image up-agent

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

