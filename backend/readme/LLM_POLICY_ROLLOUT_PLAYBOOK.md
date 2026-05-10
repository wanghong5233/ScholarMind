# LLM Policy 灰度与回滚手册

适用范围：`backend/app`、`doc_studio`、`deep_research` 三服务统一参数策略发布。

## 1. 发布前检查

1. 策略变更只允许修改 `backend/shared/llm_policy/llm_policy.v1.json`（或新版本清单）。
2. 执行本地门禁：
   - `make policy-lint`
   - `make policy-eval`
   - `make policy-quality`
3. 质量红线策略文件：
   - `backend/scripts/adaptive_retrieval_quality_policy.v1.json`
   - 该文件定义了离线（FRR/FNR/accuracy）与在线（P95/legacy_ratio）红线，以及回滚档位。
4. 变更单必须说明：
   - 变更原因（质量、成本、延迟、稳定性）
   - 预期影响任务（`task_id`）
   - 观测指标阈值（FRR/FNR、P95、错误率）

## 2. 灰度节奏（固定）

- Phase A: 5%
- Phase B: 20%
- Phase C: 50%
- Phase D: 100%

每一阶段至少观察 30 分钟（或不少于 200 次请求，取更大者），达标后再进入下一阶段。

## 3. 观测指标（强约束）

- 路由质量：
  - FRR（False Retrieval Rate）不高于基线 + 0.05
  - FNR（Missed Retrieval Rate）不高于基线 + 0.05
- 性能：
  - 端到端 P95 延迟不高于基线 + 20%
- 稳定性：
  - LLM 错误率不高于基线 + 0.5%
- 审计一致性：
  - 日志/事件中 `policy_version`、`task_id`、`resolved_max_output_tokens` 字段可见

## 4. 自动回滚条件

满足任一条件立即回滚到上一策略版本：

1. 连续 5 分钟 FRR 或 FNR 超阈值
2. 连续 5 分钟 P95 超阈值
3. 错误率连续 5 分钟超阈值
4. 发现关键任务（`app.answer` / `docstudio.ask` / `deepresearch.report`）出现系统性退化

建议在每次灰度窗口末尾执行：

```bash
make policy-quality
```

若命令返回 `status=failed action=rollback`，按第 5 节立即执行回滚。

## 5. 回滚步骤（100→20→5→0）

1. 将灰度比例从当前值回退到上一档（例如 100→20）。
2. 若 10 分钟内指标仍不恢复，继续回退到 5，再到 0。
3. 将 manifest 指针切回上一 `policy_version`。
4. 记录回滚事件：时间、版本、触发指标、影响范围、恢复时间。

## 6. 发布后复盘

- 产出复盘条目：
  - 指标前后对比（质量/延迟/成本）
  - 风险样本与根因
  - 下一轮参数优化建议
- 将复盘结论写入下一版本策略变更单，禁止“口头经验”。
