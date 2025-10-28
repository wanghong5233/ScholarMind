#!/bin/bash
# MinerU 服务构建脚本

set -e

echo "========================================="
echo "开始构建 MinerU 解析服务"
echo "========================================="

# 切换到 backend 目录
cd "$(dirname "$0")/../.."

echo "当前目录: $(pwd)"

# 构建 MinerU 服务镜像
echo "正在构建 scholarmind_mineru 镜像..."
docker-compose build mineru

echo ""
echo "========================================="
echo "构建完成！"
echo "========================================="
echo ""
echo "下一步操作："
echo "1. 启动服务: docker-compose up -d mineru"
echo "2. 查看日志: docker logs -f scholarmind_mineru"
echo "3. 健康检查: curl http://localhost:8001/health"
echo "4. 测试解析: curl -X POST http://localhost:8001/parse -F 'file=@test.pdf'"
echo ""

