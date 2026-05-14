# ScholarMind 部署 — 坑点档案与不变量

> **这不是步骤手册**。具体命令让 LLM 现场生成（apt install、mkdir、docker 安装等）。
> **这是踩坑沉淀**：把无法被 LLM 重新推导的硬约束、根因、解法集中起来，
> 喂给 LLM agent 作为部署 context，让它再走一遍部署/运维时不再犯同样的错。
>
> 编辑原则：每条记录都必须满足 — *"如果删掉，下一次必然会重新踩坑"*。
> 不满足这条的内容（架构图、安装命令、检查清单、模板 yaml）一律不写。

---

## §1 硬约束清单（违反必死）

按踩过坑的频率与代价排序。每一条都从一次真实事故里抽出来（详见 §4 档案）。

| # | 不变量 | 一句话推论 |
|---|---|---|
| **1.1** | **ECS 不继承本机 VPN / 代理 / DNS** | 任何 `raw.githubusercontent.com` / `huggingface.co` / `api.openai.com` 直连在 ECS 上随机失败 → 第三方资源必须 host 预置 + volume 注入；OpenAI 必经 Cloudflare AI Gateway + 应用层 provider 熔断 |
| **1.2** | **2C2G 不能并发** | 不能"旧容器 + build"同跑（必先 `stop`）；不能并行 build 多服务（必须串行）；**doc_studio 永远不能在 ECS 上完整 build**（含 texlive，必然 thrash）|
| **1.3** | **Docker image layer ≠ BuildKit cache** | 两套独立存储。BuildKit cache 一旦丢失（prune / GC / docker 升级），rebuild **不会**从已有 image 借 layer，必然从头跑 → 重 layer 在 2C2G 必然 thrash |
| **1.4** | **`git pull` 在 ECS 上不稳** | GitHub Anycast + HTTP/2 + GnuTLS 三连击。必须 `http.version HTTP/1.1` + `fetch && merge --ff-only` 两步法；**禁用 `git pull`** |
| **1.5** | **生产端口不暴露宿主机** | compose 用 `expose:` 不用 `ports:`；公网入口全走 cloudflared tunnel。从宿主机 `curl localhost:8003` 拿不到响应是**正常**的，不是 bug |
| **1.6** | **本地 ↔ ECS 业务 env 必须一致** | 任何"本地能跑 ECS 不能"的 bug，第一步对照 §3 业务变量表逐项 diff，再开始翻日志。LLM 模型名漂移是高频陷阱 |
| **1.7** | **Shell 脚本必须 LF 行尾** | Windows IDE 编辑 `.sh` 后 CRLF，ECS 报 `bash: $'\r': command not found`。仓库根 `.gitattributes` 已强制 LF；新增脚本时不要手动改 EOL |
| **1.8** | **生产 Dockerfile 不在 build 阶段联网下任何资源** | NLTK 数据、模型权重、parser 数据全部 host 预置 + volume 注入。新增第三方库前 `grep -R "nltk.download\|hf_hub_download" venv/` 评估隐式联网 |
| **1.9** | **`docker buildx prune` 是危险操作** | 一旦清掉 texlive 那种几 GB 的层，在 ECS 上就回不来了（见 §1.3）。**禁止在 ECS 上 prune build cache**，除非已确认放弃当前 image |

---

## §2 路径与命名约定（项目特异，最少必要集）

| 用途 | 路径 / 名称 |
|---|---|
| 代码 checkout | `/opt/apps/scholarmind`（其他项目同构：`/opt/apps/<project>`）|
| 持久数据 | `/opt/data/scholarmind/storage`（由 `SM_STORAGE_ROOT` 注入容器）|
| NLTK 预置 | `/opt/data/nltk_data`（由 `SM_NLTK_DATA_ROOT` 注入，read-only）|
| Compose 文件 | `backend/docker-compose.prod.yml` |
| Prod env | `backend/.env.production`（**不入 git**）|
| 容器命名 | `scholarmind_<service>`：`api` / `doc_studio` / `deep_research` / `db` / `redis` |
| 前端公网入口 | `https://scholarmind.wh5233.me`（Vercel）|
| API 公网入口 | `https://api-scholarmind.wh5233.me`（cloudflared tunnel）|
| Tunnel upstream | `http://<service_name>:<port>`（Docker compose service name，**不是** `host.docker.internal`，那是 Docker Desktop 专属）|

