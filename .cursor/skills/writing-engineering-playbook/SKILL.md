---
name: writing-engineering-playbook
description: >-
  Enforces ScholarMind engineering-playbook style: cross-project, project-
  agnostic engineering intuitions distilled into reusable principles. Primary
  axis is Agent/LLM application engineering, secondary axis is generic backend
  /distributed systems. Source files live in `docs/private/engineering-
  playbook/` (git-ignored) and publish to Feishu / personal blog, NOT GitHub
  README. Use when editing files under `docs/private/engineering-playbook/`,
  or when the user asks to write / 沉淀 / 提炼 工程经验 / Agent 开发经验 /
  playbook / 第一性原理 / 反模式 / 触发信号 / cross-project lesson / 跨项目
  复用 / 技术博客.
---

# Writing ScholarMind Engineering Playbook

## 一句话准则

**Playbook 一篇文章只回答一件事：跨项目复用的工程直觉是什么、为什么是它。绑定具体项目的内容、辩证过程、对话痕迹都不是 playbook。**

## 与 pitfall / architecture / readme 的边界

| 文档 | 内容粒度 | 项目绑定 | 时效 | 发布渠道 | 形态 |
|---|---|---|---|---|---|
| **Playbook** | 跨项目工程直觉 | **无** | 几年 | **飞书 / 博客** | 反模式 vs 正例 + 第一性原理 + 信号 + 自检 |
| Pitfall Archive | 单项目环境故障 | 强（路径/容器/env） | 几个月 | 仓库内 docs/ | 五段式（Symptom/Evidence/RC/Solution/Invariant） |
| Architecture Doc | 单项目当前实现 | 强 | 年级别 | 仓库内 docs/ | 现状 + 第一性原理表 + 契约 |
| README | 项目对外名片 | 强 | 持续 | GitHub 仓库主页 | standard-readme 章节 |

**判别口诀**：写 playbook 时把所有 ScholarMind 业务名词（`pgvector` / `doc_studio` / `SM_*` env / `LlamaParse`）改成抽象概念（向量索引 / 协作服务 / 业务参数 / 文档解析），文章如果还成立 → 合格；如果立刻散架 → 这是 pitfall 或 architecture 的料，不是 playbook。

## 主轴 / 副轴（决定写什么主题）

| 轴 | 范围 | 示例主题 |
|---|---|---|
| **主轴 · Agent / LLM** | LLM 应用 / Agent 编排 / RAG / 工具调用专属 | LLM provider 级熔断；Prompt 是契约；工具调用后一致性；评估闭环（offline→online）；上下文预算治理；记忆分层；意图路由的退路 |
| **副轴 · 通用后端 / 分布式** | 任何工程都适用 | 参数治理；决策-执行强一致；失败要响亮；可观测即合同；灰度与回滚；启动期校验；资源约束驱动设计；不可逆操作护栏 |

**主轴必须超过副轴**：一篇 Agent 主题 + 一篇通用主题，再写第二篇 Agent 主题。保持 playbook 的差异化身份（不是又一本通用 SRE 手册）。

## 硬性禁止（命中即删）

| 反模式 | 判断特征 | 归宿 |
|---|---|---|
| 项目业务名词 | 出现 `scholarmind_xxx` / `SM_*` / `pgvector` / `LlamaParse` / `DashScope` | 改抽象概念；改不掉 → 改投 pitfall |
| 散文化讲故事 | "我们项目曾经……后来发现……于是改成……" | 改第一性原理维度表 |
| 决策辩证过程 | "考虑过 A / B / C，最后选 D" | git log / `private/` |
| 业界对比铺垫 | "Stripe 这样做，OpenAI 那样做，所以……" | 删；必要保留 1 句作为引用 |
| 喊口号 | "工程师要有责任感 / 系统要健壮" | 删；改为可验证的检测信号 |
| 一篇覆盖多个原理 | 标题"分布式系统设计精要" | 拆成单篇单主题 |
| 教程化 | "首先 / 第二步 / 第三步" | 改 mermaid 或伪代码骨架 |
| 引用未公开内部链接 | 链 `private/` 内文件或公司内 wiki | 删；外引用必须可公开访问 |
| 时效语言 | "在 2026 年 / 最新版 Python / 当前流行的 X" | 改无时效表述 |

## 必要章节（按 01/04 实际形态归纳）

每节缺哪一块不强求，**出现即必须是这种形态**：

### 1. 现状陈述（一段，现在时）

> 任何在源码里直接出现的数字、字符串字面量，都默认是**配置维度的塌缩**，需要按 `任务 × 模型 × 服务 × 版本 × 灰度` 反向展开。

