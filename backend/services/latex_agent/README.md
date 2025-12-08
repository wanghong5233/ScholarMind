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

当前处于框架搭建阶段，核心功能正在开发中。

