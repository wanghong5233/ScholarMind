# Issue Backlog Examples · 反例 → 正例

## 反例：先入为主、补丁式方案、字段顺序倒置

```markdown
## CHAT-01 上传后总结请求误入检索链路

- 影响：用户上传 PDF 后总结时出现"正在检索知识库"
- 下一步：
  - Phase 1: 上传即直读策略（context-first）
  - Phase 2: 前端显式模式提示，一键切换
  - Phase 3: 后端统一策略
```

问题：

1. 没有 `Repro`，他人无法复现
2. 没有 `Observed Evidence`，无法判断"真检索"还是"文案误导"
3. `Root Cause` 还没写，就跳到 3 个 Phase 的实现方案
4. "上传即直读"是启发式补丁，未经契约评审就被默认
5. 字段顺序错：先写"影响"再写"下一步"，跳过 Symptom/Repro/Evidence/Hypotheses/Root Cause

## 正例：类型声明 + 字段顺序 + 证据闭环

```markdown
## CHAT-01 上传后总结请求链路与用户预期不一致

- type: bug
- status: investigating
- priority: P1

### Symptom
单篇 PDF 上传后立即让系统"总结主要内容"，前端出现"正在检索知识库"。

### Repro
1. 新建会话，保持默认设置（RAG 开关 = on）
2. 上传单篇 PDF
3. 发送："介绍一下这篇论文的主要内容"
4. 观察前端进度文案 + 后端 `[ROUTE_DECISION]` 日志

环境：local docker compose, 2026-05-14 build。

### Observed Evidence
- 前端进度：`⏳ 正在检索知识库，请稍候`
- 后端：`[ROUTE_DECISION] route_type=chat_only retrieval_disabled=True plan=[{'scope': 'session', 'kb_id': 78}]`
- 前端请求 payload：`indexMode=undefined`（即 `auto`），`useRag=true`

### Scope
所有"上传后立即提问"链路；session 同时开启 RAG 开关时复现。受影响版本：`2026-05-14` build。

### Impact
单次会话破坏用户对"刚上传即可直读"的心智预期；造成不必要的等待与困惑。

### Hypotheses
- H1：真实执行了检索（前后端表现一致）→ 文案准确，但默认行为与预期错位
- H2：未真实检索，仅文案误导（自适应路由已 skip，前端仍显示"检索中"）
- H3：session defaults 与本轮 payload 漂移，导致 plan 与执行不符

### Open Questions
- Q1：`[ROUTE_DECISION]` 中 `retrieval_disabled=True` 与前端"正在检索"是否同次请求？
- Q2：在 `useRag=false` 显式关闭后，行为是否完全符合预期？
- Q3：产品契约：上传后首轮总结的默认行为应为「检索」还是「直读」？

### Root Cause
（待 Q1-Q3 完成后填写；当前禁止下结论）

### DoD
1. 同一请求可贯通日志：payload → route 决策 → 真实是否检索 → 前端展示
2. 前端文案与真实链路一致（不允许 retrieval_disabled=True 时显示"正在检索"）
3. 产品契约固化为文档；默认行为有评审记录

### Next Step
- Phase A（证据，本周）：补 3 组对照样本（RAG 开/关、defaults 漂移），输出复现脚本
- Phase B（决策）：契约评审，产出决策记录
- Phase C：按决策落地实现，禁止在评审前合并默认行为变更
```

## 反例：Improvement 类直接写实现

```markdown
## KB-04 文档状态轮询改 SSE

- 下一步：实现 SSE endpoint `/api/docs/{id}/stream`
```

问题：没说明当前行为、为什么需要改、什么条件触发改造，直接跳实现。

## 正例：Improvement 类带 Trigger Condition

```markdown
## KB-04 文档状态延迟

- type: improvement
- status: triaging
- priority: P3

### Current Behavior
文档解析状态走前端 3s 轮询。

### Limitation
单文档解析时长 ≤ 30s 时延迟可接受；超过 30s 或大量并发上传时用户感知明显。

### Trigger Condition
满足以下任一即升级到 `planned`：
- 解析 p95 ≥ 60s
- 单 session 同时上传 ≥ 10 文件成为常态
- 用户反馈"状态更新慢"≥ 3 次 / 月

### Options Considered
- SSE：服务器推送，前端 listener；后端需暴露事件流
- WebSocket：双向连接；超出当前需求
- 长轮询：兼容性好但延迟仍在

### DoD
1. 触发条件达成时启动方案评审
2. 实现后状态延迟 p95 < 1s
3. 旧轮询接口保留为 fallback

### Next Step
等触发条件达成；当前仅收集监控数据。
```