不写背景、不写"过去我们怎么做"。直接说"现在的规则是什么"。

### 2. 反模式 vs 正例（表格）

至少 5 行，每行一个观察维度（来源、可见性、模型差异、变更轨迹、灰度、审计…）。一行一对照，无解释段落。

### 3. 第一性原理（维度表）

5 行内。每行：`维度 / 分析 / 结论`。维度名从抽象语义中选：

- 调用面 / 异质性 / 可逆性 / 故障域 / 可观测 / 数据正确性 / 信噪比 / 责任归属 / 复发频率

**禁止**散文化的"我们认为 / 这是因为"。

### 4. 触发抽象的信号（编号列表 4-7 条）

可观测的、可验证的"什么情况下立即上抽象"。不写直觉，写检测特征：

> 1. 同一字面量在两个文件出现
> 2. PR review 出现"这个数字怎么定的"
> 3. 故障复盘需要追溯当时的参数

### 5. 设计骨架 / 检测信号 / 适用边界（按主题选用）

灵活段，按文章特性挑选。形态：

- **设计骨架**：用 `text` 块写**语言无关伪代码**（不写 Python/Go 实现，避免绑定）
- **检测信号**：编号列表，对应"什么样的代码味道一律视为待修"
- **适用边界**：表格 `情境 / 是否启用`，明确什么时候不该上抽象（避免过度工程）

### 6. 自检清单（5-7 条复选框）

提交前自问。每条必须可单点验证（一眼能判断 yes/no）：

> - [ ] 这个数字会因模型不同而最优值不同吗？是 → 必须按 model 分桶。
> - [ ] 故障时能在响应里看到当时用的参数版本吗？看不到 → 缺审计。

### 7. 反向链接（playbook 内部交叉引用）

> - 配套 CI 守门 → [05-observability-as-contract](./05-observability-as-contract.md)
> - 配套灰度策略 → [06-rollout-as-feature](./06-rollout-as-feature.md)

形成 playbook 网状结构。**不引用本仓库 pitfall**（pitfall 是项目内的，playbook 跨项目，单向：pitfall 可引 playbook，playbook 不引 pitfall）。

## 写作微观规范

- 标题：`NN · 中文名 / English Name`（双语，与 01/04 对齐）
- 中文正文 + 英文代码 / 伪代码标识符
- 现在时陈述（不写"我们曾经 / 后来 / 这次"）
- 段落 ≤ 3 行；超过改表格 / mermaid / 伪代码
- 单篇 ≤ 150 行（飞书一屏可读）；超过拆篇
- 表格密度高于文字密度
- 无 emoji、无感叹号、无形容词自夸
- 不写时效语（"最近 / 当前流行 / 在 2026 年"）

## 自检清单（提交前必过）

### 内容质量
- [ ] 把所有 ScholarMind 业务名词改成抽象词，文章是否仍成立？不成立 → 删 / 改投 pitfall
- [ ] 出现"我们 / 我 / 曾经 / 后来 / 这次"了吗？→ 改现在时
- [ ] 反模式 vs 正例表 ≥ 5 行？
- [ ] 第一性原理表的维度名是抽象语义吗（异质性 / 可逆性 / 故障域）？还是项目术语？
- [ ] 触发信号是可单点验证的特征，还是直觉？

### 开源就绪（飞书 / 技术博客 / 作品集发布前）
- [ ] 含真实 IP / hostname / API key / 内部域名吗？→ 删
- [ ] 含未公开仓库的链接吗（公司内 wiki / `private/` 路径）？→ 删
- [ ] 文章在另一个项目（比如电商系统、社交 App）的工程师读起来仍有指导价值吗？无 → 改投 pitfall
- [ ] 标题与文件名编号一致？反向链接全部跳得通？

## 反例 → 正例

### 反例：散文化技术博客 + 项目绑定 + 辩证过程

```markdown
# 关于 LLM 路由的一些思考

最近在做 ScholarMind 的时候，发现 OpenAI 经常超时，我们一开始用的是简单的
模型级 fallback，比如 gpt-5 失败就降级到 gpt-5-mini，但这样有个问题——OpenAI
整个不可达的时候，每个模型都要等 60s timeout，加起来就是几分钟。

我们考虑了几种方案：
1. 模型级熔断：每个模型独立计数。优点是粒度细，缺点是 OpenAI 全断时仍然慢。
2. Provider 级熔断：整个 provider 算一个单位。这个比较 stripe 的设计……

最后我们选择了方案 2，效果不错……
```