---

## §3 业务关键 env：本地 = ECS 必须对齐

> 经验：曾因 `SM_LLM_MODEL_AUX` 本地是 `gpt-5-mini`、ECS 误设 `qwen-turbo`，同一问题本地触发 RAG 而 ECS 走纯 LLM 直答 — AUX 不稳定 → 路由器无法判定意图 → 跳过 RAG 链路。

| 变量 | 期望值 | 漂移后果 |
|---|---|---|
| `SM_LLM_TYPE` | `openai` | 主答模型变更 |
| `OPENAI_MODEL_NAME` | `gpt-5.2` | 主答模型变更 |
| `SM_LLM_MODEL_ANSWER` | `gpt-5.2` | 显式声明，避免静默回退 |
| `SM_LLM_MODEL_AUX` | `gpt-5-mini` | **意图识别走这个，错位会让 RAG 不触发** |
| `SM_LLM_MODEL_GRAPH` | `gpt-5-mini` | 图谱抽取 |
| `SM_LLM_MODEL_SUMMARY` | `gpt-5-mini` | 对话摘要 |
| `DASHSCOPE_MODEL_NAME` | `qwen3-max` | 兜底模型；禁止 `qwen-plus` / `qwen-turbo` |
| `SM_DASHSCOPE_RERANK_MODEL` | `qwen3-rerank` | 精排 |
| `SM_EMBEDDER_TYPE` | `dashscope` | embedding provider |
| `SM_EMBEDDING_MODEL` | `text-embedding-v3` | 1024 维 |
| `SM_RERANKER_TYPE` | `dashscope` | rerank provider |
| `SM_VECTOR_STORE` | `pgvector` | 不再用 ES |
| `SM_RAG_TOPK` | `8` | 受 MIN=4/MAX=8 夹紧 |
| `SM_RETRIEVE_PAGE_SIZE` | `8` | 与 topK 同步 |
| `SM_PARSER_ORDER` | **不设置** | 显式留空才能让代码默认 `llamaparse,unstructured_api,pymupdf` 生效 |
| `ENABLE_WEB_SEARCH` | `true` | DeepResearch / Doc Studio 联网搜索入口；缺失会让深度调研在 search 阶段 fail-fast |
| `WEB_SEARCH_PROVIDER` | `tavily` | 当前生产搜索 provider |
| `WEB_SEARCH_API_KEY` / `TAVILY_API_KEY` | 至少一个有值 | DeepResearch 外部调研必需；本地有、ECS 缺会导致演示期才暴露 |

**全局禁用名单**（不允许出现在任何 env / 代码 / 前端选择器）：
- `qwen-plus`、`qwen-turbo` — 输出不稳定

**维护原则**：
- 业务变量在 `backend/.env.production.example` 显式列全（不依赖代码默认）
- 改业务变量的发布顺序：**`.env.production.example`（入仓库）→ 同步本地 `.env` → ssh ECS 改 `/opt/apps/scholarmind/backend/.env.production` → `up -d --no-build --no-deps <service>` 重建目标容器**

---

## §4 坑点档案（按事件时间倒序）

### 4.1 [2026-05-11] BuildKit cache 丢失 → doc_studio rebuild 死锁

- **症状**：`compose build doc_studio` 卡在 layer 4 `apt-get install texlive-*` 数小时；监控显示磁盘 BPS / IOPS 持续打满云盘 baseline（~110 MB/s / ~1500 IOPS），网络流量归零，CPU 高位；SSH 失联但 ping 通。
- **判别证据**：`docker buildx du | head -30` 全是 KB 级条目（无 GB 级）→ BuildKit cache 已不含 texlive 层；同时 `docker images | grep doc_studio` 看到老镜像还在且含完整 texlive 层。
- **根因**：§1.3。BuildKit cache 与 image layer 是两套独立存储；cache 丢失后 BuildKit 不会从已有 image 借 layer，必然重跑 `apt install texlive-*`；该层在 2C2G 上 = dpkg 解压 1.5GB + buildkit snapshot 写盘 → swap thrash → 云盘 IOPS 饱和 → sshd 被饿死 → 强制重启 → cache 再丢 → 死循环。
- **解法**：
  - `.py` / `shared/` 改动：`bash backend/scripts/deploy_doc_studio_fast.sh`（< 1 秒，见 §5）
  - deps / Dockerfile / 系统依赖改动：本地 build + `docker save | gzip` + scp + `docker load`（绝不在 ECS build）
  - 现场恢复：控制台强制重启 → 确认老镜像还在 → fast path 增量 build → `up -d --no-build`
