---
name: writing-architecture-docs
description: >-
  Enforces ScholarMind architecture-document style: current-state statements,
  first-principles tables, contracts. Use when editing files under `docs/`
  matching `*设计*` / `*架构*` / `*Architecture*` / `*ADR*` / `*RFC*`, or when
  the user asks to write/修改 architecture / 设计 / ADR docs, or complains a
  doc is 啰嗦 / has 口水 / 对话记录.
---

# Writing ScholarMind Architecture Docs

## 一句话准则

**架构文档只回答两个问题：`当前实现是什么` 与 `为什么是这个形态`。其他都属污染。**

## 硬性禁止（命中即删）

| 反模式 | 判断特征 | 归宿 |
|---|---|---|
| 结论先行 / TL;DR / 摘要 | 用 `**xxx**` 开头总结全文 | 删除，每节都是结论无需元结构 |
| 辩证过程 / 四轮反应 / 讨论记录 | 有序列表"第一反应→反驳→第二反应" | `agent-transcripts/` |
| 外部证据 / 产品对比 / 调研表 | 列举 ChatGPT / Claude / Letta 等做法 | 删除，至多一句泛指 |
| 已删除/不再维护工件清单 | 列出被移除的文件、env、字段 | git log / CHANGELOG |
| 运维现象 | "启动失败 / wheel 冲突 / ABI 问题 / Windows-WSL 下 xxx" | issue tracker |
| 过程时态 | "之前草案……落地阶段……这次决定……" | 改现在时 |
| 对话/汇报语气 | "这里我思考 / 可见 / 迫使我们 / 就 / 说白了 / 其实" | 直接删 |
| 散文堆叠 | 连续 3 段超 5 行 | 改表格 / mermaid / 签名代码块 |

## 必要结构

章节缺哪一块不强求，**出现即必须是这种形态**：

### 1. 现状陈述（一句 + 一图）

现在时，直接说当前实现是什么：

> ScholarMind 采用主 API + RAG 管线 + DeepResearch + Doc Studio 的多服务架构，围绕论文理解、研究报告和学术写作提供能力。

配一张 mermaid 或分层职责表。

### 2. 分层职责表

三列 `层 / 负责 / 不负责`，一层一行，无解释段落。

| 层 | 负责 | 不负责 |
|---|---|---|
| Main API | 鉴权、Session、知识库、RAG 编排、网关错误边界 | 前端布局 |
| DeepResearch | 研究计划、工具调用、证据聚合、报告生成 | 主站用户体系 |
| Doc Studio | 工作区文件、Agent 编辑、编译/检查、人机确认 | RAG 索引策略 |
| Infra Services | PostgreSQL、Redis、Elasticsearch、MinerU、Grobid、Reranker | 业务策略 |

### 3. 第一性原理分析（为什么是这个形态）

维度表，不用散文。维度名从以下挑选：

- **数据规模**（量化：行数、QPS、体积）
- **能力归属**（哪个角色负责这件事）
- **写入/读取成本**（延迟、token、依赖体积）
- **故障域**（失败面、传染性）
- **可逆性**（未来换方案的迁移成本）

示例：

| 维度 | 分析 | 结论 |
|---|---|---|
| 数据规模 | 单篇论文、会话知识库和用户知识库的索引规模不同 | Session KB 与用户知识库分层 |
| 能力归属 | LLM 负责语义判断，代码负责契约、证据和错误边界 | 回答必须可追溯到检索证据 |

### 4. 接口契约（签名 + 不变式）

```text
POST /api/sessions/{session_id}/ask -> AskResponse | SSE events
POST /api/deep-research/runs -> ResearchRun
POST /api/doc-studio/workspaces/{workspace_id}/agent/run -> AgentRun
```

**不变式**：
- 返回结构必须符合 Pydantic schema
- RAG / DeepResearch 结论必须能回到检索 chunk、citation、tool trace 或 web evidence
- 无法解析模型输出、外部 API 鉴权失败、索引失败时失败，不返回伪成功

### 5. 可逆性 / 重评触发条件（如适用）

"当前选 A，未来可能换 B" 类决策给量化门槛：

1. 单次 RAG / DeepResearch 上下文超过主模型稳定窗口
2. 单次 DeepResearch 工具链稳定超时或成本超过目标预算
3. RAG 证据定位失败 case ≥ 5 起
4. 公网演示或云服务器部署的故障域发生变化

**触发判断以运行时指标为准，不在无数据时提前决策。**

## 写作微观规范

- 中文正文 + 英文代码/标识符
- 现在时陈述：❌"我们决定采用 X" → ✅"采用 X"
- 禁用口语连接词：就 / 其实 / 说白了 / 可见 / 显然
- 无感叹号、无 emoji
- 表格 > 列表 > 段落；段落不超过 3 行
- 章节引用用 `§X.Y` 或 `[附录 B](#...)`，不写"上文提到过"

## 自检清单（提交前必过）

对每一行自问：

- [ ] 描述的是"当前架构"还是"过程/对话/运维"？后者→删
- [ ] 能用表格/图/签名代替吗？能就替
- [ ] 删掉这行读者会漏什么架构事实？漏不掉→删
- [ ] "为什么"是否走了第一性原理维度表？口水论证→改表
- [ ] 出现被禁止章节了吗？命中→删

## 反例 → 正例

### 反例：结论先行 + 辩证过程 + 工件清单

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

### 正例：现状陈述 + 维度表

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

## 链路

- 工程约束基线：`.cursor/rules/core-principles.mdc`
- 架构约束基线：`.cursor/rules/scholarmind-architecture.mdc`
- README 撰写：`.cursor/skills/writing-readme/SKILL.md`
- 部署 / 坑点档案撰写：`.cursor/skills/writing-pitfall-archive/SKILL.md`
- 跨项目工程经验：`.cursor/skills/writing-engineering-playbook/SKILL.md`
