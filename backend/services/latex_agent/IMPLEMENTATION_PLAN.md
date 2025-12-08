# LaTeX Agent 实施计划

> **当前阶段**：Phase 0 完成 - 框架搭建完成，核心功能待实现  
> **最后更新**：2025-01-17

---

## 一、阶段划分说明

- **Phase 0**：框架搭建（✅ 已完成）
  - Agent 核心架构、工具系统框架、RL 训练框架、数据结构定义

- **Phase 1-2**：功能完善（⏳ 进行中）
  - Phase 1：核心功能实现（LLM 客户端、核心工具、工作区加载）
  - Phase 2：功能完善（所有工具实现、反思逻辑增强）

- **Phase 3-4**：RL 训练（📅 待开始）
  - Phase 3：RL 训练准备（数据收集、模型训练）
  - Phase 4：模型部署和优化（量化、A/B 测试）

---

## 二、当前完成情况评估（Phase 0）

### 1.1 已完成部分 ✅

#### 核心框架（100%）
- ✅ Agent 核心架构（ReAct 循环、状态管理、执行流程）
- ✅ 工具系统框架（BaseTool、ToolRegistry、参数定义）
- ✅ 数据结构和 Schema（Position、FileChange、BibliographyUpdate 等）
- ✅ API 路由和依赖注入（agent_rt.py、dependencies.py）
- ✅ 配置管理（config.py）

#### RL 训练框架（100%）
- ✅ 奖励计算器（RewardCalculator）
- ✅ 训练数据收集器（TrainingDataCollector）
- ✅ 数据库模型（TrainingEpisode、TrainingMetrics）
- ✅ 训练数据 API（training_rt.py）
- ✅ Agent 集成（数据记录逻辑）

#### 工具框架（80%）
- ✅ 所有工具的基础结构（parameters_schema、execute 方法签名）
- ✅ SearchPapersTool 已实现（调用 RAG 服务）
- ⚠️ 其他工具均为占位实现

### 1.2 待完成部分 ⏳

#### P0 - 核心功能（阻塞系统运行）
1. **LLM 客户端实现**（100% ✅）
   - ✅ `reason_and_act()` - Tool Calling 逻辑（已实现 DashScope API 调用）
   - ✅ `generate()` - 基础文本生成（已实现）

2. **核心工具实现**（40% ⏳）
   - ✅ `AnalyzeContextTool` - 文本分析（已实现，使用 LLM API）
   - ⏳ `InsertCitationTool` - 插入引用（待实现）
   - ⏳ `UpdateBibliographyTool` - 更新参考文献（待实现）
   - ⏳ `CompileLaTeXTool` - LaTeX 编译（待实现）
   - ⏳ `BatchSearchPapersTool` - 批量检索（并行，待实现）

3. **工作区上下文加载**（100% ✅）
   - ✅ `_load_workspace_context()` - 加载文件列表、引用映射（已实现）

#### P1 - 增强功能（提升系统可用性）
4. **其他工具实现**
   - `AnalyzeDocumentTool` - 文档分析
   - `CheckCitationConsistencyTool` - 引用一致性检查
   - `CheckBibliographyTool` - 参考文献检查

5. **反思逻辑增强**
   - `_reflect()` - 更深入的错误诊断和策略调整

#### P2 - 扩展功能（优化体验）
6. **错误处理和边界情况**
7. **性能优化**
8. **单元测试和集成测试**

---

## 三、实施计划（按优先级）

### Phase 1: 核心功能实现（1-2 周）

**目标**：让系统能够运行起来，完成基本的引用添加流程

#### Week 1: LLM 客户端 + 核心工具

**Day 1-2: LLM 客户端实现** ✅
- ✅ 实现 `reason_and_act()` - DashScope Tool Calling
  - ✅ 构造 ReAct prompt（观察、工具列表、历史）
  - ✅ 调用 DashScope API（`tools` 参数）
  - ✅ 解析返回的工具调用（`tool_calls`）
  - ✅ 错误处理和重试逻辑
- ✅ 实现 `generate()` - 基础文本生成
  - ✅ 支持 DashScope 和 OpenAI 兼容 API
  - ✅ 错误处理和重试

**Day 3-4: 核心工具实现** ✅
- ✅ `AnalyzeContextTool` - 文本分析
  - ✅ 调用 LLM API 提取关键论点
  - ✅ 识别需要引用的位置
  - ✅ 返回结构化结果
- ✅ `InsertCitationTool` - 插入引用
  - ✅ 读取文件内容
  - ✅ 在指定位置插入引用标记
  - ✅ 保存文件并返回变更信息
- ✅ `UpdateBibliographyTool` - 更新参考文献
  - ✅ 生成 BibTeX 条目
  - ✅ 添加到 .bib 文件
  - ✅ 更新引用映射

**Day 5: 工作区上下文 + 批量检索** ✅
- ✅ `_load_workspace_context()` - 工作区加载
  - ✅ 加载文件列表（扫描工作区目录）
  - ✅ 加载引用映射（从数据库或文件）
  - ✅ 加载工作区配置
