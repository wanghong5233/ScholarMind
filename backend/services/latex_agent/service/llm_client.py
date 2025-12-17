"""
LLM 客户端
用于 Agent 的推理和决策

支持两种模式：
1. API 模式：调用 DashScope/OpenAI API（当前使用）
2. 本地模型模式：加载微调的 Qwen-7B 模型（未来升级）
"""
from typing import Dict, Any, Optional, List
import logging
import os
import json
import time
from openai import AsyncOpenAI

from config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """
    LLM 客户端
    封装 LLM 调用逻辑，支持 Tool Calling
    """
    
    def __init__(self):
        """
        初始化 LLM 客户端
        
        支持两种模式：
        1. API 模式：调用远程 API（DashScope/OpenAI）
        2. 本地模型模式：加载微调的 Qwen-7B 模型（未来升级）
        """
        # 模式选择：本地模型优先（未来升级路径）
        self.mode = "local" if settings.RL_MODEL_ENABLED else "api"
        self.temperature = settings.LLM_TEMPERATURE
        self.max_tokens = settings.LLM_MAX_TOKENS
        
        if self.mode == "local":
            # 本地模型模式（未来实现）
            self.model_path = settings.RL_MODEL_PATH
            self.base_model = settings.RL_MODEL_BASE
            self.local_model = None  # TODO: 加载本地模型（transformers/vLLM）
            logger.info(f"LLM client initialized: mode=local, base_model={self.base_model}, path={self.model_path}")
            logger.warning("Local model mode enabled but not yet implemented. Falling back to API mode.")
            # 临时降级到 API 模式
            self.mode = "api"
        
        if self.mode == "api":
            # API 模式：优先使用 DashScope，如果没有则尝试 OpenAI
            self.api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
            self.base_url = settings.DASHSCOPE_BASE_URL if settings.DASHSCOPE_API_KEY else settings.OPENAI_BASE_URL
            self.model = settings.DASHSCOPE_MODEL_NAME if settings.DASHSCOPE_API_KEY else settings.OPENAI_MODEL_NAME
            
            if not self.api_key:
                logger.warning("LLM API key not configured. LLM calls will fail.")
                self.client = None
            else:
                # 使用 OpenAI SDK 客户端（和主 API 服务一样）
                self.client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url
                )
                provider = "DashScope" if settings.DASHSCOPE_API_KEY else "OpenAI"
                logger.info(f"LLM client initialized: mode=api, provider={provider}, model={self.model}")
    
    async def generate(
        self,
        prompt: str,
        tools: Optional[list] = None,
        temperature: float = 0.3
    ) -> Dict[str, Any]:
        """
        生成回复（支持 API 和本地模型两种模式）
        
        Args:
            prompt: 提示词
            tools: 可用工具列表（Tool Calling 格式）
            temperature: 温度参数
            
        Returns:
            LLM 响应，包含 content 和 tool_calls
        """
        if self.mode == "local":
            # 本地模型模式（未来实现）
            return await self._generate_local(prompt, tools, temperature)
        else:
            # API 模式（当前使用）
            return await self._generate_api(prompt, tools, temperature)
    
    async def _generate_api(
        self,
        prompt: str,
        tools: Optional[list] = None,
        temperature: float = 0.3
    ) -> Dict[str, Any]:
        """
        使用 API 生成回复（DashScope/OpenAI）
        
        使用 OpenAI SDK（和主 API 服务一样），自带重试和超时管理
        """
        if not self.client:
            raise ValueError("LLM API key not configured")
        
        try:
            # 构建请求参数（和主 API 服务保持一致）
            kwargs = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": self.max_tokens,
                "stream": False
            }
            
            # 如果提供了工具，添加到请求中
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            
            # 调用 OpenAI SDK（自带重试和超时管理）
            response = await self.client.chat.completions.create(**kwargs)
            
            # 解析响应
            if not response.choices:
                raise ValueError("No choices in LLM response")
            
            message = response.choices[0].message
            content = message.content or ""
            tool_calls = message.tool_calls
            
            # 转换 tool_calls 为标准格式
            formatted_tool_calls = None
            if tool_calls:
                formatted_tool_calls = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in tool_calls
                ]
            
            return {
                "content": content,
                "tool_calls": formatted_tool_calls
            }
        
        except Exception as e:
            logger.error(f"Error calling LLM: {e}", exc_info=True)
            raise
    
    async def _generate_local(
        self,
        prompt: str,
        tools: Optional[list] = None,
        temperature: float = 0.3
    ) -> Dict[str, Any]:
        """
        使用本地模型生成回复（未来实现）
        
        TODO: 实现本地模型推理逻辑
        - 使用 transformers 加载微调的 Qwen-7B
        - 或使用 vLLM 进行高效推理
        - 支持 Tool Calling 格式输出
        """
        raise NotImplementedError(
            "本地模型模式尚未实现。请设置 RL_MODEL_ENABLED=False 使用 API 模式，"
            "或等待本地模型推理功能开发完成。"
        )
    
    async def reason_and_act(
        self,
        observation: str,
        available_tools: list,
        history: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        推理并决定下一步行动（Tool Calling）
        
        使用 ReAct 模式构造 prompt，调用 Tool Calling API
        
        Args:
            observation: 当前观察
            available_tools: 可用工具列表（Tool Calling 格式）
            history: 执行历史（可选）
            
        Returns:
            包含 tool_name, parameters, thought 的字典
        """
        if not self.api_key:
            raise ValueError("LLM API key not configured")
        
        # 构造 ReAct prompt
        prompt = self._build_react_prompt(observation, available_tools, history)

        if settings.LOG_FULL_PROMPT:
            divider = "=" * 80
            logger.info(
                "\n%s\n📤 完整的 LLM Prompt (发送给大模型)\n%s\n%s\n%s",
                divider,
                divider,
                prompt,
                divider,
            )
            tool_params_note = ""
            if not settings.LOG_PROMPT_INCLUDE_TOOL_PARAMS:
                tool_params_note = " (工具参数详情已省略，如需查看请设置 LOG_PROMPT_INCLUDE_TOOL_PARAMS=True)"
            logger.info(
                "🔧 Prompt 元信息: tools=%s, temperature=%s, model=%s%s",
                len(available_tools),
                self.temperature,
                getattr(self, "model", "unknown"),
                tool_params_note,
            )
        
        # 调用 LLM Tool Calling API
        try:
            response = await self.generate(
                prompt=prompt,
                tools=available_tools,
                temperature=self.temperature
            )
            
            if settings.LOG_FULL_PROMPT:
                divider = "=" * 80
                try:
                    response_dump = json.dumps(response, ensure_ascii=False, indent=2)
                except TypeError:
                    response_dump = str(response)
                logger.info(
                    "\n%s\n📥 LLM 响应结果\n%s\n%s\n%s",
                    divider,
                    divider,
                    response_dump,
                    divider,
                )

            # 解析工具调用结果
            tool_calls = response.get("tool_calls")
            content = response.get("content", "")
            
            # 如果有工具调用，解析第一个工具调用
            if tool_calls and len(tool_calls) > 0:
                tool_call = tool_calls[0]
                function = tool_call.get("function", {})
                tool_name = function.get("name", "")
                arguments_str = function.get("arguments", "{}")
                
                # 解析参数 JSON
                try:
                    parameters = json.loads(arguments_str)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse tool arguments: {arguments_str}")
                    parameters = {}
                
                return {
                    "tool_name": tool_name,
                    "parameters": parameters,
                    "thought": content or f"Calling tool: {tool_name}"
                }
            
            # 如果没有工具调用，说明任务完成或需要继续思考
            # 检查 content 中是否包含完成信号
            if content and ("完成" in content or "finish" in content.lower() or "完成" in content):
                return {
                    "tool_name": "finish",
                    "parameters": {},
                    "thought": content
                }
            
            # 默认返回分析工具（让 Agent 继续分析）
            return {
                "tool_name": "analyze_context_tool",
                "parameters": {"text": observation},
                "thought": content or "Analyzing the situation..."
            }
        
        except Exception as e:
            logger.error(f"Error in reason_and_act: {e}", exc_info=True)
            # 失败时返回默认工具调用
            return {
                "tool_name": "analyze_context_tool",
                "parameters": {"text": observation},
                "thought": f"Error occurred: {str(e)}. Trying to analyze context..."
            }
    
    def _build_react_prompt(
        self,
        observation: str,
        available_tools: list,
        history: Optional[list] = None
    ) -> str:
        """
        构造 ReAct 模式的 prompt
        
        Args:
            observation: 当前观察
            available_tools: 可用工具列表
            history: 执行历史
            
        Returns:
            构造好的 prompt 字符串
        """
        prompt_parts = []
        
        # 系统提示
        prompt_parts.append("""你是一个智能 LaTeX 编辑助手 Agent（类似 Cursor），能够：
- 理解用户的各类需求（编辑、查询、建议等）
- 自主决定是否需要检索文献、编辑文件
- 灵活组合多个工具完成复杂任务
- 给出清晰的回复和操作总结

## 用户选中片段的处理

当用户在编辑器中选中了一个或多个文本片段时：
- Observation 会显示所有片段的完整内容，格式为：`@selectionX (文件名, 位置): 完整文本`
- 用户的指令（User Intent）中会用 `【片段1】`、`【片段2】` 等自然语言引用这些片段
- 你应该理解这些引用对应 Observation 中的 `@selection1`、`@selection2` 等片段
- 例如：用户说"请优化【片段1】"，你应该查看 Observation 中 `@selection1` 的完整内容
- 如果需要修改选中的内容，优先使用 `rewrite_selection_tool`（会自动使用 selection.start/end）

## 工作原则

1. **快速决策，避免过度分析**：
   - ⚠️ 不要重复调用分析工具（analyze_context_tool, analyze_document_tool）
   - 第一次分析后，应立即行动（检索/编辑/回复）

2. **根据需求决定行动**：
   - 简单问题：调用 reply_to_user_tool 直接回答
   - 需要文献支持：search_papers_tool → reply_to_user_tool
   - 需要修改文档：analyze_document_tool → insert_text_tool → reply_to_user_tool

3. **必须及时回复用户**：
   - ⚠️ 最多 2-3 个工具调用后，必须调用 `reply_to_user_tool`
   - 无论是否编辑文件，都必须回复
   - 回复应包含：已完成的操作、修改的文件、对问题的解答

4. **精确编辑**：
   - 使用 `insert_text_tool` 时，必须提供足够的上下文（3-5行）
   - 当需要替换现有选区时，优先使用 `rewrite_selection_tool`（selection.start/end 来自 Observation）
   - **建议/回答类任务：直接在 `reply_to_user_tool` 的 reply 参数中生成内容，不需要其他工具**

5. **严格的执行流程**：
   - 分析类工具（analyze_*）：最多调用 1 次
   - 检索类工具（search_*）：根据需要调用 0-1 次
   - 编辑类工具（insert_*, update_*）：根据需要调用 0-N 次
   - 回复工具（reply_to_user_tool）：必须调用 1 次（作为最后一步）

6. **没有检索结果也要继续**：
   - 如果知识库返回 0 条结果或网络超时，仍然要根据已有文本完成"优化/重写/润色"
   - 可以引用当前文档中的信息，切勿因为缺少文献而停止执行
   - 当提示"未绑定知识库"时，检索工具会自动跳过，你必须依靠现有上下文完成任务

7. **⚠️ 语言一致性要求（非常重要）**：
   - **必须严格保持文档语言的一致性**
   - 如果文档是英文论文，生成的所有内容必须使用英文，禁止在英文文档中插入中文内容
   - 如果文档是中文论文，生成的内容应使用中文（除非是英文摘要等特殊部分）
   - 在编辑文档前，先观察文档的主要语言（通过 Observation 中的文档内容判断）
   - 如果用户用中文指令修改英文文档，你仍然必须用英文生成内容
   - 如果检测到语言不一致，编辑工具会拒绝执行，导致任务失败

## 执行流程示例（严格遵守）

**场景1：简单问答（1-2步）**
1. search_papers_tool（如需文献支持）
2. reply_to_user_tool（必须）

**场景2：检查/建议/优化建议（1-2步）**
- 用户说"检查"、"建议"、"是否可以优化" → 只给建议，不修改
1. analyze_context_tool（分析内容）
2. reply_to_user_tool（给出优化建议，不要修改文件）

**场景3：明确修改文档（3-4步）**
- 用户说"帮我修改"、"请优化"、"重写" → 才修改文件
1. analyze_document_tool（分析文档结构）
2. insert_text_tool（在精确位置插入内容）
3. compile_latex_tool（验证编译，可选）
4. reply_to_user_tool（总结修改，必须）

⚠️ **重要区分**：
- "检查"、"是否可以"、"建议" → 只给建议，不修改文件，不要调用 analyze_document_tool
- "修改"、"优化"、"重写" → 才修改文件，需要调用 analyze_document_tool

⚠️ **禁止的行为**：
- ❌ 重复调用 analyze_context_tool
- ❌ 重复调用 analyze_document_tool
- ❌ 用户只是询问建议时调用 analyze_document_tool
- ❌ **建议/检查类任务调用 answer_without_edit_tool**（建议应直接写在 reply_to_user_tool 的 reply 参数中）
- ❌ 不调用 reply_to_user_tool 就结束
- ❌ 超过 3 个工具调用还不回复

当前任务：根据用户意图，选择最合适的工具执行下一步操作。""")
        
        # 添加执行历史（如果有）
        if history:
            prompt_parts.append("\n## 执行历史")
            for i, step in enumerate(history[-5:], 1):  # 只取最近5步
                step_type = step.get("type", "unknown")
                step_content = step.get("content", "")
                step_tool = step.get("tool", "")
                step_result = step.get("result", {})
                
                prompt_parts.append(f"\n步骤 {i} ({step_type}):")
                if step_tool:
                    prompt_parts.append(f"  工具: {step_tool}")
                prompt_parts.append(f"  内容: {step_content}")
                if step_result:
                    result_str = json.dumps(step_result, ensure_ascii=False, indent=2)
                    prompt_parts.append(f"  结果: {result_str}")
        
        # 添加可用工具描述
        prompt_parts.append("\n## 可用工具")
        for tool in available_tools:
            tool_name = tool.get("function", {}).get("name", "")
            tool_desc = tool.get("function", {}).get("description", "")
            tool_params = tool.get("function", {}).get("parameters", {})
            
            prompt_parts.append(f"\n- **{tool_name}**: {tool_desc}")
            
            # 根据配置决定是否输出工具参数详情
            # 注意：这只影响日志输出，不影响发送给 LLM 的实际内容（LLM 通过 tools 参数接收完整定义）
            if settings.LOG_PROMPT_INCLUDE_TOOL_PARAMS and tool_params:
                params_desc = json.dumps(tool_params, ensure_ascii=False, indent=2)
                prompt_parts.append(f"  参数: {params_desc}")
        
        # 添加当前观察
        prompt_parts.append("\n## 当前观察")
        prompt_parts.append(observation)
        
        # 添加指令
        prompt_parts.append("""
## 你的任务

根据当前观察和执行历史，选择最合适的**下一步**工具操作。

**重要规则**：
1. ⚠️ **避免过度分析**：不要重复调用 analyze_* 工具
2. ⚠️ **必须及时回复**：最多 2-3 个工具调用后，必须调用 `reply_to_user_tool`
3. **选择最合适的工具**：从可用工具列表中选择（必须是精确的工具名称）
4. **提供完整参数**：特别是 insert_text_tool 的 search_context 必须包含 3-5 行上下文
5. **任务完成标准**：
   - 简单问答：直接调用 reply_to_user_tool
   - 编辑任务：analyze_document → insert_text → reply_to_user_tool
   - 如果已经调用了 3 个以上工具，下一步必须是 reply_to_user_tool

请直接调用工具（使用 Tool Calling），不要添加额外的解释。""")
        
        return "\n".join(prompt_parts)

