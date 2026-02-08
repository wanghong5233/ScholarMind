# Doc Studio 快速启动指南

## 🎉 改进完成！

Doc Studio 已全面升级，现在类似 Cursor 的智能 Agent：

### ✅ 核心改进

| 改进项 | 改进前 | 改进后 |
|-------|--------|--------|
| **回复能力** | ❌ 只能编辑 | ✅ 可问答/可编辑 |
| **编辑精度** | ❌ 行号/标记 | ✅ 上下文定位 |
| **回复质量** | ❌ "Task completed" | ✅ 完整总结 |
| **Diff 预览** | ✅ 已实现 | ✅ 已验证 |
| **工具总数** | 9 个 | **10 个** |

### 🆕 新增功能

1. **ReplyToUserTool** - 回复用户的核心工具
   - 总结操作
   - 报告修改
   - 解答问题
   - 提供建议

2. **改进的 InsertTextTool** - 精确的上下文编辑
   - 基于上下文匹配（类似 Cursor）
   - 唯一性验证
   - 支持 before/after 模式

3. **增强的 Prompt** - 清晰的执行引导
   - 明确任务完成标准
   - 强制要求回复用户
   - 提供执行流程示例

## 🚀 立即测试

### 1. 确认服务状态

```bash
cd backend

# 查看服务状态
docker compose ps

# 应该看到 doc_studio 服务在运行
```

### 2. 查看日志（可选）

```bash
# 在新终端窗口运行
make logs-agent

# 期望输出
# INFO:     Application startup complete.
# INFO:     Initialized tool registry with 10 tools
```

### 3. 打开前端

```
http://localhost:3000/doc-studio
```

### 4. 快速测试

#### 测试 1：纯问答（30秒）

在聊天框输入：
```
什么是图神经网络？
```

**期望结果**：
- ✅ Agent 返回详细解释
- ✅ 无 Diff 预览（没有编辑文件）

---

#### 测试 2：编辑文档（1分钟）

在聊天框输入：
```
这里帮我写一段关于图神经网络的摘要
```

**期望结果**：
- ✅ Agent 检索文献 → 插入摘要 → 总结操作
- ✅ Diff 预览自动弹出
- ✅ 可以接受/拒绝修改

---

## 📊 完整功能列表

### 工具生态系统（10个工具）

#### 1. 分析类（2个）
- `analyze_context_tool` - 理解用户意图
- `analyze_document_tool` - 分析文档结构

#### 2. 检索类（2个）
- `search_papers_tool` - 单查询检索
- `batch_search_papers_tool` - 批量检索

#### 3. 编辑类（3个）
- `insert_text_tool` - **通用文本插入**（上下文定位）
- `insert_citation_tool` - 插入引用
- `update_bibliography_tool` - 更新参考文献

#### 4. 验证类（3个）
- `compile_latex_tool` - 编译验证
- `check_citation_consistency_tool` - 引用一致性
- `check_bibliography_tool` - 参考文献检查

#### 5. 响应类（1个）
- `reply_to_user_tool` - **回复用户**（新增）

---

## 🎯 使用场景

### 场景 1：纯问答
**用户**：`"什么是强化学习？"`
**Agent**：检索 → 回复
**结果**：完整的解释，无文件修改

### 场景 2：简单编辑
**用户**：`"帮我写一段关于 GNN 的摘要"`
**Agent**：分析 → 检索 → 编辑 → 回复
**结果**：插入摘要，Diff 预览

### 场景 3：复杂任务
**用户**：`"添加 Related Work 章节并引用综述"`
**Agent**：分析 → 批量检索 → 插入章节 → 更新 bib → 编译 → 回复
**结果**：2 个文件修改，完整总结

### 场景 4：仅建议
**用户**：`"LaTeX 如何添加超链接？"`
**Agent**：直接回答
**结果**：使用建议，无检索/编辑

---

## 🛡️ 安全机制

### 编辑安全
- ✅ **上下文唯一性验证**：防止误改错误位置
- ✅ **Diff 预览**：修改前可审查
- ✅ **逐个接受/拒绝**：完全控制

### 执行安全
- ✅ **最大迭代限制**：防止死循环
- ✅ **超时保护**：30秒自动终止
- ✅ **错误恢复**：工具调用失败时自动处理

---

## 📁 关键文件

