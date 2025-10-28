# MinerU 解析服务

## 概述
MinerU 是 ScholarMind 项目的核心 PDF 解析引擎，作为独立的 Docker 服务运行，通过 HTTP API 为主应用提供学术论文的深度解析能力。

## 功能特性
- **表格结构化**: 将 PDF 表格还原为 JSON 结构（行列关系、单元格内容）
- **公式提取**: 将数学公式转换为 LaTeX 格式
- **图表识别**: 检测图表区域并提取 caption
- **版面分析**: 精准识别标题层级、段落、列表、脚注等元素
- **位置信息**: 输出每个元素的 bbox 坐标，支持精确溯源

## 架构说明
```
┌─────────────────┐
│ scholarmind_api │  ──HTTP POST──>  ┌────────────────┐
│   (FastAPI)     │                   │ scholarmind_   │
│                 │  <──JSON Result── │    mineru      │
└─────────────────┘                   │  (FastAPI)     │
                                      └────────────────┘
                                             │
                                             ▼
                                      ┌────────────────┐
                                      │  MinerU Core   │
                                      │  (Python Lib)  │
                                      └────────────────┘
```

## API 接口

### POST /parse
解析 PDF 文件并返回结构化 JSON。

**请求**:
- Content-Type: `multipart/form-data`
- Body: `file` 字段上传 PDF 文件

**响应**:
```json
{
  "pages": [
    {
      "page": 1,
      "elements": [
        {
          "type": "paragraph",
          "text": "论文正文内容...",
          "bbox": [x0, y0, x1, y1],
          "confidence": 0.95
        },
        {
          "type": "table",
          "text": "表格的 Markdown 表示",
          "bbox": [x0, y0, x1, y1],
          "table": {
            "rows": [...],
            "cols": [...]
          },
          "confidence": 0.92
        },
        {
          "type": "equation",
          "text": "E = mc^2",
          "latex": "E = mc^2",
          "bbox": [x0, y0, x1, y1],
          "confidence": 0.89
        },
        {
          "type": "figure",
          "caption": "图1: 实验结果对比",
          "bbox": [x0, y0, x1, y1]
        }
      ]
    }
  ]
}
```

### GET /health
健康检查接口。

**响应**:
```json
{
  "status": "ok",
  "service": "mineru"
}
```

## 部署说明

### 1. 构建并启动服务
```bash
cd backend
docker-compose up -d mineru
```

### 2. 验证服务状态
```bash
# 检查容器状态
docker ps | grep scholarmind_mineru

# 健康检查
curl http://localhost:8001/health

# 测试解析（需要准备一个 test.pdf）
curl -X POST http://localhost:8001/parse \
  -F "file=@test.pdf" \
  -o result.json
```

### 3. 查看日志
```bash
docker logs -f scholarmind_mineru
```

## 配置说明

### 环境变量（在 docker-compose.yml 中配置）
- `PYTHONUNBUFFERED=1`: 禁用 Python 输出缓冲，实时查看日志

### 主应用配置（backend/.env）
```bash
# MinerU 集成模式（auto/http/cli）
SM_MINERU_MODE=http

# MinerU HTTP 服务地址（Docker 网络内部地址）
SM_MINERU_ENDPOINT=http://mineru:8001

# 解析超时时间（秒）
SM_MINERU_TIMEOUT_SECS=120

# 兜底路径最大页数
SM_MINERU_MAX_PAGES=30
```

## 故障排查

### 问题1: 容器无法启动
**症状**: `docker-compose up` 报错或容器反复重启

**排查步骤**:
1. 检查 MinerU 安装是否成功
   ```bash
   docker-compose run --rm mineru python -c "import mineru; print(mineru.__version__)"
   ```
2. 查看详细日志
   ```bash
   docker logs scholarmind_mineru
   ```

**解决方案**:
- 如果 MinerU 尚未发布到 PyPI，修改 `Dockerfile` 中的安装命令为实际的 GitHub 仓库地址
- 如果需要额外的系统依赖，在 `Dockerfile` 的 `apt-get install` 部分添加

### 问题2: 解析请求超时
**症状**: 主应用日志显示 `MinerUParser.http.fail err=timeout`

**排查步骤**:
1. 检查 PDF 文件大小和页数
2. 查看 MinerU 服务日志是否有处理记录
3. 验证网络连通性
   ```bash
   docker exec scholarmind_api curl http://mineru:8001/health
   ```

**解决方案**:
- 增加 `SM_MINERU_TIMEOUT_SECS` 配置值
- 对大文件启用分页处理（后续优化）

### 问题3: 解析结果为空
**症状**: 返回 `{"pages": []}`

**排查步骤**:
1. 手动测试 MinerU CLI
   ```bash
   docker exec scholarmind_mineru mineru --input /path/to/test.pdf --output /tmp/out.json
   ```
2. 检查 PDF 是否为扫描件或加密文件

**解决方案**:
- 如果是扫描件，需要配置 OCR 引擎（见下节）
- 如果 MinerU 不支持该 PDF，系统会自动降级到 PyMuPDF 兜底

## 进阶配置

### 集成 OCR 引擎（针对扫描版 PDF）
如需处理扫描版 PDF，需在 `Dockerfile` 中添加 OCR 依赖：

```dockerfile
# 安装 Tesseract OCR
RUN apt-get update && apt-get install -y tesseract-ocr tesseract-ocr-chi-sim

# 或集成 PaddleOCR
RUN pip install paddlepaddle paddleocr
```

然后在 `mineru_service.py` 中配置 MinerU 使用 OCR。

### 性能优化
- **并发处理**: 调整 `uvicorn` 的 `--workers` 参数
- **GPU 加速**: 如果宿主机有 GPU，修改 `docker-compose.yml` 添加 GPU 支持
  ```yaml
  mineru:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
  ```

## 开发说明

### 本地开发模式
如需修改 MinerU 服务代码：
1. 编辑 `backend/services/mineru/mineru_service.py`
2. 重启容器
   ```bash
   docker-compose restart mineru
   ```

### 添加新的解析能力
在 `mineru_service.py` 的 `parse_pdf` 函数中扩展逻辑，例如：
- 添加章节结构识别
- 提取参考文献列表
- 识别作者和机构信息

输出格式保持与现有 JSON schema 兼容，主应用的 `MinerUParser._parse_mineru_json` 会自动适配。

## 相关文档
- [MinerU 官方文档](https://github.com/opendatalab/MinerU)
- [ScholarMind 解析器架构](../../readme/readme.md)
- [多模态解析流水线设计](../../简历材料.md)

