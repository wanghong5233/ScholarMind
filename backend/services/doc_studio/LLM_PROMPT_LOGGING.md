# LLM Prompt 完整日志功能

## 📋 功能说明

为了方便测试和调试，我们在后端 `LLMClient` 中添加了完整的日志输出功能，可以查看：
1. **发送给 LLM 的完整 Prompt**
2. **LLM 的响应结果**
3. **API 请求参数**

---

## 🔍 如何查看日志

### 方法 1：Docker Compose 实时日志

```bash
# 查看 doc_studio 服务的实时日志
docker compose logs doc_studio --tail=0 -f
```

### 方法 2：过滤特定日志

```bash
# 只查看 LLM Prompt 相关的日志
docker compose logs doc_studio --tail=0 -f | grep "LLM"
```

### 方法 3：保存日志到文件

```bash
# 保存到文件方便后续分析
docker compose logs doc_studio > doc_studio_logs.txt
```

---

## 📊 日志输出示例

### 1️⃣ 完整的 LLM Prompt

当 Agent 调用 LLM 时，会输出：

```
================================================================================
📤 完整的 LLM Prompt (发送给大模型)
================================================================================
你是一个智能 LaTeX 编辑助手 Agent（类似 Cursor），能够：
- 理解用户的各类需求（编辑、查询、建议等）
- 自主决定是否需要检索文献、编辑文件
- 灵活组合多个工具完成复杂任务
- 给出清晰的回复和操作总结

## 用户选中片段的处理

当用户在编辑器中选中了一个或多个文本片段时：
- Observation 会显示所有片段的完整内容，格式为：`@selectionX (文件名, 位置): 完整文本`
- 用户的指令（User Intent）中会用 `【片段1】`、`【片段2】` 等自然语言引用这些片段
- 你应该理解这些引用对应 Observation 中的 `@selection1`、`@selection2` 等片段
- 例如：用户说"请优化【片段1】"，你应该查看 Observation 中 `@selection1` 的完整内容
- 如果需要修改选中的内容，优先使用 `rewrite_selection_tool`（会自动使用 selection.start/end）

## 工作原则
[... 完整的系统提示 ...]

## 执行历史
[... 如果有历史，会显示最近 5 步 ...]

## 可用工具
[... 所有可用工具的详细描述 ...]

## 当前观察

User Intent: 【片段1】检查一下这段内容是否符合论文规范？用语是否专业？

Workspace ID: workspace-123

Target File: paper2_wh.tex

用户选中了 1 个片段：

@selection1 (paper2_wh.tex, 位置1234:1334, 264字符):
```
Although all UAVs in the swarm can pre-store the full parameters of a complex multi-task learning (MTL) model for scheduling flexibility, executing the entire model on a single UAV remains highly inefficient or even infeasible under realistic mission settings.
```

## 你的任务

根据当前观察和执行历史，选择最合适的**下一步**工具操作。
[... 任务说明 ...]

================================================================================
可用工具数: 12
历史步骤数: 0
Temperature: 0.3
================================================================================
```

---

### 2️⃣ API 请求参数

```
🔄 调用 LLM API: model=qwen-plus, temp=0.3, tools_count=12
```

---

### 3️⃣ LLM 响应结果

```
================================================================================
📥 LLM 响应结果
================================================================================
Response: {
  "content": "我将分析用户选中的段落，检查其学术规范性和专业用语。",
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "analyze_context_tool",
        "arguments": "{\"text\": \"Although all UAVs in the swarm...\"}"
      }
    }
  ]
}
================================================================================
```

---

## 🎯 日志级别说明

我们使用了三种日志级别：

| 级别 | 用途 | 示例 |
|------|------|------|
| `INFO` | 重要信息（完整 Prompt、响应结果） | 📤 完整的 LLM Prompt |
| `DEBUG` | 调试信息（API 请求参数） | 🔄 调用 LLM API |
| `ERROR` | 错误信息 | Error calling LLM |

---

## 🛠️ 配置日志级别

### 临时修改（当前会话）

如果你想看到更详细的 DEBUG 日志，可以在 `backend/services/doc_studio/main.py` 中修改：

