---
name: writing-pitfall-archive
description: >-
  Enforces ScholarMind operations/deployment pitfall-archive style: distill
  recurring failures into invariants, not procedure dumps. Modeled on Google
  SRE blameless postmortem but written as a living archive (multi-incident
  sediment, not single-incident report). Use when editing files matching
  `*DEPLOYMENT*` / `*MANUAL*` / `*RUNBOOK*` / `*PITFALL*` / `*OPERATIONS*` /
  `*坑*` / `*手册*` under `docs/`, or when the user asks to sediment 部署经验
  / 踩坑总结 / 故障复盘 / runbook / postmortem / 不再犯同样错误.
---

# Writing ScholarMind Pitfall Archive

## 一句话准则

**档案只为一件事：让同一类故障不再发生第二次。任何不服务于这个目的的内容都是噪音。**

## 与业界 Runbook / Postmortem 的关系

| 文档类型 | 范围 | 时态 | 在本仓库的位置 |
|---|---|---|---|
| Postmortem（单次复盘） | 单一事件 | 过去式：发生过什么 | git commit message / `private/` |
| Runbook（操作手册） | 高频流程 | 命令式：怎么做 | `backend/scripts/` 的脚本即是 |
| **Pitfall Archive**（坑点档案） | 多次故障沉淀 | 现在时：现在的规则是什么 | **`docs/*MANUAL*.md` ← 本 skill 的目标** |

**关键差异**：Postmortem 是事件级临时产物，可归档；Pitfall Archive 是"项目级不变量库"，永远活的、持续演进。

## 硬性禁止（命中即删）

| 反模式 | 判断特征 | 归宿 |
|---|---|---|
| 命令序列 / 一步步操作 | 连续 `cd / docker compose / ssh` 等 5 行以上代码块 | shell 脚本 (`backend/scripts/`) |
| 教程口吻 | "首先 / 接下来 / 然后 / 最后" | 改为坑点条目 |
| 架构图 / 整体图 | mermaid 整体 graph、组件关系图 | 架构文档（`writing-architecture-docs`）|
| 单次事件的完整记录 | "2026-05-09 14:32 我登录 ECS 看到 ..." | git log / `private/` |
| 决策辩证过程 | "考虑了 A 方案，又考虑 B，最后选 C" | git commit / `private/` |
| 安抚性语言 | "不用担心 / 这是正常的 / 后续会解决" | 删 |
| 重复表达 | 同一不变量在 4 节里复述 | 用 `§X.Y` 内部引用 |
| 复制粘贴的报错堆栈 | 完整 traceback 占 30 行 | 留关键 3 行作 Evidence |
| 与项目无关的通用 SRE 知识 | "Docker 镜像分层原理 / Linux OOM 机制" | 删 |

## 必要章节结构

每节缺哪一块不强求，**出现即必须是这种形态**：

### §1 硬性约束（Hard Constraints）

不变量清单，每条 1-2 句、含**违反后果**：

> **1.2 2C2G 不能并发** — 不能"旧容器 + build"同跑（必先 stop）；不能并行 build 多服务（必须串行）；**doc_studio 永远不能在 ECS 上完整 build**（含 texlive，必然 thrash）

格式：`**编号 + 一句话约束** — 量化范围 + 违反后果`。**不解释 why**（why 进 §4 坑点条目）。

### §2 路径与命名约定

环境路径、容器命名、域名、卷挂载的事实表：

| 项 | 值 | 备注 |
|---|---|---|
| 代码 checkout | `/opt/apps/scholarmind` | 与 `/opt/apps/<project>` 同构 |
| 容器命名 | `scholarmind_<service>` | `api` / `doc_studio` / `db` |

### §3 业务关键 env（本地 ↔ ECS 必须对齐）

变量名 + 典型值 + **漂移后果**。**不写完整 .env 内容**（那是 `.env.production.example` 的职责）。

### §4 坑点档案（核心）

每条坑点用**五段式**：

