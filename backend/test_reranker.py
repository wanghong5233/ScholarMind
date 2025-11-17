#!/usr/bin/env python3
"""测试精排服务"""
import requests
import json

# 测试数据
test_data = {
    "query": "什么是深度学习？",
    "chunks": [
        {
            "chunk_id": "1",
            "content": "深度学习是机器学习的一个分支，使用神经网络进行学习"
        },
        {
            "chunk_id": "2",
            "content": "神经网络由多个层组成，每层包含多个神经元"
        }
    ],
    "batch_size": 32
}

# 发送请求
try:
    print("发送精排请求...")
    response = requests.post(
        "http://localhost:8002/rerank",
        json=test_data,
        timeout=120  # 模型首次加载可能需要较长时间
    )
    response.raise_for_status()
    
    result = response.json()
    print("\n✅ 精排成功！")
    print(f"返回的chunks数量: {len(result.get('reranked_chunks', []))}")
    print(f"分数: {result.get('scores', [])}")
    print(f"\n排序后的chunks:")
    for i, chunk in enumerate(result.get('reranked_chunks', []), 1):
        print(f"  {i}. [{chunk.get('chunk_id')}] {chunk.get('content', '')[:50]}...")
        
except requests.exceptions.RequestException as e:
    print(f"❌ 请求失败: {e}")
    if hasattr(e, 'response') and e.response is not None:
        print(f"响应内容: {e.response.text}")

