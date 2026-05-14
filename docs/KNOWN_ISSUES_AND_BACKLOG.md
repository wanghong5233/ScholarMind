# ScholarMind Known Issues & Backlog

> 只追踪未解决、活跃中的问题。问题关闭后直接移出（历史由 Git 保留）。

最后更新：2026-05-14

## 管理约定

- **状态机（4 值）**：`triaging`（问题未定义清楚）/ `investigating`（已定义、根因未定）/ `planned`（根因已定、待实现）/ `blocked`（等外部输入）。**禁用** `todo` / `in_progress` / `done` / `wontfix`。
- **类型（3 类）**：`bug`（行为错误）/ `improvement`（现状可用但需演进）/ `validation`（已实现待验证）。字段顺序随类型而定。
- **硬约束**：`Root Cause` 未确定时，`Next Step` 只允许写"补证据 / 召集决策"，禁止写"改 X / 默认 Y"。
- **Trigger Condition 未达成**的 improvement 类条目 → 状态保持 `triaging`，不进 `planned`。
- **优先级映射**：P0 功能完全不可用 / 数据丢失 / 演示阻塞｜P1 功能降级 / 信任受损｜P2 体验问题但可用｜P3 长期改进 / 触发条件未达成。
- ID 永不复用；同根因不拆多 ID。

## Active Issues

| ID | type | 问题 | 优先级 | 状态 | 本阶段下一步 |
|---|---|---|---|---|---|
| API-01 | bug | Ask 在 429 压力下超时，且失败请求输入可能丢失 | P0 | planned | 实现 Phase 1：持久化前移 + 失败状态落库 |
| ING-01 | bug | 远端解析 strict fail 破坏降级契约（LlamaParse 402 → 全量失败） | P0 | planned | 实现 Phase 0：用户路径关闭 strict fail |
| CHAT-01 | bug | 上传 PDF 后首轮总结链路与用户预期不一致 | P1 | investigating | 补 3 组对照样本（RAG 开/关 × session defaults） |
| KB-06 | validation | 上传"重复文件提示"生产验证 | P1 | investigating | 跑 3 组用例并记录实测输出 |
| DS-01 | improvement | Doc Studio 工作区删除缺少可恢复机制 | P1 | investigating | 收敛软删 / 恢复 / 延迟硬删 三方案 |
| KB-04 | improvement | 文档状态仍为 3s 轮询 | P3 | triaging | 等触发信号（解析时长增长 / 用户反馈） |
| KB-05 | improvement | `doc_studio` 镜像体积偏大 | P3 | triaging | 等触发信号（磁盘告警阈值） |
| KB-03 | improvement | 同上传请求内重复文件未前置去重 | P3 | triaging | 等触发信号（批量上传性能瓶颈） |

---

## API-01 · Ask 超时与输入丢失

- **type**: bug ｜ **status**: planned ｜ **priority**: P0

### Symptom

调用 `/ask` 后，前端可能显示 `ASK timeout at generation`，且 `GET /api/history/get_messages` 看不到刚刚发送的用户输入。

### Repro

1. 在 LLM Provider 已达 RPM 上限的窗口内连续发起 ≥3 个 `/ask` 请求。
2. 等待任一请求超时。
3. 拉取该 conversation 的消息列表，对比已发送 input 是否在列。

### Observed Evidence

```
openai.RateLimitError: 429 Too Many Requests
... 同一 attempt 在同 provider 内串行重试至 SM_ASK_TIMEOUT_SECS 才退出
拉取消息列表：用户输入条目缺失
```

### Scope

后端 `chat_ask_orchestrator` + `llm/client.py` + 消息持久化路径；影响所有走 `/ask` 的会话。

### Impact

任一 429 抖动窗口内：用户既看不到答案、也看不到自己刚发的输入，对系统可靠性的信任直接受损。这是 P0 信任问题，不只是功能问题。

### Root Cause

- 用户输入持久化挂在"生成成功"路径尾部；生成失败 / 超时分支未保证写入。
- `429` 被归为模型级 fallback，未升格为 Provider 级熔断 → 同 Provider 下其他模型继续尝试，串行重试拉满超时预算。
- 缺少 `accepted/running/failed/timed_out` 一等状态字段，前端只能呈现"有答案 / 无答案"。

### DoD

1. `POST /ask` 返回 `accepted` 后，`GET /api/history/get_messages` 必须可见该条用户输入（无论后续是否失败）。
2. 同一 attempt 内，单 Provider 命中 `429` 或连接故障 → 立即熔断该 Provider，不再尝试该 Provider 下其他模型。
3. 失败请求必须落库：`status + error_code + provider + model + retryable`。
4. 前端可见 `failed/timed_out` 并支持"重试为新 attempt"。

### Next Step