- ✅ `BatchSearchPapersTool` - 批量检索
  - ✅ 使用 `asyncio.gather` 并行调用
  - ✅ 合并结果并返回

#### Week 2: LaTeX 编译 + 测试

**Day 1-2: LaTeX 编译工具** ✅
- ✅ `CompileLaTeXTool` - LaTeX 编译
  - ✅ 调用 `pdflatex`/`xelatex` 命令
  - ✅ 处理 BibTeX（`bibtex` 命令）
  - ✅ 解析编译日志（错误、警告）
  - ✅ 返回编译结果和 PDF 路径

**Day 3-4: 端到端测试**
- ❌ 测试完整流程：添加引用 → 更新参考文献 → 编译验证
- ❌ 修复发现的 bug
- ❌ 完善错误处理

**Day 5: 文档和优化**
- ❌ 更新 README（使用说明）
- ❌ 代码审查和优化
- ❌ 准备 Phase 2

---

### Phase 2: 功能完善（1 周）

**目标**：完善所有工具，提升系统可用性

#### Week 3: 其他工具 + 反思增强

**Day 1-2: 其他工具实现** ✅
- ✅ `AnalyzeDocumentTool` - 文档分析
  - ✅ 解析 LaTeX 文档结构
  - ✅ 提取所有引用位置
  - ✅ 分析文档语义
- ✅ `CheckCitationConsistencyTool` - 引用一致性检查
  - ✅ 解析所有引用格式
  - ✅ 检查格式一致性
  - ✅ 返回不一致的引用列表
- ✅ `CheckBibliographyTool` - 参考文献检查
  - ✅ 提取所有 citation keys
  - ✅ 检查 .bib 文件中的条目
  - ✅ 返回缺失的引用

**Day 3-4: 反思逻辑增强** ✅
- ✅ `_reflect()` - 深入反思
  - ✅ 评估结果是否符合预期
  - ✅ 检查是否需要进一步操作
  - ✅ 决定是否调整策略
  - ✅ 生成修复建议

**Day 5: 测试和优化** ⏳（待联调）
- ⏳ 测试所有工具
- ⏳ 完善错误处理
- ⏳ 性能优化

---

### Phase 3: RL 训练准备（2-3 周）

**目标**：收集训练数据，准备 RL 训练环境

#### Week 4-5: 数据收集和准备

**Week 4: 数据收集系统完善**
- ❌ 完善训练数据收集逻辑
- ❌ 添加数据质量检查
- ❌ 实现数据导出功能（用于训练）
- ❌ 添加数据统计和可视化

**Week 5: 训练数据准备**
- ❌ 收集 1000-5000 个训练回合
- ❌ 数据清洗和标注
- ❌ 准备训练脚本（PPO）
- ❌ 准备训练环境（AutoDL）

#### Week 6: 初步训练和评估

**Day 1-3: 模型训练**
- ❌ 在 AutoDL 上部署训练环境
- ❌ 加载 Qwen-7B 基础模型
- ❌ 配置 LoRA/QLoRA 微调
- ❌ 运行 PPO 训练（1000-5000 episodes）

**Day 4-5: 模型评估**
- ❌ 评估训练后的模型性能
- ❌ 对比基线模型（Qwen-Plus API）
- ❌ 分析训练指标（奖励、完成率等）
- ❌ 准备模型量化

---

### Phase 4: 模型部署和优化（1-2 周）

**目标**：部署 RL 训练的模型，进行 A/B 测试

#### Week 7: 模型量化和部署

**Day 1-2: 模型量化**
- [ ] 使用 bitsandbytes 进行 INT4 量化
- [ ] 测试量化后模型性能（8GB 显存）
- [ ] 优化 KV Cache 和 CPU Offloading
- [ ] 验证推理延迟和准确率

**Day 3-4: 本地部署**
- [ ] 实现本地模型加载（7B INT4）
- [ ] 集成到 LLMClient（支持本地模型和 API）
- [ ] 添加模型切换逻辑（A/B 测试）
- [ ] 性能测试和优化

**Day 5: A/B 测试准备**
- [ ] 实现 A/B 测试框架
- [ ] 添加指标收集（完成率、延迟、成本）
- [ ] 准备对比实验

#### Week 8: A/B 测试和优化

**Day 1-3: A/B 测试**
- [ ] 运行 A/B 测试（微调模型 vs API）
- [ ] 收集用户反馈
- [ ] 分析测试结果

**Day 4-5: 优化和总结**
- [ ] 根据测试结果优化模型
- [ ] 文档化实验结果
- [ ] 准备面试材料

---

## 四、技术债务和风险

### 3.1 技术债务

1. **LLM 客户端错误处理**
   - 当前：基础错误处理
   - 需要：重试逻辑、超时处理、降级策略

2. **工具实现的完整性**
   - 当前：部分工具是占位实现
   - 需要：完善所有工具的实际逻辑

