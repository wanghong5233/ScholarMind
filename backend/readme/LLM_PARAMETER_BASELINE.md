# LLM 参数基线清单（v1）

本文件用于三服务参数治理的统一基线。目标是把“任务参数”和“模型能力参数”分开管理，避免业务代码继续散落魔法数字。

## 命名规范

- `max_output_tokens`：任务层输出预算（统一抽象名）
- `token_param`：模型 API 字段映射（`max_tokens` / `max_completion_tokens`）
- `temperature`：任务采样温度（由策略层裁剪）
- `retries`：任务默认重试次数
- `timeout_secs`：任务默认超时
- `context_window_hint`：模型上下文窗口提示值（用于预算计算）

## 参数来源分层

- **L1: 模型能力画像**：`backend/shared/llm_policy/llm_policy.v1.json` 的 `model_capabilities`
- **L2: 任务策略**：同一清单的 `task_policies`
- **L3: 环境覆盖**：各服务 `Settings` 中 policy 路径、开关与版本
- **L4: 请求级 override**：仅允许受控字段（例如 `llm_max_tokens`），并经过策略层 clamp

## 当前三服务任务基线

| task_id | 默认 token | 范围 | 默认温度 | 默认超时 |
| --- | ---: | --- | ---: | ---: |
| `app.answer` | 3072 | 256-8192 | 0.3 | 60s |
| `app.aux` | 512 | 128-2048 | 0.0 | 60s |
| `app.summary` | 256 | 128-1024 | 0.2 | 60s |
| `app.compression` | 1024 | 256-2048 | 0.0 | 60s |
| `app.rewrite` | 256 | 128-512 | 0.1 | 60s |
| `app.translate` | 256 | 128-512 | 0.0 | 60s |
| `app.hyde` | 256 | 128-1024 | 0.2 | 60s |
| `app.graph` | 512 | 128-1024 | 0.1 | 60s |
| `app.fact_extraction` | 512 | 128-1024 | 0.1 | 60s |
| `app.equation_description` | 2048 | 256-3072 | 0.2 | 60s |
| `docstudio.ask` | 1200 | 256-1600 | 0.2 | 75s |
| `docstudio.guardrail` | 520 | 256-800 | 0.2 | 75s |
| `docstudio.analysis` | 2000 | 512-2400 | 0.3 | 75s |
| `docstudio.answer_without_edit` | 800 | 256-1200 | 0.3 | 75s |
| `deepresearch.rag_summary` | 512 | 256-1024 | 0.2 | 120s |
| `deepresearch.decision` | 768 | 256-2048 | 0.2 | 120s |
| `deepresearch.report` | 2560 | 512-4096 | 0.2 | 120s |
| `deepresearch.report_section` | 1280 | 512-2048 | 0.2 | 120s |

## 治理规则

- 新增任务参数必须进入 `llm_policy.v1.json`，禁止在业务代码新增裸 `max_tokens` / `temperature` 字面量。
- 模型 API 字段映射必须通过能力画像，不得在业务流程里写模型前缀判断。
- 参数发布必须携带 `policy_version`，并可回滚到上一版本。
