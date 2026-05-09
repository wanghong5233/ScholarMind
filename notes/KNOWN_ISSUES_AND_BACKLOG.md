# ScholarMind Known Issues & Backlog

记录已知未修的问题、根因初判、候选方案与优先级。每次有空一次性消化一批，
减少线上小修小补带来的反复部署。新加问题在表末追加；修完移到 §99 归档。

## 索引

| ID | 标题 | 优先级 | 触发条件 |
|---|---|---|---|
| KB-01 | 本地上传查重仅用 file_hash | 已修(待云端验证) | 用户报告"同一篇又出现一条" ≥ 2 起 |
| KB-02 | DOI / arXiv ID 字符串未规范化 | 已修(待云端验证) | 与 KB-01 同发，或日志见同 DOI 多种写法 |
| KB-03 | 同一上传请求内重复未前置去重 | 低 | 当 batch 上传明显变慢 |
| KB-04 | 文档状态仍走 3s 前端轮询 | 低 | 解析时长普遍 > 30s 或用户感知滞后 |
| KB-05 | doc_studio 镜像 7.6 GB | 低 | 磁盘可用 < 20% 或共用 ECS 部署新项目 |
| KB-06 | 本次"重复文件提示"待生产验证 | 高 | 下次部署后立即验证 |

## 状态更新（2026-05-09）

- KB-01（本地上传仅 file_hash 去重）已按根因修复：
  - 在 `ParseIndexHandler` 元数据抽取后增加二次去重（semantic_scholar_id / doi / arXiv id / file_hash）。
  - 命中重复时执行“保留一条、删除另一条”的收敛策略，避免同一篇论文留下两条文档行。
- KB-02（DOI / arXiv ID 未规范化）已按根因修复：
  - 新增统一规范化模块 `backend/app/service/document_identity.py`。
  - `DocumentCreate` 入参写入前规范化（doi / semantic_scholar_id / source_url）。
  - 文档去重逻辑改为按规范化值匹配，不再依赖原始字符串精确相等。
- 当前状态：本地静态检查 + 单元测试通过，待云端部署后按 KB-06 验证清单回归。

## KB-01 本地上传查重仅用 file_hash

**现状**：`LocalUploadHandler.run()` 在 KB 内按 SHA-256 查重，命中即跳过。
未在解析后回填 DOI / arXiv id / semantic_scholar_id 维度做二次查重。

```29:35:backend/app/service/job_handler/local_upload_handler.py
                dup = find_document_by_file_hash(db, kb_id, f_meta["sha256"])
                if dup:
                    result.details.append({
                        "filename": f_meta.get("original_name"),
                        "status": "duplicate",
                        "doc_id": dup.id,
                    })
```

**影响**：同一篇论文从不同来源拿到的 PDF（arXiv v1 vs v2 vs 出版社终稿、
某些 PDF 阅读器会改写元数据导致 hash 飘移）→ 在 KB 内被当成两篇不同
论文入库，污染检索结果。

**候选方案**：

| 方案 | 改动面 | 副作用 |
|---|---|---|
| A | `ParseIndexHandler` 解析完成、拿到 DOI/arXiv id 后再调用一次 `_find_duplicate_document`，命中则把新行 chunk 合并到旧行并删除新行 | 中：要保证 chunks reindex 幂等、注意已建好的 pgvector 行 |
| B | 上传时先按"文件名 + 前 N 字节摘要"粗筛，命中弹 UI 二次确认 | 小：粗筛漏判率高，体验一般 |
| C | 不做，由用户在 KB 列表手动合并 | 0：体验差 |

**建议**：先 A。

## KB-02 DOI / arXiv ID 字符串未规范化

**现状**：`Document.doi` / `Document.semantic_scholar_id` 存的是原始值。
`_find_duplicate_document` 用字符串精确匹配三键，未做大小写、URL 前缀、
arXiv 版本号的归一化。

**影响**：在线导入同一篇论文从 arXiv 与 Semantic Scholar 两个 provider
返回时，DOI 一边带 `https://doi.org/` 一边裸值，或 arXiv id 一边
`2310.06825` 一边 `2310.06825v2` → 视为不同。

**候选方案**：

| 方案 | 改动面 |
|---|---|
| A | `schemas/document.py` validator：lower、strip prefix、strip arXiv 版本，写入前规范化 |
| B | DB 加 generated column 存规范化值，唯一索引建在它上面 |

**建议**：先 A，足够；除非数据量上来后才考虑 B。

## KB-03 同一上传请求内重复未前置去重