- **不变量**：§1.2 / §1.3 / §1.9

### 4.2 [2026-05-09] ECS 直连 OpenAI 不稳 → 单请求耗时数分钟

- **症状**：浏览器 `Network Error`；后端日志 `LLM candidate failed, trying fallback: openai/gpt-5.2` 把 OpenAI 11 个候选挨个 timeout，9-11 分钟后才轮到 DashScope。
- **判别证据**：本地（带 VPN）能调通；ECS 上 `curl -m 10 https://api.openai.com/v1/models` 直接 timeout。
- **根因**：§1.1（ECS 国内出口到 OpenAI 不稳）+ 应用层 fallback 是"模型级"而非"provider 级"，同一 provider 所有模型挨个 timeout 才换 provider。
- **解法**：
  - **网络层**：`OPENAI_BASE_URL=https://gateway.ai.cloudflare.com/v1/<account>/<gateway>/openai`（Cloudflare AI Gateway 免费版，不开 Authenticated / Cache / Retry — 应用层已处理）
  - **应用层**：三个服务的 `llm_client.py` 加 `_is_provider_connectivity_error` + `skipped_providers` 短路集合，识别 timeout / 502 / 503 / 504 / 连接错误后**整个 provider 在本请求内拉黑**（已落地 commit `a2847b0`）
- **不变量**：§1.1

### 4.3 [2026-05-10] 并行 build → swap 死锁 → SSH 失联

- **症状**：`docker compose up -d --force-recreate scholarmind_api doc_studio deep_research` 后 CPU 持续 95%+；SSH banner exchange timeout；云助手 agent "未运行"；VNC 输入卡死。
- **判别证据**：旧三服务仍在运行（占 ~1GB 内存），新 build 同时跑（dockerd + 多个 buildkitd）→ 2GB 内存被瓜分进 swap。
- **根因**：§1.2。`--force-recreate` 隐含的语义是"先 build 再切容器"，2C2G 同时承载旧服务 + 多个 build 必崩。
- **解法**：固化"**停 → 串行 build → 起**"三步法：
  1. `compose stop scholarmind_api doc_studio deep_research`（保留 db / redis / cloudflared）
  2. **逐个**`compose build --pull=false <service>`，每个跑完再下一个
  3. `compose up -d --no-build --no-deps <services>`
  - 现场恢复：控制台**普通**重启（不是强制重启，build 进度无所谓反正会重做）→ 按上述三步重做
- **不变量**：§1.2

### 4.4 [2026-05-07] NLTK 运行时下载 → uvicorn 不启动 → unhealthy

- **症状**：`scholarmind_api` 容器 healthcheck 永远 Connection refused；日志卡在 `[nltk_data] Downloading package punkt_tab to /usr/local/nltk_data...`。
- **根因**：§1.1 / §1.8。`llama-index-core` 初始化分词器时静默 `nltk.download('punkt_tab' / 'stopwords')`，目标 `raw.githubusercontent.com` 在 ECS 不可达 → 阻塞 uvicorn 启动 → 容器 unhealthy。
- **解法**：
  - **首次部署/新 ECS 必做**：`bash backend/scripts/prepare_nltk_data.sh /opt/data/nltk_data`（通过 jsDelivr → ghproxy 多镜像 fallback 拉取 `punkt` / `punkt_tab` / `stopwords` / `wordnet`）
  - compose 已声明挂载 `${SM_NLTK_DATA_ROOT:-/opt/data/nltk_data}:/usr/local/nltk_data:ro`，容器启动直接读 host 数据
  - 新增第三方库前 `grep -R "nltk.download\|hf_hub_download" venv/` 评估隐式联网