- **Phase 1 (P0)**: 持久化前移到生成发起前 + 补失败状态字段（满足 DoD 1, 3）。
- **Phase 2 (P1)**: Provider 级分类 / 熔断 / 退避（满足 DoD 2）。
- **Phase 3 (P1)**: 前端 `failed/timed_out` 呈现与重试语义（满足 DoD 4）。

---

## ING-01 · 远端解析 strict fail 破坏降级契约

- **type**: bug ｜ **status**: planned ｜ **priority**: P0

### Symptom

`/upload` 接口同步返回成功，但后台 parser job 整批失败；前端用户感知为"上传后全部不可用"。

### Repro

1. 配置 `SM_PARSER_ORDER=llamaparse,unstructured_api,pymupdf` + `SM_REMOTE_PARSER_STRICT_FAIL=true`。
2. 将 LlamaParse 账号置于 `402 Payment Required` 状态。
3. 上传任意 PDF。
4. 观察 parser job 是否回退到 `unstructured_api` 或 `pymupdf`。

### Observed Evidence

```
llamaparse: 402 Payment Required
strict_fail=True → pipeline abort at first remote parser
后续 unstructured_api / pymupdf 未被调用
```

### Scope

`backend/app/service/core/ingestion/parser_orchestrator`；影响所有走 `/upload` 的上传链路。

### Impact

录屏 / 线上演示窗口内全量解析失败 → 上传功能整体不可用，可用性直接 0。

### Root Cause

- `SM_REMOTE_PARSER_STRICT_FAIL=true` 被错误地应用到**用户路径**；它原本应只作用于"发布前校验"。
- 解析顺序首跳是付费 SaaS（LlamaParse），其额度故障被 strict fail 升级为整链路故障，绕过降级策略。

### DoD

1. 用户路径上，单一远端解析器的额度 / 账单故障**不得**导致整条解析链路失败。
2. 命中 `402 / 429 / 5xx` 时必须继续尝试后续解析器，并记录每一跳结果。
3. Job 结果可见最终使用的解析器与降级轨迹（"成功来自哪一跳"）。
4. `STRICT_FAIL` 仅允许用于发布前校验 / 灰度守门，不允许作用于最终用户链路。

### Next Step

- **Phase 0 (P0, 演示止血)**: 用户路径关闭 `STRICT_FAIL`，启用可降级解析顺序（满足 DoD 1, 2）。
- **Phase 1 (P1, 结构化)**: 拆分"用户路径策略"与"校验路径策略"两套开关（满足 DoD 4）。
- **Phase 2 (P1, 防复发)**: 解析供应商额度健康探针 + 预警阈值 + UI 友好提示。

---

## CHAT-01 · 上传后首轮总结链路与用户预期不一致

- **type**: bug ｜ **status**: investigating ｜ **priority**: P1

### Symptom

"刚上传单篇 PDF → 立即请求总结"场景下，前端出现"正在检索知识库"提示；与"应聚焦刚上传文件内容"的用户心智冲突。

### Repro

1. 上传单篇 PDF（默认 `draftRagEnabled=true`）。
2. 发送"总结这篇文章主要内容"。
3. 观察前端是否出现"正在检索知识库"提示。
4. 对照后端日志确认是否实际执行检索。

### Observed Evidence

- 前端默认 `draftRagEnabled=true`；仅当显式 `useRag=false` 时才下发 `indexMode=disabled`。
- 上传路径按 `usingRag` 分叉：`true` 走 `/upload`（入会话 KB），`false` 走 `upload-for-context`（直读上下文）并异步入库。
- 后端 `index_mode=auto` 时基于 session defaults 组装 retrieval plan（可含 session KB + user KB）。
- 后端仅在 `index_mode == "disabled"` 时注入 `context_json.uploaded_files` 到生成上下文。

### Scope

`frontend/src/pages/chat` + `backend/app/service/core/conversation/chat_ask_orchestrator`；影响"上传后首轮"场景。

### Impact

用户在演示 / 真实使用中等待无效检索 → 困惑、放弃；对"知识助手是否真在用我刚给的文件"产生信任怀疑。

### Hypotheses（互斥、可证伪）

1. **H1 实际真检索了**：后端 `index_mode=auto` + session KB 非空 → retrieval plan 命中 → 真执行了检索。
2. **H2 前端文案误导**：后端未检索，但前端阶段提示与真实链路不一致。
3. **H3 产品契约空白**："上传后首轮总结"在产品层无明确约定，前后端各自合理但组合不合理。

### Open Questions

- 给 3 组对照样本（RAG 开 / 关 × session defaults 默认 / 自定义），前端文案 vs 后端真实链路是否一致？→ 关闭 H2。
- 后端 retrieval plan 日志在该场景下是否真有非空命中？→ 关闭 H1。
- 产品决策："上传后首轮"是 RAG-on-uploaded-only / 直读上下文 / 全量检索？→ 关闭 H3。

### Root Cause