| 段 | 内容 | 长度 |
|---|---|---|
| **Symptom** | 用户/工程师看到的现象 | 1-2 句 |
| **Evidence** | 可复现的证据：日志关键词、命令输出片段、监控数值 | 2-4 行 |
| **Root Cause** | 为什么会出现，引用 §1 的硬约束编号 | 1-3 句 |
| **Solution** | 改 env / 改代码 / 改流程 / 改架构 | 1-3 句 |
| **Invariant** | 沉淀到 §1 的哪条；如是新增不变量则在此声明；如同类问题在其他项目也见过 → 上抽象到 playbook 并注明 | 1-2 句 |

条目命名：`§4.N [日期] 坑点一句话总结`，按时间倒序排列。

### §5 关键工具脚本

| 脚本 | 触发条件 | 产物 |
|---|---|---|
| `backend/scripts/prepare_nltk_data.sh` | 首次部署 / NLTK 资源缺失 | `/opt/data/nltk_data/*` |

**不写脚本内部实现**（那是脚本注释的职责），只写"什么时候用 / 产生什么"。

### §6 信号判别表

症状 → 坑点条目编号的快速查表：

| 症状 | 查 |
|---|---|
| CPU 95%+、SSH banner timeout | §4.3 |
| 本地能跑、ECS 不能跑 | §4.6 |

### §7 演进规则（这份档案怎么活下去）

| 触发 | 行为 |
|---|---|
| 同一现象出现第 2 次 | 新增 §4.N 条目 |
| 同一类约束被违反 ≥ 3 次 | 升级到 §1 硬性约束 |
| **同类问题在 ≥ 2 个项目里都出现** | **上抽象到 engineering-playbook（跨项目复用层）**，pitfall 条目里 Invariant 段引用 playbook §NN |
| 不变量被新架构推翻 | 标 `~~strikethrough~~` 保留 1 个版本，下次清理 |
| 工具脚本失效 | §5 删条 + 脚本归档到 `private/archived/` |
| 季度回顾 | 检查 §4 中 6 个月未触发的条目，归档到 `docs/archived/` |

**两层引用方向（严格单向）**：

| 方向 | 是否允许 | 理由 |
|---|---|---|
| pitfall → playbook | 是 | "上抽象到 playbook §08" 提醒未来跨项目复用 |
| playbook → 本仓库 pitfall | **否** | playbook 跨项目，若 link 回项目内 pitfall 即破坏可移植性 |
| playbook ↔ playbook | 是 | playbook 内部反向链接形成网状结构 |

## 写作微观规范

- 现在时陈述：❌"曾经出现过 X" → ✅"在 X 条件下会出现 Y"
- 量化优先：❌"CPU 很高" → ✅"CPU 95%+ 持续 30s"
- 证据原始化：日志关键词原文、容器状态原文、监控数值原文；不二次描述
- 内部交叉引用用 `§X.Y`，不用"上文 / 前面"
- 中文正文 + 英文代码 / 日志 / 路径
- 编号一旦发布**永不变**（外部脚本/文档可能 `grep §4.3`），新增只往后追加

## 自检清单（提交前必过）

- [ ] 这一段是"现在的规则"还是"过去发生的故事"？后者 → 删 / 移 `private/`
- [ ] 能用表格 / 五段式代替吗？能就替
- [ ] 命令序列超过 5 行？→ 抽成脚本，正文留链接
- [ ] 出现"首先 / 接下来 / 然后"这种教程口吻？→ 改写
- [ ] §1 / §4 / §6 三处是否互相引用闭环？（信号判别能否查到坑点，坑点能否上溯到约束）
- [ ] 编号有无与历史版本冲突？（git blame 验证）
- [ ] 含敏感信息（真实 IP / hostname / API key 值 / 内部域名）？→ 删

## 反例 → 正例

### 反例：教程口吻 + 命令序列 + 决策辩证