### 核心逻辑
```
backend/services/doc_studio/
├── service/
│   ├── agent_service.py       # ReAct 循环，自动 FINISH
│   ├── llm_client.py          # 改进的 Prompt
│   ├── tool_registry.py       # 工具注册（10个）
│   └── tools/
│       ├── response_tools.py  # 🆕 回复用户
│       ├── editing_tools.py   # 🔧 上下文编辑
│       ├── retrieval_tools.py
│       ├── analysis_tools.py
│       └── validation_tools.py
├── router/
│   └── agent_rt.py            # API 端点
└── main.py                     # FastAPI 应用
```

### 文档
```
backend/services/doc_studio/
├── README.md                       # 📖 服务入口说明
├── ARCHITECTURE_OVERVIEW_12_7.md  # 🏗️ 当前架构概览
├── COMPREHENSIVE_UPGRADE_PLAN.md  # 🚀 完整升级方案
├── QUICK_START.md                 # ⚡ 本文件（快速启动）
└── docs/
    ├── DOCUMENTATION_INDEX.md     # 📚 完整文档索引
    ├── future/                    # 未来规划
    └── archived/                  # 历史归档
```

**详细文档请查看**：[docs/DOCUMENTATION_INDEX.md](docs/DOCUMENTATION_INDEX.md)

---

## 🔍 调试指南

### 查看日志
```bash
# 实时日志（推荐）
make logs-agent

# 最近 50 行
docker compose logs doc_studio --tail=50
```

### 进入容器
```bash
docker compose exec doc_studio bash

# 检查工具注册
python -c "
from service.tool_registry import create_tool_registry
registry = create_tool_registry()
print(f'Total: {len(registry.list_tools())} tools')
print(registry.list_tools())
"
```

### 测试 LLM 连接
```bash
docker compose exec doc_studio python -c "
import asyncio
from service.llm_client import LLMClient

async def test():
    client = LLMClient()
    result = await client.generate('测试连接', tools=None)
    print(result)

asyncio.run(test())
"
```

---

## ❓ 常见问题

### Q1: Agent 不回复？
**A**: 检查日志，确认 `reply_to_user_tool` 被调用。如果没有，检查 LLM API 是否正常。

### Q2: 编辑位置错误？
**A**: 检查日志中的 `search_context`，确认上下文是否唯一。如果不唯一，工具会返回错误。

### Q3: Diff 预览不显示？
**A**: 检查 API 响应中的 `file_diffs` 字段。如果为空，检查 `modified_files` 是否被标记。

### Q4: 性能慢？
**A**: 正常响应时间 10-30秒。如果超过 30秒，检查：
   - LLM API 响应时间
   - RAG 检索速度
   - 知识库大小

---

## 📞 技术支持

### 日志位置
- **Agent 日志**: `docker compose logs doc_studio`
- **主 API 日志**: `docker compose logs scholarmind_api`
- **RAG 日志**: `docker compose logs scholarmind_api`

### 重启服务
```bash
# 方法 1：热重启（推荐，不重新构建）
docker compose restart doc_studio

# 方法 2：强制重启（确保代码更新）
make force-restart-agent

# 方法 3：重新构建（如果依赖变化）
make rebuild-agent
```

---

## ✅ 检查清单

测试前：
- [ ] 所有服务都在运行 (`docker compose ps`)
- [ ] 知识库已准备（至少 1 个，包含文档）
- [ ] 前端可访问 (`http://localhost:3000`)
- [ ] 日志窗口已打开 (`make logs-agent`)

测试中：
- [ ] 用例 1：纯问答 - 通过
- [ ] 用例 2：编辑文档 - 通过
- [ ] Diff 预览正常显示
- [ ] 接受/拒绝按钮正常工作

测试后：
- [ ] 所有文件修改都能看到 Diff
- [ ] Agent 的回复清晰完整
- [ ] 没有编辑错误的位置
- [ ] 性能可接受（< 30秒）

---

## 🎉 准备就绪！

**当前状态**：
- ✅ 代码已更新
- ✅ 热加载已生效
- ✅ Linter 检查通过
- ✅ 所有工具已注册

**立即开始测试** 🚀

```bash
# 第 1 步：查看日志
make logs-agent

# 第 2 步：打开前端
# http://localhost:3000/doc-studio

# 第 3 步：测试
# 输入："什么是图神经网络？"
```

**Good luck!** 🍀

