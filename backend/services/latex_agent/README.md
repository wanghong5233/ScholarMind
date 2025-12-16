# LaTeX Agent Service

LaTeX 编辑 Agent 微服务，提供智能引用管理、文档编辑等功能。

## 功能特性

- **自主规划**：Agent 能够自主分解任务、制定执行计划
- **多步骤推理**：执行需要多步骤的复杂任务
- **工具调用**：调用多个工具协调完成复杂任务
- **自我反思**：反思执行结果，发现错误并自动修复
- **上下文理解**：理解整个项目的上下文，做出全局最优决策

## 服务架构

```
latex_agent/
├── main.py                 # 服务入口
├── router/                 # API 路由
│   └── agent_rt.py
├── service/                # 核心服务
│   ├── agent_service.py   # Agent 核心逻辑
│   └── tools/             # 工具集
│       ├── base_tool.py
│       ├── analysis_tools.py
│       ├── retrieval_tools.py
│       ├── editing_tools.py
│       └── validation_tools.py
├── schemas/               # API Schema
├── models/                # 数据模型
├── requirements.txt       # Python 依赖
└── Dockerfile            # Docker 配置
```

## API 端点

### 工作区管理
- `POST /workspaces` - 创建工作区
- `GET /workspaces/{workspace_id}` - 获取工作区信息
- `PUT /workspaces/{workspace_id}` - 更新工作区配置
- `DELETE /workspaces/{workspace_id}` - 删除工作区

### Agent 编辑操作
- `POST /workspaces/{workspace_id}/edit` - 编辑文档（核心 API）
- `POST /workspaces/{workspace_id}/add-citation` - 添加引用
- `POST /workspaces/{workspace_id}/batch-add-citations` - 批量添加引用
- `POST /workspaces/{workspace_id}/check-citations` - 检查引用
- `POST /workspaces/{workspace_id}/optimize-citations` - 优化引用

### 编译操作
- `POST /workspaces/{workspace_id}/compile` - 编译 LaTeX
- `GET /workspaces/{workspace_id}/compile-status` - 获取编译状态
- `GET /workspaces/{workspace_id}/pdf` - 获取 PDF 预览

### 文件操作
- `GET /workspaces/{workspace_id}/files` - 获取文件列表
- `GET /workspaces/{workspace_id}/files/{file_path}` - 获取文件内容
- `PUT /workspaces/{workspace_id}/files/{file_path}` - 更新文件内容

## 环境变量

- `PORT`: 服务端口（默认 8003）
- `RAG_SERVICE_URL`: RAG 服务 URL（默认 http://scholarmind_api:8000）
- `DATABASE_URL`: 数据库连接 URL（可选）

## 运行方式

### 本地开发
```bash
cd backend/services/latex_agent
pip install -r requirements.txt
python main.py
```

### Docker
```bash
docker build -t latex-agent .
docker run -p 8003:8003 latex-agent
```

### Docker Compose
在 `backend/docker-compose.yml` 中添加服务配置。

## 开发状态

核心功能已完成，正在按照 `COMPREHENSIVE_UPGRADE_PLAN.md` 进行系统优化。

**当前完成**：
- ✅ ReAct Agent 核心循环（Planner → Executor → Reflector）
- ✅ 意图识别 + 动态计划构建（配置驱动）
- ✅ 工具系统（10+ 工具：分析/检索/编辑/验证）
- ✅ Prometheus 监控 + Trace ID 全链路追踪
- ✅ 安全防护（Prompt Injection + 速率限制）
- ✅ 工作区缓存（LRU+TTL）
- ✅ 用户反馈闭环（点赞/点踩）
- ✅ 增量 Diff 生成

**正在优化**：
- 🟡 LLM 调用缓存（性能优化）
- 🟡 Grafana 监控面板（可观测性）
- 🟡 端到端集成测试（测试覆盖）

**未来规划**：
- ⏸️ RL 后训练优化（详见 `docs/future/RL_TRAINING_DESIGN.md`）

## 📚 文档导航

### 核心文档（必读）

| 文档 | 说明 | 阅读时间 | 何时阅读 |
|------|------|----------|----------|
| **README.md** (本文档) | 服务入口说明 | 5分钟 | 快速了解功能和运行方式 |
| **ARCHITECTURE_OVERVIEW_12_7.md** | 当前架构概览 | 30分钟 | 理解系统设计、调试问题 |
| **COMPREHENSIVE_UPGRADE_PLAN.md** | 完整升级方案 | 60分钟 | 了解优化方向、准备面试 |
| **docs/DOCUMENTATION_INDEX.md** | 📖 完整文档索引 | - | 查找所有文档、推荐阅读路线 |

### 其他文档

- **docs/future/RL_TRAINING_DESIGN.md** - RL后训练技术方案（未来规划，面试加分）
- **docs/archived/** - 历史文档归档（了解设计演变）

**推荐阅读路线**：
- 🚀 **快速上手**：README → ARCHITECTURE_OVERVIEW（第1-2章）
- 💻 **开发人员**：README → ARCHITECTURE_OVERVIEW → COMPREHENSIVE_UPGRADE_PLAN
- 🎯 **面试准备**：README → ARCHITECTURE_OVERVIEW → COMPREHENSIVE_UPGRADE_PLAN → RL_TRAINING_DESIGN

**详细阅读指南请查看**：[docs/DOCUMENTATION_INDEX.md](docs/DOCUMENTATION_INDEX.md)

