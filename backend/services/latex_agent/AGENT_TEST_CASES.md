# LaTeX Agent 服务前后端联调测试案例

> 目标：指导 ScholarMind LaTeX Agent 微服务与前端 `latex-editor` 页面进行端到端联调，确保工作区、文件、Agent、编译、训练数据五大能力稳定可用，并覆盖关键异常场景。

---

## 1. 测试范围与策略

- **涵盖模块**
  1. 工作区 & 文件系统（Workspace/File APIs + UI 双向校验）
  2. Agent 会话 API（`/edit`、`/add-citation` …）及前端聊天面板
  3. 编译闭环（`compile_latex_tool` → `/compile` → PDF 下载）
  4. RL 训练数据面板（`/training/*`）
  5. 权限/稳定性（`X-User-Id`、路径逃逸、防止 RAG/LLM 失败放大）
- **不在本轮覆盖**：MinerU/LangChain 解析、后续 Phase 2/3 高级能力（多模态、Agent RL）。
- **方法**：每个用例均给出前端操作 + API 调试（cURL/Postman） + 磁盘/日志验证，优先人工串场确保联调顺滑。

---

## 2. 环境准备

### 2.1 基础服务

| 组件 | 版本/说明 | 必要操作 |
| --- | --- | --- |
| 遵循 `backend/README.md` 的 FastAPI 主服务 | 端口 8000 | 需要提供 RAG Service `/api/sessions/...` 及数据库 |
| LaTeX Agent Service | `python backend/services/latex_agent/main.py` 或 docker-compose | 配置 `.env`:<br>`WORKSPACES_ROOT=<绝对路径>`<br>`RAG_SERVICE_URL=http://127.0.0.1:8000`<br>`LLM_API_KEY=<Dashscope/OpenAI key>`<br>`LLM_MODEL=qwen-plus` |
| 前端 `latex-editor` 页面 | `npm run dev` | `.env` 设置 `VITE_LATEX_AGENT_BASE=http://127.0.0.1:8003/api`、`VITE_LATEX_DEFAULT_USER_ID=1` |
| 可用的 LaTeX 工具链 | `pdflatex` + `bibtex` | Windows 需确保 PATH 包含 MiKTeX/TexLive |
| 可选：PostgreSQL | 若需验证 RL 埋点 | 填写 `DATABASE_URL=postgresql://...` 并运行 `alembic` |

### 2.2 验证样本

1. 创建测试用户 `X-User-Id: 1`，根目录 `${WORKSPACES_ROOT}/1`。
2. 准备 `demo.tex` 包含少量 section 与 `\cite{}`；另拷贝一篇 bib（可用 `testpdf` 中论文元数据）。
3. 若需多用户隔离，额外准备 `X-User-Id: 2` 并创建不同 workspace。

### 2.3 通用校验项

- 每次请求记录 `X-Request-ID`，后端日志应打印 `workspace=<id> user=<id>`。
- 文件操作后核对磁盘实际内容。
- 前端 UI 需同步刷新（文件树/聊天面板/编译 Tab）。

---

## 3. 用例矩阵概览

| ID | 模块 | 场景 | 主接口/页面 |
| --- | --- | --- | --- |
| WS-01/02/03/04 | 工作区 CRUD | 创建/列出/更新/删除 | `/workspaces` + 前端左侧工作区选择 |
| SEC-01/02 | 权限 | 无 Header / 多用户隔离 | 全量 API |
| FL-01~05 | 文件操作 | 读写/新建/上传/路径防逃逸 | `/files` 系列 + Monaco 编辑器 |
| CP-01/02/03 | 编译 | 成功/缺主文件/缺编译器 | `/compile` + 右侧“编译结果” Tab |
| AG-01~08 | Agent | 基础编辑/引用/批处理/异常 | `/edit`、`/add-citation`、`/check-citations`… + 聊天侧栏 |
| RAG-01 | 外部依赖 | RAG 服务失败 | `SearchPapersTool` 调用日志 |
| RL-01~03 | 训练埋点 | Episode 查询、反馈、指标 | `/training/*` |
| LOG-01 | 观测性 | `X-Request-ID`/日志结构 | 全量请求 |

---

## 4. 详细测试案例

### 4.1 工作区与权限

#### WS-01 创建工作区（UI + API）
- **目的**：验证 `/workspaces` 创建流程与前端弹窗。
- **前置**：`X-User-Id:1`，Agent 服务运行。
- **步骤**：
  1. 前端点击“新建工作区”，输入 `latex-demo`.
  2. 观察 Network：`POST /api/workspaces` body 包含 `{"name":"latex-demo"}`。
  3. 登陆服务器，检查 `${WORKSPACES_ROOT}/1/<new_id>` 下生成 `main.tex`、`sections/`、`figures/`、`.workspace.json`。