问题：
- 项目绑定（ScholarMind / OpenAI / gpt-5 具体值）
- 散文化讲故事（最近 / 一开始 / 后来）
- 辩证过程（考虑了 A / B / 最后选 C）
- 业界对比铺垫（比较 stripe……）
- 时效语（最近）

### 正例：抽象原则 + 反模式表 + 第一性原理

```markdown
# 08 · Provider 级熔断 / Provider-Level Circuit Breaker

## 现状陈述

LLM 调用 fallback 必须按 provider 而非按 model 计数。同一 provider 不可达时，
该 provider 下所有 model 在当前请求内必须立即跳过，不再逐个尝试。

## 反模式 vs 正例

| 维度 | 反模式 | 正例 |
|---|---|---|
| 计数粒度 | 按 model 计 timeout 次数 | 按 provider 计连续不可达次数 |
| 失败传染 | 一个 model 失败，下一个 model 仍发起调用 | provider 标黑后，本请求内全部跳过 |
| 探测窗口 | 每次请求都重新探测 | 失败窗口内复用判定 |
| 可观测 | 只记录最终 fallback 用了哪个 model | 记录跳过的 provider 列表 |
| 跨请求 | 状态不持久 | 内存级 TTL + 重试探活 |

## 第一性原理

| 维度 | 分析 | 结论 |
|---|---|---|
| 异质性 | 不同 provider 的失败模式不同（网络/限流/认证）| 失败语义必须按 provider 分类 |
| 故障域 | 一个 provider 的网络问题会同时影响其所有 model | 失败传染必须在 provider 层切断 |
| 可逆性 | 网络问题往往秒级恢复 | 必须有 TTL 自动恢复，不能永久标黑 |
| 可观测 | 排障时需要回答"哪个 provider 当时被跳过" | 响应/日志必须带 `skipped_providers` |

## 触发抽象的信号

1. fallback 链路里出现"OpenAI 全部 model 挨个超时"现象
2. 单次请求总耗时 = `N × timeout`，N = 该 provider 的 model 数
3. 排障日志只能看到最后一个 model 的失败，看不到前面被跳过的
4. 出现 `if model_name.startswith('gpt')` 之类的硬编码 provider 判别

## 自检清单

- [ ] fallback 计数是按 provider 还是按 model？按 model → 改
- [ ] provider 标黑有 TTL 吗？无 → 加
- [ ] 响应里能看到本次请求被跳过的 provider 吗？看不到 → 加审计字段
- [ ] 当某 provider 的所有 model 都被标黑时，是否快速失败而非陷入空转？

## 反向链接

- 兜底审计字段 → [04-loud-failure](./04-loud-failure.md)
- 参数治理（provider 列表 / TTL 配置）→ [01-parameter-governance](./01-parameter-governance.md)
```

## 业界对照（设计依据）

| 来源 | 关键概念 | 在本 skill 的体现 |
|---|---|---|
| Stripe / Cloudflare engineering blog | 一篇一主题；表格密度高；无形容词；可执行 | §写作微观规范、§必要章节 |
| [Google SRE workbook](https://sre.google/workbook/table-of-contents/) "philosophies" 章节 | 把工程直觉沉淀为不绑定具体故障的"哲学" | §一句话准则、§硬性禁止「项目业务名词」|
| Martin Kleppmann 《Designing Data-Intensive Applications》 | 用语言无关伪代码 + 抽象维度论证，跨技术栈复用 | §设计骨架「语言无关伪代码」 |
| ADR (Architecture Decision Records) 文化 | 一份 record 只承载一个决策；context / decision / consequences 三段 | §必要章节 4 节式（现状 / 对比 / 原理 / 信号）|
| [Awesome Engineering Blogs](https://github.com/kilimchoi/engineering-blogs) 风格 | 长青文章不写时效语，避免"今年最佳实践"陷阱 | §硬性禁止「时效语言」 |

**与 pitfall 的关键差异**（再次强调）：pitfall 回答"我们项目今天怎么不死"，playbook 回答"任何项目长期下去都该怎么做"。两者绑定与否、时效长短、可发布性都不同。

## 链路

- 工程约束基线：`.cursor/rules/core-principles.mdc`
- 部署 / 坑点档案撰写：`.cursor/skills/writing-pitfall-archive/SKILL.md`
- 架构文档撰写：`.cursor/skills/writing-architecture-docs/SKILL.md`
- README 撰写：`.cursor/skills/writing-readme/SKILL.md`
- 当前 playbook 实例集：`docs/private/engineering-playbook/`（已有 01-07 七篇）