3. **工作区文件管理**
   - 当前：未实现文件操作
   - 需要：文件读写、版本控制、并发安全

4. **LaTeX 编译**
   - 当前：未实现
   - 需要：编译命令调用、日志解析、错误定位

### 3.2 风险点

1. **LLM API 调用失败**
   - 风险：API 不可用或限流
   - 缓解：重试逻辑、降级策略、本地模型备选

2. **LaTeX 编译环境**
   - 风险：Docker 镜像缺少 LaTeX 工具链
   - 缓解：更新 Dockerfile，添加 LaTeX 依赖

3. **8GB 显存限制**
   - 风险：7B INT4 模型可能无法稳定运行
   - 缓解：优化 KV Cache、CPU Offloading、备选 3B 模型

4. **训练数据质量**
   - 风险：数据不足或质量差
   - 缓解：数据质量检查、人工标注、合成数据

---

## 五、验收标准

### 5.1 Phase 0 验收标准（已完成 ✅）

- [x] Agent 核心架构搭建完成
- [x] 工具系统框架搭建完成
- [x] RL 训练框架搭建完成
- [x] 数据结构和 Schema 定义完成

### 5.2 Phase 1 验收标准（核心功能实现）

- [ ] LLM 客户端能够成功调用 DashScope API
- [ ] Agent 能够完成基本的引用添加流程（分析 → 检索 → 插入 → 更新）
- [ ] LaTeX 编译工具能够成功编译文档
- [ ] 系统能够处理基本的错误情况

### 5.3 Phase 2 验收标准（功能完善）

- [ ] 所有工具都有实际实现（非占位）
- [ ] Agent 能够完成复杂任务（批量添加引用、检查引用）
- [ ] 反思逻辑能够发现并修复错误

### 5.4 Phase 3 验收标准（RL 训练准备）

- [ ] 收集至少 1000 个高质量训练回合
- [ ] 成功完成模型训练（PPO）
- [ ] 训练后模型性能提升（对比基线）

### 5.5 Phase 4 验收标准（模型部署）

- [ ] 7B INT4 模型在 8GB 显存上稳定运行
- [ ] A/B 测试显示微调模型优于或接近 API 模型
- [ ] 系统能够自动切换模型（A/B 测试）

---

## 六、资源需求

### 6.1 开发资源

- **时间**：6-8 周（Phase 1-4）
- **人力**：1 人（全栈开发）
- **硬件**：开发机器（8GB 显存笔记本）

### 6.2 训练资源（Phase 3-4）

- **GPU**：AutoDL 云平台（A100 40GB 或 2x RTX 3090）
- **存储**：训练数据存储（PostgreSQL）
- **成本**：训练约 2-4 小时，成本可控

### 6.3 部署资源（Phase 4）

- **本地推理**：8GB 显存笔记本
- **模型存储**：~4GB（INT4 量化）
- **API 服务**：DashScope API（按需调用）

---

## 七、下一步行动

### 立即开始（本周）

1. **实现 LLM 客户端的 Tool Calling**
   - 优先级：P0
   - 预计时间：2 天
   - 依赖：DashScope API 文档

2. **实现核心工具（AnalyzeContextTool、InsertCitationTool）**
   - 优先级：P0
   - 预计时间：2 天
   - 依赖：LLM 客户端完成

3. **实现工作区上下文加载**
   - 优先级：P0
   - 预计时间：1 天
   - 依赖：文件系统操作

### 本周目标

- ✅ LLM 客户端能够成功调用 API
- ✅ Agent 能够完成一个简单的引用添加流程
- ✅ 系统能够运行起来（即使功能不完整）

---

## 八、进度跟踪

### 8.1 当前进度

- **Phase 0 - 框架搭建**：100% ✅
- **Phase 1 - 核心功能**：60% ⏳（进行中）
  - ✅ LLM 客户端实现（100%）
  - ✅ 工作区上下文加载（100%）
  - ✅ AnalyzeContextTool（100%）
  - ⏳ 其他核心工具（20%）
- **Phase 2 - 功能完善**：0% ⏳
- **Phase 3 - RL 训练**：0% ⏳
- **Phase 4 - 模型部署**：0% ⏳

### 8.2 里程碑

- [x] **Milestone 0**：框架搭建完成（Phase 0）✅
- [ ] **Milestone 1**：系统能够运行（Phase 1 Week 2 结束）
- [ ] **Milestone 2**：所有工具实现完成（Phase 2 Week 3 结束）
- [ ] **Milestone 3**：训练数据准备完成（Phase 3 Week 5 结束）
- [ ] **Milestone 4**：模型训练完成（Phase 3 Week 6 结束）
- [ ] **Milestone 5**：模型部署和 A/B 测试完成（Phase 4 Week 8 结束）

---

**最后更新**：2025-01-17  
**负责人**：待定  
**当前状态**：Phase 0 完成，Phase 1 进行中