待 Hypotheses 收敛后填写。

### DoD

1. 同一请求可追踪完整链路：前端 payload（`useRag/indexMode`）→ 后端 route 决策 → 是否实际检索 → 前端展示文案。
2. 前端提示必须与后端真实执行链路一致，禁止"未检索却显示检索中"。
3. 明确并固化产品契约：上传后首轮总结在默认配置下的预期行为。
4. 契约评审通过前，不引入启发式补丁作为默认行为。

### Next Step

- 补全 3 组对照样本与链路日志，关闭 H1 / H2 / H3 至少一条。

---

## KB-06 · 上传"重复文件提示"生产验证

- **type**: validation ｜ **status**: investigating ｜ **priority**: P1

### Subject

`/upload` 接口在重复文件场景下的提示文案准确性。

### Test Plan

| # | 用例 | 输入 |
|---|---|---|
| T1 | 单文件重复 | 同一 PDF 连续上传 2 次 |
| T2 | 混合批量 | 一次上传 3 个文件，其中 2 个 hash 相同 |
| T3 | 慢解析 | 单文件、解析在 15s 内未达终态 |

### Pass Criteria

- T1：第二次提示 `1 篇已存在跳过`。
- T2：提示 `2 篇新增，1 篇已存在跳过`。
- T3：15s 内无终态时提示 `仍在处理`，**不**误报 `失败`。

### Environment

生产环境（domain.scholarmind.ai），最新 release 镜像。

### Result

待跑（保持本条直到三个用例全 pass 并记录实测输出）。

---

## DS-01 · Doc Studio 工作区删除可恢复性

- **type**: improvement ｜ **status**: investigating ｜ **priority**: P1

### Current Behavior

工作区删除走硬删除：直接从 DB 移除工作区记录及关联文档，不留恢复路径，审计仅靠数据库审计日志。

### Limitation

误删后**无任何用户侧恢复入口**；客服 / 工程介入也只能从备份恢复，恢复粒度粗、耗时长；不满足"误操作可挽回"的产品最小预期。

### Trigger Condition

已达成——P1 用户教育型功能在 SaaS 产品里基本属于"刚需即将"，无需等具体告警。

### Options Considered

| 方案 | 思路 | 风险 |
|---|---|---|
| A. 软删（is_deleted 字段） | 标记删除 + 用户侧"已删除"列表可恢复 | DB 字段污染、查询要加过滤 |
| B. 软删 + 延迟硬删 task | A 之上 30 天后异步硬删 | 增加调度复杂度 |
| C. 备份 / Snapshot | 备份系统恢复 | 用户体感差、运维介入 |

### DoD

1. 删除后用户在"已删除工作区"列表可见，可一键恢复，状态完全还原。
2. 30 天后自动硬删；硬删前再次提醒。
3. 整链路（软删 / 恢复 / 硬删）有审计字段：actor / time / reason。
4. 全链路回归测试覆盖。

### Next Step

- 收敛方案 A / B（建议 B）→ 选定后状态转 `planned`。

---

## KB-04 · 文档状态仍为 3s 轮询

- **type**: improvement ｜ **status**: triaging ｜ **priority**: P3

### Current Behavior

文档解析进度状态通过前端每 3s 轮询 `/documents/status` 接口呈现。

### Limitation

p95 解析时长 < 30s 时观感可接受；解析时长上升时用户感知最多 3s 滞后。

### Trigger Condition（未达成）

下列任一发生才升 `planned`：
- 解析时长 p95 >60s 且影响用户路径。
- 用户反馈"状态延迟明显"在 2 周内 ≥3 例。

### Next Step

- 保持监听；触发条件达成前不投入实施。

---

## KB-05 · `doc_studio` 镜像体积偏大

- **type**: improvement ｜ **status**: triaging ｜ **priority**: P3

### Current Behavior

`doc_studio` 容器镜像体积约 2.1 GB（包含完整 LaTeX 工具链 + 字体）。

### Limitation

冷启动 ~12s；磁盘占用偏高但当前未触发告警阈值。

### Trigger Condition（未达成）

- ECS 磁盘水位 >80%，或
- 冷启动时间 p95 >20s。

### Next Step

- 保持监听；触发条件达成前不投入瘦身。

---

## KB-03 · 同上传请求内重复文件未前置去重

- **type**: improvement ｜ **status**: triaging ｜ **priority**: P3

### Current Behavior

单次 `/upload` 内若同 hash 文件出现多次，每个文件独立进入解析流水线；功能正确但有冗余 IO。

### Limitation

冗余消耗存储 / 解析配额；当前批量上传量未到瓶颈，无可感受影响。

### Trigger Condition（未达成）

- 批量上传 p95 时长 >上限的 1.5×，或
- 解析配额因冗余被显著消耗（>10%）。

### Next Step

- 保持监听；触发条件达成前不投入实施。
