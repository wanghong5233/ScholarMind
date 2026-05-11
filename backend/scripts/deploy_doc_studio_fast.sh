#!/usr/bin/env bash
# 用途：在 2C2G ECS 上对 doc_studio 服务做"代码级"增量更新，
#       跳过 texlive 重装，10 秒内完成 build + 重启。
#
# 背景与约束（见 docs/LOW_COST_CLOUD_DEPLOYMENT_MANUAL.md §10.10）：
#   - doc_studio 镜像含 ~5.1GB TeX Live 层，2C2G 上重 build 必然 swap thrash；
#   - BuildKit cache 与 image layer 是两套独立存储，cache 一旦丢失就回不来；
#   - 因此采用"已有 image 当 base + 只 COPY 代码"的 fast path。
#
# 适用场景：只改了 services/doc_studio/ 下的 .py 代码或 shared/ 共享代码。
#
# 不适用场景（需走完整 build，建议本地 build + scp，见 §10.11）：
#   - requirements.txt 变更
#   - services/doc_studio/Dockerfile 变更
#   - 系统级依赖（texlive、字体、apt 包列表）变更
#
# 使用：
#   cd /opt/apps/scholarmind/backend
#   bash scripts/deploy_doc_studio_fast.sh

set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.production"
BASE_TAG="backend-doc_studio:base"
TARGET_TAG="backend-doc_studio:latest"
FAST_DOCKERFILE="services/doc_studio/Dockerfile.fast"

if [[ ! -f "$COMPOSE_FILE" || ! -f "$ENV_FILE" || ! -f "$FAST_DOCKERFILE" ]]; then
  echo "[ERR] 必须在 backend/ 目录运行：缺少 $COMPOSE_FILE / $ENV_FILE / $FAST_DOCKERFILE" >&2
  exit 1
fi

echo "[1/5] 校验 base image 存在"
if ! docker image inspect "$BASE_TAG" >/dev/null 2>&1; then
  if docker image inspect "$TARGET_TAG" >/dev/null 2>&1; then
    echo "       base tag 不存在，从当前 latest 打 base tag"
    docker tag "$TARGET_TAG" "$BASE_TAG"
  else
    echo "[ERR] $BASE_TAG 和 $TARGET_TAG 都不存在；必须先有一份完整的 doc_studio 镜像。" >&2
    echo "      首次部署或重建：参考 §10.11 本地 build + scp 上传。" >&2
    exit 1
  fi
fi

echo "[2/5] 增量 build（只 COPY 代码，不装 texlive）"
docker build -f "$FAST_DOCKERFILE" -t "$TARGET_TAG" .

echo "[3/5] 启动/更新 doc_studio 容器"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --no-build --no-deps doc_studio

echo "[4/5] 等待 healthcheck（最长 90s）"
for i in $(seq 1 18); do
  status="$(docker inspect -f '{{.State.Health.Status}}' scholarmind_doc_studio 2>/dev/null || echo unknown)"
  if [[ "$status" == "healthy" ]]; then
    echo "       healthy at ${i}*5s"
    break
  fi
  sleep 5
done

echo "[5/5] 现状"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps doc_studio
echo
echo "      验证新代码已落地（grep count 应 >=1）："
docker exec scholarmind_doc_studio grep -c "_is_provider_connectivity_error" /app/service/llm_client.py || true
echo
echo "[OK] doc_studio 增量更新完成。"