- **不变量**：§1.1 / §1.8

### 4.5 [2026-05-08] git pull → HTTP/2 framing / GnuTLS 错误

- **症状**：`git pull origin main` 报以下任一：
  - `RPC failed; curl 16 Error in the HTTP2 framing layer`
  - `GnuTLS recv error (-110): The TLS connection was non-properly terminated`
  - `Failed to connect to github.com port 443 after 129273 ms: Connection timed out`
- **根因**：§1.4。GitHub Anycast 在国内出口抖动 + HTTP/2 多路复用对抖动不耐受 + `git pull` 等价 `fetch + merge` 两次联网，第二次容易撞抖动窗口。
- **解法**（一次性配置，每台 ECS 配一次永久生效）：
  ```
  git config --global http.version HTTP/1.1
  git config --global http.postBuffer 524288000
  ```
  之后**统一**用 `git fetch origin && git merge --ff-only origin/main`（merge 是纯本地操作，fetch 成功就 100% 成功）。
  `git fetch` 反复失败 3 次后等 1-2 分钟再试，不在不稳态做 `--force`。
- **不变量**：§1.4

### 4.6 [早期] 本地 ↔ ECS env 漂移 → 路由行为差异

- **症状**：同一问题本地触发 RAG，ECS 走纯 LLM 直答（意图识别失败）。
- **根因**：§1.6。`SM_LLM_MODEL_AUX` 本地 `gpt-5-mini`、ECS 误设 `qwen-turbo`；AUX 不稳定 → 意图分类输出乱 → 路由器跳过 RAG。
- **解法**：以 §3 业务变量表为唯一真理源；调整 env 走"`.env.production.example` → 本地 → ECS"单向链。
- **不变量**：§1.6

### 4.7 [早期] Shell 脚本 CRLF → ECS 执行失败

- **症状**：ECS 上 `bash scripts/prepare_nltk_data.sh` 报 `bash: $'\r': command not found`。
- **根因**：§1.7。Windows IDE 默认 CRLF 行尾，git 传输到 Linux 后第一行 shebang 就跪。
- **解法**：仓库根 `.gitattributes` 已强制 `.sh` / `Dockerfile` / `*.yml` / `.env*` 为 LF；新增脚本时不要手动改 EOL。
- **不变量**：§1.7

### 4.8 [2026-05-13] DeepResearch 联网搜索 key 未进 prod env → search 阶段 fail-fast

- **症状**：生产站点触发 DeepResearch 外部调研后报 `Web search is requested but WEB_SEARCH_API_KEY/TAVILY_API_KEY/SERPER_API_KEY is missing.`；本地同类流程正常。
- **判别证据**：
  - ECS 文件层：`.env.production` 存在，但 `grep -nE "^(ENABLE_WEB_SEARCH|WEB_SEARCH_PROVIDER|WEB_SEARCH_API_KEY|TAVILY_API_KEY|SERPER_API_KEY)=" .env.production` 无匹配。
  - 容器层：`docker exec scholarmind_deep_research env | grep -E "ENABLE_WEB_SEARCH|WEB_SEARCH|TAVILY|SERPER"` 无输出。
  - 模板层：`backend/.env.example` 有 Web Search 配置段，`backend/.env.production.example` 漏写，首次部署按模板同步必然缺。
- **根因**：§1.6。代码同步不等于 env 同步；`.env.production` 不入 git，生产只会继承人工填写的变量。功能以 fail-fast 暴露缺 key 是正确的，但 `.env.production.example` 没把 `ENABLE_WEB_SEARCH` + provider key 纳入生产契约，导致演示前才触发。
- **解法**：补齐 `backend/.env.production.example` 的 Web Search 段；ECS 上更新 `/opt/apps/scholarmind/backend/.env.production` 后，用 `docker compose -f docker-compose.prod.yml --env-file .env.production up -d --no-build --no-deps deep_research` 重建目标容器，不能只依赖 `restart`。
- **不变量**：§1.6。任何 `ENABLE_*` 开关只要会在缺 key 时 fail-fast，对应 key 必须同时出现在 `backend/.env.example` 与 `backend/.env.production.example`；新增外部 API 能力必须做文件层 + 容器层双检。

