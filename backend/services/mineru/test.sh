#!/bin/bash
# MinerU 服务测试脚本

set -e

echo "========================================="
echo "MinerU 服务测试"
echo "========================================="

# 检查容器状态
echo ""
echo "1. 检查容器状态..."
docker ps | grep scholarmind_mineru || echo "警告: MinerU 容器未运行！"

# 健康检查
echo ""
echo "2. 健康检查..."
curl -f http://localhost:8001/health && echo " ✓ 健康检查通过" || echo " ✗ 健康检查失败"

# 测试解析（需要提供 PDF 文件）
echo ""
echo "3. 测试 PDF 解析..."
if [ -f "test.pdf" ]; then
    echo "使用 test.pdf 进行测试..."
    curl -X POST http://localhost:8001/parse \
      -F "file=@test.pdf" \
      -o test_result.json
    
    if [ -f "test_result.json" ]; then
        echo " ✓ 解析成功，结果已保存到 test_result.json"
        echo "   预览前 200 字符:"
        head -c 200 test_result.json
        echo ""
    else
        echo " ✗ 解析失败"
    fi
else
    echo "跳过解析测试（未找到 test.pdf）"
    echo "提示: 将测试 PDF 文件命名为 test.pdf 并放在当前目录"
fi

# 查看最近日志
echo ""
echo "4. 最近日志（最后 20 行）..."
docker logs --tail 20 scholarmind_mineru

echo ""
echo "========================================="
echo "测试完成"
echo "========================================="