- **期望**：
  - 返回 200，`workspace_id` 唯一。
  - `.workspace.json` 含 `main_file/bibliography_file`。
  - 前端 Select 自动定位到新工作区并触发文件树加载。

#### WS-02 列表 & 自动打开
- **目的**：`GET /workspaces` + 前端自动选择逻辑。
- **步骤**：刷新页面 → 观察请求 & UI。
- **期望**：接口返回至少 1 个条目；前端 `latexAgentState.workspaceId` 与 API 返回一致，文件树显示 `main.tex` 内容。

#### WS-03 更新配置
- **步骤**：
  1. `PUT /workspaces/{id}` 设置 `{"config":{"main_file":"sections/intro.tex","compiler":"xelatex"}}`.
  2. 前端 `fetchWorkspaceFiles` 后编译按钮应使用新主文件。
  3. 检查 `.workspace.json`。
- **期望**：响应携带最新 config；`compileWorkspace` 请求体默认 main_file=更新值。

#### WS-04 删除工作区
- **步骤**：点击“删除” → 触发 `DELETE /workspaces/{id}` → 刷新列表。
- **期望**：目录被彻底移除；前端自动切回其它工作区；再次访问返回 404。

#### SEC-01 缺少 `X-User-Id`
- **步骤**：在 Postman 手动去掉 header 调用任一 API。
- **期望**：401 + `Missing X-User-Id header`。

#### SEC-02 多用户隔离
- **步骤**：
  1. 用 `X-User-Id:1` 创建 workspace A。
  2. 用 `X-User-Id:2` 请求 `/workspaces`，确保看不到 A。
  3. 尝试跨用户访问 `/workspaces/{A}` → 404。
- **期望**：不同根目录互不影响。

---

### 4.2 文件系统

#### FL-01 打开/读取文件
- **步骤**：前端点击 `main.tex`，观察 `GET /files/{path}` 结果与编辑器内容。
- **期望**：编码信息正确；Monaco `dirty=false`。

#### FL-02 保存文件
- **步骤**：
  1. 在编辑器中修改文本。
  2. `Ctrl+S` → 触发 `PUT /files/{path}`。
  3. 查看磁盘内容与 `latexAgentState.files[path].dirty`。
- **期望**：返回 `modified_at` 变更；文件实际更新；UI 去掉“未保存”标记。

#### FL-03 新建文件/目录
- **步骤**：通过 UI “新建文件”输入 `sections/method.tex`。
- **期望**：接口返回 200；文件树新增节点；可立即打开编辑。

#### FL-04 上传文件 & 下载
- **步骤**：上传 `figures/diagram.pdf` → `buildDownloadUrl` 打开文件。
- **期望**：`POST /files/upload` 返回路径，`/download?file_path=...` 可下载。

#### FL-05 路径逃逸防护
- **步骤**：`POST /files` body `{"path":"../evil.tex","type":"file"}`。
- **期望**：400 + `Invalid file path`。

---

### 4.3 编译流程

#### CP-01 正常编译
- **前置**：确保 `pdflatex` 可执行，`main.tex` 可编译。
- **步骤**：
  1. 前端点“编译” → `POST /compile`。
  2. 查看返回 `success=true`、`data.pdf_path`。
  3. 点击“下载 PDF”。
- **期望**：输出 PDF 存在；右侧“编译结果”显示日志与警告列表。

#### CP-02 主文件缺失
- **步骤**：将 `.workspace.json` 设置 `main_file="nope.tex"`，再次编译。
- **期望**：`success=false`，消息指向“主文件不存在”。

#### CP-03 无编译器
- **步骤**：暂时从 PATH 移除 `pdflatex` 或修改 config 指向假编译器。
- **期望**：HTTP 200 + `success=false` + `error="找不到编译器"`；前端 toast 显示失败。

---

### 4.4 Agent 能力

> 说明：若真实 LLM/RAG 不可用，可在 `.env` 中指向 mock server 或使用「提示极短 + 已有引用」的低成本场景，重点验证请求/响应数据结构和前端联动。

#### AG-01 基础编辑（/edit）
- **步骤**：
  1. 在聊天输入“在文档开头添加一句 TODO”。
  2. 观察 `POST /workspaces/{id}/edit` payload（含 `target_location`）与返回 `changes`。
  3. 编辑器应自动刷新受影响文件。
- **期望**：`execution_history` 包含 ACTION/RESULT；`changes` 包括 `file/position/type`；聊天面板展示 Agent 回复。

