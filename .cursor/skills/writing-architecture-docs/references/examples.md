# Architecture Doc Examples · 反例 → 正例

## 反例：结论先行 + 辩证过程 + 工件清单

```markdown
### B.1 结论先行
当前阶段，ScholarMind 放弃 Cloudflare Tunnel……

### B.2 决策背景
之前的本地 Docker + Tunnel 方案频繁抖动。落地阶段连续遇到三类问题：
1. 网络问题：QUIC 不稳定……
2. 运维问题：Windows 任务计划偶发……

### B.4 我们的辩证过程
1. 第一反应（错）：Tunnel 不稳定 → "再写一个守护脚本"
   - 反驳：守护脚本不等于云服务器架构……

### B.7 已删除/不再维护的工件
- backend/scripts/tunnel_watchdog.ps1
- CF_TUNNEL_TOKEN
- Cloudflare Tunnel 配置截图
```

问题：

- 结论先行的"当前阶段……"是 TL;DR 元结构，纯污染
- 决策背景 / 辩证过程是过去式叙事，不是架构事实
- 工件清单是 git log 的职责

## 正例：现状陈述 + 维度表

```markdown
## B.1 架构

ScholarMind 采用主 API 统一入口，RAG、DeepResearch、Doc Studio 分别承担
论文问答、研究编排和写作编辑能力。

[mermaid 图]

## B.2 第一性原理

| 维度 | 分析 | 结论 |
|---|---|---|
| 数据规模 | 文档索引、会话记忆、报告证据规模不同 | 分层存储与分层检索 |
| 能力归属 | LLM 负责生成，代码负责证据、契约和失败边界 | 不把错误吞成空结果 |
| 写入成本 | Demo、调试、云部署目标不同 | 本地调试与公网部署分开描述 |
| 可逆性 | API schema 稳定 | 后续可替换模型、部署和存储 |
```

（工件清单归 git log / CHANGELOG，不进架构文档。）

## 章节形态示例

### 现状陈述

> ScholarMind 采用主 API + RAG 管线 + DeepResearch + Doc Studio 的多服务架构，围绕论文理解、研究报告和学术写作提供能力。

一句话 + 一图（mermaid 或分层职责表）。

### 分层职责表

| 层 | 负责 | 不负责 |
|---|---|---|
| Main API | 鉴权、Session、知识库、RAG 编排、网关错误边界 | 前端布局 |
| DeepResearch | 研究计划、工具调用、证据聚合、报告生成 | 主站用户体系 |
| Doc Studio | 工作区文件、Agent 编辑、编译/检查、人机确认 | RAG 索引策略 |
| Infra Services | PostgreSQL、Redis、Elasticsearch、MinerU、Grobid、Reranker | 业务策略 |

三列 `层 / 负责 / 不负责`，无解释段落。

### 第一性原理维度选项

- **数据规模**（量化：行数、QPS、体积）
- **能力归属**（哪个角色负责这件事）
- **写入/读取成本**（延迟、token、依赖体积）
- **故障域**（失败面、传染性）
- **可逆性**（未来换方案的迁移成本）

### 接口契约

```text
POST /api/sessions/{session_id}/ask -> AskResponse | SSE events
POST /api/deep-research/runs -> ResearchRun
POST /api/doc-studio/workspaces/{workspace_id}/agent/run -> AgentRun
```

**不变式**：
- 返回结构必须符合 Pydantic schema
- RAG / DeepResearch 结论必须能回到检索 chunk、citation、tool trace 或 web evidence
- 无法解析模型输出、外部 API 鉴权失败、索引失败时失败，不返回伪成功

### 可逆性 / 重评触发条件

"当前选 A，未来可能换 B"类决策给量化门槛：

1. 单次 RAG / DeepResearch 上下文超过主模型稳定窗口
2. 单次 DeepResearch 工具链稳定超时或成本超过目标预算
3. RAG 证据定位失败 case ≥ 5 起
4. 公网演示或云服务器部署的故障域发生变化

**触发判断以运行时指标为准，不在无数据时提前决策。**