```markdown
### 10.10 doc_studio 部署优化

之前我们一直在 ECS 上完整 build doc_studio 镜像，但发现这样会导致 CPU
飙到 95%，SSH 都断了。我们考虑了几个方案：A 升配置（贵），B 调度时间
（不稳定），C 改 Dockerfile（治标）。最后选择了 D：增量构建。

操作步骤如下：
1. 首先确认本地有 base 镜像：
   ```bash
   docker image inspect backend-doc_studio:base
   ```
2. 接下来创建 Dockerfile.fast：
   ```dockerfile
   FROM backend-doc_studio:base
   COPY services/doc_studio/ /app/
   ```
3. 然后 build：
   ```bash
   docker build -f Dockerfile.fast -t backend-doc_studio:latest .
   ```
4. 最后 up：
   ```bash
   docker compose up -d --no-build --no-deps doc_studio
   ```

不用担心 base 镜像，后续会自动维护。
```

问题：教程口吻、5+ 行命令序列、决策辩证、安抚语言全员到齐。读者下次又遇到同类坑无法快速定位根因。

### 正例：硬约束 + 五段式 + 脚本引用

```markdown
### §1.3 BuildKit 缓存 ≠ Docker 镜像层

| 编号 | 约束 | 违反后果 |
|---|---|---|
| 1.3 | BuildKit cache 不是 image layer，`docker buildx prune` 后会丢；ECS 上重新 build texlive 必然 thrash | doc_studio 永远不能在 ECS 上完整 build |

### §4.1 [2026-05-08] doc_studio 在 ECS 完整 build 导致 SSH 断连

- **Symptom**：`docker compose up -d --build doc_studio` 后 CPU 95%+，SSH banner exchange timeout，云助手 agent "未运行"。
- **Evidence**：
  - `docker buildx du` 显示 cache 已被 prune
  - `apt-get install texlive-*` 阶段卡 30 分钟以上
  - 监控：磁盘 IO 110MB/s、1500 IOPS 持续
- **Root Cause**：违反 §1.3。BuildKit cache 丢失 → 重新下载并解包 texlive（多 GB）→ 2C2G 内存不足 → swap thrash。
- **Solution**：`Dockerfile.fast` 复用 `backend-doc_studio:base`，只 COPY 代码；本地 build 完整镜像后 `docker save | scp` 到 ECS。
- **Invariant**：升级 §1.3 — **doc_studio 永远不能在 ECS 上完整 build**。

完整流程见 `backend/scripts/deploy_doc_studio_fast.sh`。
```

## 业界对照（设计依据）

| 来源 | 关键概念 | 在本 skill 的体现 |
|---|---|---|
| [Google SRE Postmortem](https://sre.google/workbook/postmortem-culture)（blameless）| Detection / Root Cause / Action Items / Lessons Learned；对事不对人；事实先于解读 | §4 五段式直接对应（Symptom = Detection，Evidence = Timeline 事实层，Root Cause = RCA，Solution = Action Items，Invariant = Lessons Learned）|
| [Google Cloud Conduct Postmortems](https://docs.cloud.google.com/architecture/framework/reliability/conduct-postmortems) | Postmortem 是活文档，要可搜索、可链接、可回溯 | §7 演进规则（编号永不变 / 不变量升级流程）|
| Atlassian PIR (Post-Incident Review) | 关注系统而非个人，关注预防而非追责 | §硬性禁止「单次事件的完整记录」「决策辩证过程」|
| AWS Well-Architected Operational Excellence | "Learn from operational failures" 但不沉湎事件叙事 | Pitfall Archive vs Postmortem 的角色边界划分（见开篇对比表）|

**和单次 Postmortem 的关键差异**：Postmortem 写"发生过什么"，Pitfall Archive 写"现在的规则是什么"。两者互补：事件复盘进 git commit / `private/`，沉淀下来的规则进 `docs/` 档案。

## 链路

- 工程约束基线：`.cursor/rules/core-principles.mdc`
- 跨项目工程经验（上抽象层）：`.cursor/skills/writing-engineering-playbook/SKILL.md`
- README 撰写：`.cursor/skills/writing-readme/SKILL.md`
- 架构文档撰写：`.cursor/skills/writing-architecture-docs/SKILL.md`
- 当前活档案实例：`docs/LOW_COST_CLOUD_DEPLOYMENT_MANUAL.md`
