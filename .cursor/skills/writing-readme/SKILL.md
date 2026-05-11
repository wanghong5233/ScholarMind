---
name: writing-readme
description: >-
  Enforces ScholarMind public README style: concise, factual, scannable, aligned
  with standard-readme spec and the GitHub 10-second rule. Use when editing
  top-level `README.md` / `README_EN.md`, when the user asks to 写 / 同步 /
  修改 / 更新 README, or complains the README has 口水话 / 啰嗦 / 解释性内容 /
  内部业务字段 / 不像专业 GitHub 项目 / 风格不一致.
---

# Writing ScholarMind READMEs

## 一句话准则

**README 是项目卡片，不是实现手册。读者用 10 秒决定是否继续看；超出 10 秒还没回答"这是什么 / 我为什么要关心 / 怎么试一下"就是失败。**

## 角色边界（决定写什么 / 不写什么）

| 文档 | 受众 | 内容粒度 | 链接去向 |
|---|---|---|---|
| 根 `README.md` | 公开访客、招聘方、用户 | 项目愿景 / 架构总览 / 三模块概述 / demo 入口 | demo、`docs/`、外部主页 |
| 子目录 `<sub>/README.md` | 该子目录开发者 / 运维 | **根 README 的补集**：仅含子目录独有的开发命令、运维细节、目录约定 | 链回根 README + `docs/` |
| `docs/*.md` | 严肃读者、内部协作者 | 部署、坑点、设计 | 架构文档 |
| `docs/private/*.md` | 仅本人 | 笔记、面经、简历、archived 草稿、engineering-playbook | 不公开 |
| 架构文档 | 协作者、未来的自己 | 当前实现 + 为什么 | 见 `.cursor/skills/writing-architecture-docs` |

**子 README 补集原则**（命中即重写）：

| 子 README 误写 | 正确归宿 |
|---|---|
| 复述项目愿景 / 整体架构 / 功能特性 | 删，引根 README |
| 描述与根 README 重叠的 Tech Stack | 删 |
| 子目录独有的运维 SOP（tunnel / DB migration / watchdog）| **保留**，根 README 也不该承载 |
| 子目录独有的开发命令（`npm run dev` / `alembic upgrade`）| 保留 |
| 子目录独有的环境变量 / 目录约定 | 保留 |

**判别口诀**：删掉这段后，根 README 仍能让人启动起来吗？能 → 子 README 该写它；不能 → 它属于根 README，不进子 README。

**README 不承担**：部署细节、故障排查全集、内部 env 对齐表、SQL/Python 函数名、未发布的实验功能。

## 硬性禁止（命中即删）

| 反模式 | 判断特征 | 归宿 |
|---|---|---|
| 口水话 / 解释性铺垫 | "为了 / 这是因为 / 我们采用 / 值得一提的是" | 删 |
| 内部业务字段 | `ts_rank_cd` / `SM_RETRIEVE_PAGE_SIZE` / pydantic schema 名 | `docs/` |
| 当前未启用功能的辩解 | "生产默认不启用 / 仅开发期使用" 写进高层描述 | 删 |
| 过程时态 | "曾经使用过 X / 后来切到 Y / 这次重构了 Z" | git log / CHANGELOG |
| 内部链接出现在 README | 链接指向 `private/` 或未 tracked 路径 | 改指 `docs/` 或外部 URL |
| 重复造架构图 | 同一架构画 ASCII + mermaid + 文字 3 份 | 留一份（mermaid 优先）|
| 失效 demo 链接 / 旧域名 | demo 跳转 404 或指向已废弃子域 | 改新链接，**提交前必须人工点击**一次 |
| 工件清单 / 已删除文件列表 | "本次移除了 elasticsearch / reranker" | git log |
| 自我夸赞形容词 | "强大的 / 优雅的 / 业界领先的 / 创新的" | 删 |

## 标准章节顺序（standard-readme spec 对齐）

```text
1. Title              ← 与仓库名一致
2. One-line Tagline   ← <120 字符；package.json / setup.py description 同源
3. Badges             ← 3-5 个：CI / License / Stars / Demo / Tech
4. Demo               ← GIF/截图/Live link，二者其一必备
5. Background         ← 一段话：解决什么问题，给谁用
6. Features           ← 5-8 个 bullet，可扫描
7. Architecture       ← mermaid 图 + 分层职责表（一图代千言）
8. Quick Start        ← 3 步以内能跑起来；命令必须真实可复制
9. Tech Stack         ← 表格：前端 / 后端 / 数据 / 基础设施
10. Documentation     ← 链接到 docs/ 子文档
11. Contributing      ← 必备（即使只是一行）
12. License           ← 必备
```

**100 行以上的 README 必须有 TOC**（standard-readme 硬要求）。

## 触发更新的时机

| Git 变化 | 是否更新 README | 改哪 |
|---|---|---|
| 服务增删（compose service 增减） | 必改 | Architecture / Tech Stack |
| 核心依赖切换（如 ES → pgvector） | 必改 | Architecture / Background |
| 外部 URL 改变（demo / docs） | 必改 | Header / Demo / Documentation |
| 内部实现优化（同一职责换库） | 不改 | 记在 `docs/CHANGELOG` |
| 部署细节调整 | 不改 | `docs/` 部署手册 |
| Bug 修复 | 不改 | git commit message |
| 单纯重命名变量 | 不改 | git log |

**判别原则**：变化是否影响"外部读者对项目的第一印象 / 决定是否上手"。是 → 改 README；否 → 不改。

## 写作微观规范

