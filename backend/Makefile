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
	@echo "  ps        - 查看当前服务的容器状态"
	@echo ""
	@echo "调试命令:"
	@echo "  shell     - 进入后端服务 (backend) 的容器"
	@echo "  db-shell  - 进入 PostgreSQL 数据库容器并连接到 scholarmind_db"
	@echo "  redis-cli - 进入 Redis 容器并启动 redis-cli"
	@echo ""
	@echo "数据库命令:"
	@echo "  migrate   - 在后端容器内执行数据库迁移 (alembic upgrade head)"


# (推荐) 构建并以后台模式启动所有服务
# --build: 在启动前强制重新构建镜像
# -d: 在后台 (detached mode) 运行
.PHONY: up
up:
	docker compose up

# (推荐) 停止并删除所有容器、网络及数据卷，实现彻底清理
# -v: 删除与容器关联的匿名数据卷
# --remove-orphans: 删除不再在 compose 文件中定义的服务容器
.PHONY: down
down:
	docker compose down -v --remove-orphans

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
# -f: 持续跟踪日志输出
.PHONY: logs
logs:
	docker compose logs -f

# 查看服务容器状态
.PHONY: ps
ps:
	docker compose ps

# 进入后端服务的容器
.PHONY: shell
shell:
	docker compose exec backend bash

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