```python
# 原来是 INFO
logging.basicConfig(level=logging.INFO)

# 改为 DEBUG（会输出更多细节）
logging.basicConfig(level=logging.DEBUG)
```

### 永久配置（推荐）

修改 `backend/services/doc_studio/.env` 文件（如果有），或在 `config.py` 中设置：

```python
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")  # 可以改为 "DEBUG"
```

---

## 📝 实际使用示例

### 场景：测试 Prompt 替换是否正确

1. **启动日志监控**：
   ```bash
   docker compose logs doc_studio --tail=0 -f
   ```

2. **在前端操作**：
   - 选中一段文本
   - 输入：`@selection1检查这段是否规范`
   - 点击发送

3. **观察日志输出**：
   ```
   📤 完整的 LLM Prompt (发送给大模型)
   ...
   User Intent: 【片段1】检查这段是否规范
   ...
   @selection1 (paper2_wh.tex, 位置1234:1334, 264字符):
   ```
   
   ✅ **验证点**：
   - User Intent 中应该是 `【片段1】`（已替换）
   - Observation 中应该显示 `@selection1` 及其完整内容

4. **查看 LLM 响应**：
   ```
   📥 LLM 响应结果
   ...
   "tool_name": "analyze_context_tool"
   ...
   ```
   
   ✅ **验证点**：LLM 是否正确理解了用户意图并调用了合适的工具

---

## 🔧 调试技巧

### 1. 查找特定 trace_id 的日志

```bash
docker compose logs doc_studio | grep "trace-1234567890"
```

### 2. 只看 Prompt 和响应（过滤其他噪音）

```bash
docker compose logs doc_studio --tail=0 -f | grep -E "(📤|📥|User Intent)"
```

### 3. 统计工具调用次数

```bash
docker compose logs doc_studio | grep "tool_name" | sort | uniq -c
```

### 4. 查看最近 10 次 Prompt

```bash
docker compose logs doc_studio | grep "📤 完整的 LLM Prompt" | tail -10
```

---

## 📊 日志输出时机

| 阶段 | 触发时机 | 日志内容 |
|------|---------|----------|
| **Prompt 构建** | `reason_and_act` 调用时 | 完整的 ReAct Prompt |
| **API 调用** | 发送给 LLM 前 | 请求参数（model, temp, tools_count） |
| **响应解析** | 收到 LLM 响应后 | 完整的响应 JSON |
| **工具执行** | 每次工具调用 | 工具名称、参数、结果 |

---

## ⚠️ 注意事项

1. **日志量较大**：完整 Prompt 可能包含几千字符，建议只在测试时开启详细日志
2. **敏感信息**：日志中可能包含用户文档内容，生产环境需谨慎
3. **性能影响**：大量日志输出会略微影响性能，建议生产环境使用 INFO 或 WARNING 级别

---

## 🎓 最佳实践

### 开发调试时

```bash
# 实时查看详细日志
docker compose logs doc_studio --tail=0 -f
```

### 问题排查时

```bash
# 保存完整日志供后续分析
docker compose logs doc_studio > debug_$(date +%Y%m%d_%H%M%S).log
```

### 性能测试时

```bash
# 只记录关键信息，减少 I/O
docker compose logs doc_studio --tail=100 | grep -E "(ERROR|WARNING)"
```

---

## 🚀 未来改进

考虑添加：
1. **结构化日志**（JSON 格式）方便机器解析
2. **日志分级采样**（只记录 10% 的详细 Prompt）
3. **敏感信息脱敏**（自动隐藏用户文档内容）
4. **日志聚合**（集成 ELK/Loki 等日志平台）

---

## 📚 相关文件

- `backend/services/doc_studio/service/llm_client.py`：LLM 客户端（添加了日志）
- `backend/services/doc_studio/service/agent_service.py`：Agent 服务（构建 Observation）
- `frontend/src/pages/doc-studio/index.tsx`：前端（Prompt 替换逻辑）

---

现在你可以**实时查看完整的 LLM Prompt 和响应**，方便调试和优化！🎉