---

## §5 关键工具脚本入口

具体命令直接看脚本本身；这里只列入口与触发条件。

| 脚本 | 触发条件 | 关键产出 |
|---|---|---|
| `backend/scripts/prepare_nltk_data.sh` | 首次部署 / 新 ECS / NLTK 资源缺失 | `/opt/data/nltk_data` 下 `tokenizers/punkt*` + `corpora/stopwords` + `corpora/wordnet` |
| `backend/scripts/deploy_doc_studio_fast.sh` | 改了 `services/doc_studio/**/*.py` 或 `shared/**/*.py` | `backend-doc_studio:latest` 新镜像（< 1 秒构建）+ 容器重启 |
| `backend/services/doc_studio/Dockerfile.fast` | 被 `deploy_doc_studio_fast.sh` 调用，不单独执行 | 增量 image（仅 COPY 代码，不动 texlive 层）|

**doc_studio fast path 的前置条件**：
- `backend-doc_studio:latest` 镜像必须已存在（首次部署或镜像被误删不适用，走本地 build + scp）
- 改动**只**涉及 Python 代码 + shared；任何 deps / Dockerfile / 系统依赖改动**必须**走本地 build + scp

**doc_studio 本地 build + scp 通道**（§1.2 推论的必经路径）：
1. 本地：`compose build doc_studio` → `docker save | gzip -9 > doc_studio.tar.gz` → `scp` 上去
2. ECS：`stop doc_studio` → `gunzip -c doc_studio.tar.gz | docker load` → `docker tag :latest :base`（重置 fast path 的 base）→ `up -d --no-build`
3. 架构对齐：Mac Apple Silicon 必须 `--platform linux/amd64`；Windows Docker Desktop + WSL2 默认就是 amd64；验证 `docker image inspect ... | grep Architecture` = `"amd64"`

---

## §6 信号判别速查（症状 → 哪条档案）

> 用途：拿到一个症状先反查这张表，再去 §4 看细节。LLM agent 用这张表做快速分诊。

| 你看到的信号 | 多半是 |
|---|---|
| 容器 `unhealthy`，日志卡 `[nltk_data] Downloading ...` | §4.4 |
| 容器 `unhealthy`，日志卡 `Connecting to api.openai.com...` 或 fallback 链长达分钟级 | §4.2 |
| `git pull/fetch` 报 `HTTP2 framing` / `GnuTLS` / `Connection timed out` | §4.5 |
| DeepResearch 外部调研报 `WEB_SEARCH_API_KEY/TAVILY_API_KEY/SERPER_API_KEY is missing` | §4.8 |
| `docker compose build` 卡在 `RUN apt-get install ... texlive-*` 数小时 + 磁盘 IO 打满 baseline | §4.1（**不要再误判为 4.3**）|
| CPU 95%+、SSH banner timeout、VNC 卡死、云助手报 Agent 未运行 | §4.3（旧服务没停就 build），或 §4.1（doc_studio 在 ECS build）|
| 本地能跑、ECS 不能跑，但日志看着没异常 | §4.6（先 diff §3 业务 env 表）|
| ECS 上 `bash xxx.sh` 报 `$'\r': command not found` | §4.7 |
| 宿主机 `curl localhost:<port>` 返回 000 但容器 `healthy` | §1.5（不是 bug，prod 不暴露宿主机端口）|

---

## §7 升级 / 演进规则

- **新增坑点**：必须按 §4 五段式（症状 / 判别证据 / 根因 / 解法 / 不变量）写。少一段不收。
- **新增不变量**：必须由至少一次真实事故支撑，并在 §4 增加对应档案条目。
- **删除内容**：满足 *"删掉后下一次必然重新踩坑"* 才留；不满足直接删。
- **命令/步骤**：原则上让 LLM 现场生成。只有那些"反直觉、不写下来下次必死"的命令片段才入档（如 §4.5 的 `git config`）。
