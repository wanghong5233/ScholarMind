# BGE Reranker 精排服务

## 概述
BGE Reranker 是 ScholarMind 项目的精排服务，作为独立的 Docker 服务运行，通过 HTTP API 为主应用提供 Cross-Encoder 精排能力。

## 功能特性
- **Cross-Encoder 精排**: 使用 BGE-Reranker-Large 模型对检索结果进行深度语义交互打分
- **INT8 量化支持**: 支持 bitsandbytes INT8 量化，8GB 显存即可运行大模型
- **独立部署**: 与 API 服务解耦，可独立扩展和升级
- **GPU 加速**: 支持 NVIDIA GPU 加速推理
- **自动模型下载**: 首次启动时如果模型不存在，会自动从 HuggingFace 下载，无需手动操作

## 架构说明
```
┌─────────────────┐
│ scholarmind_api │  ──HTTP POST──>  ┌──────────────────┐
│   (FastAPI)     │                   │ scholarmind_     │
│                 │  <──JSON Result── │    reranker      │
└─────────────────┘                   │  (FastAPI)      │
                                      └──────────────────┘
                                             │
                                             ▼
                                      ┌──────────────────┐
                                      │ BGE-Reranker-    │
                                      │ Large (INT8)     │
                                      └──────────────────┘
```

## API 接口

### GET /health
健康检查接口

**响应**:
```json
{
  "status": "ok",
  "service": "reranker",
  "gpu_available": true,
  "gpu_count": 1,
  "gpu_name": "NVIDIA GeForce RTX 3060",
  "model_loaded": true,
  "device": "cuda"
}
```

### POST /rerank
对候选块列表进行重排序

**请求体**:
```json
{
  "query": "什么是深度学习？",
  "chunks": [
    {
      "chunk_id": "chunk_1",
      "content": "深度学习是机器学习的一个分支...",
      "metadata": {...}
    },
    {
      "chunk_id": "chunk_2",
      "content": "神经网络由多个层组成...",
      "metadata": {...}
    }
  ],
  "batch_size": 32
}
```

**响应**:
```json
{
  "reranked_chunks": [
    {
      "chunk_id": "chunk_1",
      "content": "深度学习是机器学习的一个分支...",
      "metadata": {...}
    },
    {
      "chunk_id": "chunk_2",
      "content": "神经网络由多个层组成...",
      "metadata": {...}
    }
  ],
  "scores": [0.95, 0.87]
}
```

## 部署

### GPU 版本（推荐）
```bash
cd backend
docker-compose build reranker
docker-compose up -d reranker
```

### CPU 版本（开发/测试）
修改 `docker-compose.yml` 中的 `dockerfile: Dockerfile.gpu` 为 `dockerfile: Dockerfile`

> ⚠️ **注意**：CPU 版本仅用于本地开发或功能验证，推理延迟较高且不支持 INT8 量化，不建议在正式环境使用。

## 环境变量

- `RERANKER_MODEL_PATH`: 模型路径（默认: `/models/bge-reranker-large`）
- `RERANKER_USE_QUANTIZATION`: 是否启用 INT8 量化（默认: `true`）
- `CUDA_VISIBLE_DEVICES`: 指定使用的 GPU（默认: `0`）

## 验证

```bash
# 检查健康状态
curl http://localhost:8002/health

# 测试精排功能
curl -X POST http://localhost:8002/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "query": "什么是深度学习？",
    "chunks": [
      {"chunk_id": "1", "content": "深度学习是机器学习的一个分支"},
      {"chunk_id": "2", "content": "神经网络由多个层组成"}
    ]
  }'
```