- 中文 README 用中文正文，技术名词 / 标识符保留英文（不翻译 `pgvector` 为"PG向量"）
- 现在时陈述：❌"我们决定采用 X" → ✅"采用 X"
- 段落 ≤ 3 行；超过改表格 / 列表 / mermaid
- 链接必须可点：相对路径用 `./docs/xxx.md`；外链带 https://；**提交前批量点验**
- Badges 来自 `shields.io`，颜色不超过 3 种
- mermaid 图节点数 ≤ 12；超过拆分图
- 双语 README：`README.md`（中文主）+ `README_EN.md`（英文版）；**两份内容必须严格对齐**，同步更新

## 自检清单（提交前必过）

### 通用
- [ ] 删掉这行，10 秒读者会漏什么核心事实？漏不掉 → 删
- [ ] 任何业务字段名、内部 env 名、内部函数名出现在公开 README？→ 删
- [ ] 所有相对路径都指向 git tracked 文件？（`docs/` ✅、`docs/private/` ❌、`notes/` 已废弃 ❌）
- [ ] 所有外部链接今天还能打开？（demo / docs / 主页）
- [ ] 有无形容词式自夸（"强大 / 优雅 / 领先"）？→ 删

### 根 README 专属
- [ ] 中文版改了，英文版同步了吗？反之亦然
- [ ] 架构图与当前 `docker-compose.prod.yml` 的服务清单一致？

### 子 README 专属（`backend/README.md` / `frontend/README.md` / `<service>/README.md`）
- [ ] 这段如果删掉，根 README 仍能让人启动起来吗？能 → 该写它；不能 → 它属于根 README
- [ ] 是否复述了根 README 的项目愿景 / 架构图 / Features？→ 删
- [ ] 是否含子目录独有的开发命令 / 运维 SOP / 环境变量？→ 这才是子 README 的本职
- [ ] 文档开头是否明确写"项目入口见根 [`README.md`](../README.md)"，避免读者误以为这是项目主入口？

## 反例 → 正例

### 反例 1：口水话 + 内部业务字段

```markdown
### RAG 检索

为了提供更准确的语义检索能力，ScholarMind 采用了 BM25 + 向量混合检索的方案。
具体来说，BM25 通过 PostgreSQL 内置的 `ts_rank_cd` 函数实现全文检索，向量
检索基于 pgvector 的 cosine 距离。值得一提的是，生产部署默认不启用本地
Reranker，而是走 DashScope 云端重排。
```

问题：`ts_rank_cd` / `cosine` 是实现细节，"为了 / 具体来说 / 值得一提的是" 是口水，"生产默认不启用" 是内部决策细节。

### 正例 1

```markdown
### RAG 检索

BM25 + 向量混合检索（PostgreSQL pgvector），DashScope 云端重排。
```

### 反例 2：服务表 + 自我夸赞

```markdown
ScholarMind 包含强大的 6 个微服务：
- API Gateway：业界领先的高性能网关
- DocStudio：优雅的文档协作平台
- DeepResearch：创新的研究编排引擎
- Reranker：精准的重排服务（已下线）
- MinerU：先进的 PDF 解析（已下线）
- Grobid：成熟的文献解析（已下线）
```

问题：形容词堆砌；已下线服务出现在公开介绍里。

### 正例 2

| 服务 | 端口 | 职责 |
|---|---|---|
| `scholarmind_api` | 8000 | 鉴权、会话、RAG 编排 |
| `doc_studio` | 8003 | 工作区文件、Agent 编辑 |
| `deep_research` | 8002 | 研究计划、报告生成 |

（已下线服务归 git log，不进 README。）

### 反例 3：失效链接 + 内部目录

```markdown
> Demo: https://demo-scholarmind.wh5233.me （旧子域，404）
>
> 云端部署见 [notes/LOW_COST_CLOUD_DEPLOYMENT_MANUAL.md](./notes/LOW_COST_CLOUD_DEPLOYMENT_MANUAL.md)
```

问题：旧 demo 子域已废弃；`notes/` 是 git ignored 内部目录，链接对公开访客 404。

### 正例 3

```markdown
> Demo: https://scholarmind.wh5233.me/demo
>
> 云端部署见 [docs/LOW_COST_CLOUD_DEPLOYMENT_MANUAL.md](./docs/LOW_COST_CLOUD_DEPLOYMENT_MANUAL.md)
```

## 业界对照（设计依据）

| 来源 | 关键约束 | 在本 skill 的体现 |
|---|---|---|
| [standard-readme spec](https://github.com/RichardLitt/standard-readme/blob/main/spec.md)（RichardLitt）| Title / Short Description / Contributing / License 必备，章节顺序固定，>100 行需 TOC | 见 §标准章节顺序、§硬性禁止「失效链接」|
| GitHub README 10-second rule | 读者 10 秒内必须能答 What / Why / How try it | §一句话准则、§标准章节顺序前 4 节 |
| [awesome-readme](https://github.com/matiassingers/awesome-readme) hall-of-fame | Logo + Tagline + Badges + Demo GIF + 一句话 pitch + 三步 Quick Start | §标准章节顺序 1-8 |
| Stripe / Vercel / Anthropic 公司项目 README 风格 | 无形容词自夸、表格密度高、外链点验严格 | §写作微观规范、§硬性禁止「自我夸赞形容词」|

## 链路

- 工程约束基线：`.cursor/rules/core-principles.mdc`
- 架构文档撰写：`.cursor/skills/writing-architecture-docs/SKILL.md`
- 部署 / 坑点档案撰写：`.cursor/skills/writing-pitfall-archive/SKILL.md`
- 跨项目工程经验：`.cursor/skills/writing-engineering-playbook/SKILL.md`