**现状**：前端逐文件串行 POST `/upload`。如果用户一次选 N 个文件其中
两个 hash 相同，第二个的临时盘落地 + hash 计算仍会跑。后端 handler
循环到第二个时才发现重复。

**影响**：低；浪费几百 ms IO。

**候选方案**：前端在 `submit()` 前用 `crypto.subtle.digest('SHA-256', ...)`
预算 hash 去重；或不做。

**建议**：暂不做。

## KB-04 文档状态仍走 3s 前端轮询

**现状**：`frontend/src/pages/repository/index.tsx` 用 `useEffect + setInterval`，
当 `documents` 中存在 `pending` / `parsing` 行时每 3s 拉一次 `listDocuments`，
全部 ready 后自动停止。

**影响**：每个状态变化最多滞后 3s。Cloudflare Tunnel 走 HTTP/2，
对 SSE 友好；FastAPI 原生支持 `EventSourceResponse`。

**候选方案**：

| 方案 | 改动面 |
|---|---|
| A | 后端新增 `GET /api/jobs/stream?kb_id=` SSE 端点，每次 job 状态变更推一帧；前端订阅，轮询保留为 fallback |
| B | 维持轮询，但拉取频率随 inflight 数下降而退避（3s → 5s → 10s） |
| C | 维持现状 |

**建议**：在用户反馈或解析时长拉长后再上 A；当前 3s 体感可接受。

## KB-05 doc_studio 镜像 7.6 GB

**现状**：`backend/services/doc_studio/Dockerfile` 装完整 `texlive-base +
texlive-latex-base + texlive-latex-recommended + texlive-latex-extra +
texlive-bibtex-extra + biber + texlive-xetex + texlive-fonts-extra` 等。
镜像 7.6 GB / content 2.21 GB，是 ECS 上最大的单镜像。

**影响**：

- 占用 ECS 40 GB 磁盘的近 1/5
- 冷启动 / 重新拉取耗时长
- 与 ScriptLens 等第二个项目共用同一 ECS 时磁盘紧张

**候选方案**：

| 方案 | 改动面 | 收益 |
|---|---|---|
| A | 删 `texlive-bibtex-extra + biber + texlive-fonts-extra`，仅保留 IEEE/ACM 类英文模板必需包；中文/复杂参考文献模板按需再加 | 小 | 估降 3-4 GB |
| B | 改用 [tectonic](https://tectonic-typesetting.github.io/)（按需下载宏包），镜像本体很小 | 中 | 镜像降到 ~1 GB，首次编译 +10s |
| C | 维持现状 | 0 | 0 |

**建议**：当 ECS 磁盘 < 20% 或第二个项目要落地时启动 A，再不够上 B。

## KB-06 本次"重复文件提示"待生产验证

**现状**：本轮提交在 `frontend/src/pages/repository/components/upload.tsx`
引入 `api.job.waitForJobCompletion + extractJobDetails`，按文件统计
`ok / duplicate / failed / pending`，在结束时弹聚合 message。

**影响**：未在生产环境跑过；后端 detail 字段命名（`status: "duplicate"`）
若有边角差异需要对齐。

**待办**：

1. 部署后将同一 PDF 上传两次，第二次应弹 `1 篇已存在跳过`
2. 同时上传 3 个文件其中 2 个相同，应弹 `2 篇新增，1 篇已存在跳过`
3. 上传一个解析慢的大 PDF，确认 15s 内拿不到终态时弹 `仍在处理`
   而不是 `失败`

## §99 已修复 / 已废弃

（移到这里时附 commit hash + 修复日期）

- 2026-05-09 `cancelRepeat` 默认全局开启 → 改为按 HTTP method 智能判断，
  修复"删除文档后列表要等下一轮 3s 轮询才更新"。详见 commit。
- 2026-05-09 全局 axios `loading: true` 导致 GET / 轮询触发全屏蒙层闪烁
  → 改为按 HTTP method 判断（GET/HEAD 不弹）。
- 2026-05-09 本地上传 `Upload.Dragger` 缺少 `multiple` 属性 → 已加；
  `maxCount` 由 10 提升到 50，单文件 50 MB。
- 2026-05-04 scholarmind_api 镜像由 11.7 GB 瘦身到 1.71 GB（删 torch /
  sentence-transformers / unstructured 等 cold-path 依赖；保留 LocalBgeEmbedder
  作为未来扩展点）。详见 `LOW_COST_CLOUD_DEPLOYMENT_MANUAL.md` §10.7。