#### AG-02 添加引用（/add-citation）
- **步骤**：
  1. 选中一段文字 → 点击“引用选中文本” → 输入 prompt 或直接调用 API：`target_text`, `citation_style`.
  2. 检查 `main.tex` 是否插入 `\cite`。
- **期望**：`LaTeXEditResponse.changes` 指向正确行；`references.bib` 如需更新由 `UpdateBibliographyTool` 完成。

#### AG-03 批量引用（/batch-add-citations）
- **步骤**：`POST /batch-add-citations` with `{"target_sections":["Introduction","Method"],"citation_style":"\\citep{}"}`。
- **期望**：多条变更；execution history 中出现 `batch_search_papers_tool`。

#### AG-04 引用自检（/check-citations）
- **步骤**：故意混用 `\cite` & `\citet` → 调用 API。
- **期望**：返回 `issues` 列表包含不一致项；前端 toast/日志可展示。

#### AG-05 引用优化（/optimize-citations）
- **步骤**：准备重复引用 → 调用 API。
- **期望**：`optimizations` 描述调整；`changes` 指向修改后的文件。

#### AG-06 RAG 依赖失败
- **步骤**：停掉主 RAG 服务或将 `RAG_SERVICE_URL` 设为错误地址，再运行 `/edit`（需要检索的指令）。
- **期望**：工具返回失败并记录在 `execution_history`，前端提示“Agent execution failed: <error>”而不崩溃。

#### AG-07 LLM Key 缺失
- **步骤**：清空 `LLM_API_KEY` 环境变量重启服务，再调用 `/edit`。
- **期望**：接口 500 + message `LLM reasoning failed: LLM API key not configured`；前端 toast。

#### AG-08 训练数据收集
- **前置**：配置 `DATABASE_URL`，运行迁移。
- **步骤**：
  1. `POST /edit` with `"collect_training_data": true`.
  2. 调 `GET /training/episodes`，确认出现新记录，`actions/rewards` 填充。
- **期望**：`episode_id` 返回；DB `training_episodes` 新增记录。

---

### 4.5 RAG & 外部依赖

#### RAG-01 多查询执行
- **步骤**：调用 `/batch-add-citations` 或直接 `BatchSearchPapersTool`，同时抓包 `http://scholarmind_api:8000/api/sessions/dummy_session/retrieve`。
- **期望**：对每个 query 产生 HTTP 请求，超时/异常会在 `results[].error` 中体现。

---

### 4.6 RL 训练 API

#### RL-01 Episode 查询（DB on/off）
- **步骤**：
  1. DB 未配置时调用 `/training/episodes` → 503。
  2. DB 配置后再调 → 200 + 列表。

#### RL-02 用户反馈 & 专家评分
- **步骤**：选取 `episode_id` → `POST /training/episodes/{id}/feedback`（0-10），再调用 `expert-rating`。
- **期望**：成功返回；数据库字段更新。

#### RL-03 创建训练指标
- **步骤**：`POST /training/metrics` 提交一份指标对象 → `GET /training/metrics`.
- **期望**：`training_run_id` 返回；列表包含新增记录。

---

### 4.7 观测性

#### LOG-01 请求 ID 与结构化日志
- **步骤**：任意调用 → 查看响应头 `X-Request-ID` 与服务日志。
- **期望**：日志包含 request_id/workspace_id/user_id/工具名称；Error 时堆栈完整。

---

## 5. 故障排查建议

| 症状 | 排查步骤 |
| --- | --- |
| Agent API 超时 | 检查 LLM Key、RAG Service 端口、`AGENT_MAX_ITERATIONS` 是否过大 |
| 前端聊天没有刷新 | 查看 Redux store `latexAgentState.executionHistory` 是否更新；确认 `openFile` 重新加载受影响文件 |
| 编译报错但日志空 | 核对 `pdflatex` 路径；确保 `WORKSPACES_ROOT` 可写 |
| 训练数据未落库 | 查看 `alembic` 迁移是否执行、`DATABASE_URL` 凭据是否正确 |

---

## 6. 交付建议

- 按上述用例执行可形成 checklist，建议记录截图（UI + Postman + 日志）。
- 对发现的问题直接在 `AGENT_TEST_CASES.md` 附录中补充 “缺陷记录” 区块，方便追溯。
- 后续若引入多模态/Agent RL，可在此文档追加 Phase 2/3 专属用例，以保持统一格式。

> 完成本测试计划后，可证明 LaTeX Agent 服务具备**可演示、可调试、可观测**的端到端闭环，满足实习面试时的项目背书需求。

