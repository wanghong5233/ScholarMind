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

from core.config import settings
from utils.language import guess_language
from utils.prompt_loader import load_prompt_bundle
from metrics import record_llm_usage

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
        self.mode = "api"
        self.provider = None
        self.temperature = settings.LLM_TEMPERATURE
        self.max_tokens = settings.LLM_MAX_TOKENS
        self.client = None
        self._client_cache: Dict[str, AsyncOpenAI] = {}
        self._provider_health: Dict[str, Dict[str, Any]] = {}
        self._configure()

    def refresh_config(self) -> Dict[str, Any]:
        """Refresh config from environment and rebuild the client."""

        from config import refresh_settings

        refresh_settings()
        self._configure()
        return {
            "mode": self.mode,
            "provider": self.provider,
            "model": getattr(self, "model", None),
        }

    def _configure(self) -> None:
        """Apply current settings to the client."""

        # 模式选择：本地模型优先（未来升级路径）
        self.mode = "local" if settings.RL_MODEL_ENABLED else "api"
        self.temperature = settings.LLM_TEMPERATURE
        self.max_tokens = settings.LLM_MAX_TOKENS
        self.client = None
        self.provider = None

        if self.mode == "local":
            # 本地模型模式（未来实现）
            self.model_path = settings.RL_MODEL_PATH
            self.base_model = settings.RL_MODEL_BASE
            self.local_model = None  # TODO: 加载本地模型（transformers/vLLM）
            logger.info(
                "LLM client initialized: mode=local, base_model=%s, path=%s",
                self.base_model,
                self.model_path,
            )
            logger.warning("Local model mode enabled but not yet implemented. Falling back to API mode.")
            # 临时降级到 API 模式
            self.mode = "api"

        if self.mode == "api":
            # API 模式：优先使用 DashScope，如果没有则尝试 OpenAI
            self.api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
            self.base_url = (
                settings.DASHSCOPE_BASE_URL if settings.DASHSCOPE_API_KEY else settings.OPENAI_BASE_URL
            )
            self.model = (
                settings.DASHSCOPE_MODEL_NAME if settings.DASHSCOPE_API_KEY else settings.OPENAI_MODEL_NAME
            )
            self.provider = "DashScope" if settings.DASHSCOPE_API_KEY else "OpenAI"

            if not self.api_key:
                logger.warning("LLM API key not configured. LLM calls will fail.")
                self.client = None
            else:
                # 使用 OpenAI SDK 客户端（和主 API 服务一样）
                self.client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=settings.LLM_REQUEST_TIMEOUT,
                )
                cache_key = self._build_client_cache_key(self.provider, self.base_url)
                self._client_cache[cache_key] = self.client
                logger.info(
                    "LLM client initialized: mode=api, provider=%s, model=%s",
                    self.provider,
                    self.model,
                )

    def _build_client_cache_key(self, provider: str, base_url: str) -> str:
        """Build a cache key for provider clients."""

        return f"{provider.lower()}::{base_url}"

    @staticmethod
    def _normalize_provider_key(provider: str) -> str:
        return str(provider or "").strip().lower()

    def _get_provider_config(self, provider: str) -> Dict[str, Any]:
        """Return provider settings for the given provider."""

        normalized = provider.lower()
        if normalized == "dashscope":
            return {
                "provider": "DashScope",
                "api_key": settings.DASHSCOPE_API_KEY,
                "base_url": settings.DASHSCOPE_BASE_URL,
                "model": settings.DASHSCOPE_MODEL_NAME,
            }
        if normalized == "openai":
            return {
                "provider": "OpenAI",
                "api_key": settings.OPENAI_API_KEY,
                "base_url": settings.OPENAI_BASE_URL,
                "model": settings.OPENAI_MODEL_NAME,
            }
        raise ValueError(f"Unsupported LLM provider: {provider}")

    def _coerce_float(self, value: Any, fallback: float) -> float:
        """Coerce a value to float if possible."""

        try:
            if value is None:
                return fallback
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _coerce_int(self, value: Any, fallback: int) -> int:
        """Coerce a value to int if possible."""

        try:
            if value is None:
                return fallback
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _resolve_llm_config(
        self,
        llm_options: Optional[Dict[str, Any]],
        provider_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve LLM config based on optional overrides."""

        resolved_provider = provider_key
        if not resolved_provider:
            provider_override = (llm_options or {}).get("llm_provider")
            if provider_override and str(provider_override).lower() != "auto":
                resolved_provider = str(provider_override)
            else:
                resolved_provider = "dashscope" if settings.DASHSCOPE_API_KEY else "openai"

        config = self._get_provider_config(self._normalize_provider_key(resolved_provider))
        model_override = (llm_options or {}).get("llm_model")
        if model_override:
            config["model"] = str(model_override)

        config["temperature"] = self._coerce_float(
            (llm_options or {}).get("llm_temperature"),
            self.temperature,
        )
        config["max_tokens"] = self._coerce_int(
            (llm_options or {}).get("llm_max_tokens"),
            self.max_tokens,
        )
        return config

    def _get_available_providers(self) -> List[str]:
        providers: List[str] = []
        if settings.DASHSCOPE_API_KEY:
            providers.append("dashscope")
        if settings.OPENAI_API_KEY:
            providers.append("openai")
        return providers

    def _get_provider_candidates(self, llm_options: Optional[Dict[str, Any]]) -> List[str]:
        provider_override = (llm_options or {}).get("llm_provider")
        normalized_override = self._normalize_provider_key(provider_override or "")
        preferred = ""
        if normalized_override and normalized_override != "auto":
            preferred = normalized_override
        else:
            preferred = "dashscope" if settings.DASHSCOPE_API_KEY else "openai"

        available = self._get_available_providers()
        if preferred and preferred not in available and available:
            preferred = available[0]

        if not settings.LLM_FALLBACK_ENABLED:
            return [preferred] if preferred else available

        if provider_override and normalized_override != "auto" and not settings.LLM_FALLBACK_ALLOW_EXPLICIT_PROVIDER:
            return [preferred] if preferred else available

        candidates = [preferred] if preferred else []
        for provider in available:
            if provider not in candidates:
                candidates.append(provider)
        return candidates

    def get_health_snapshot(self) -> Dict[str, Any]:
        """Return provider health information for UI/monitoring."""

        now = time.time()
        available = self._get_available_providers()
        candidates = self._get_provider_candidates(None)
        providers: List[Dict[str, Any]] = []
        for provider in available:
            state = self._get_provider_state(provider)
            cooldown_until = float(state.get("cooldown_until") or 0.0)
            in_cooldown = now < cooldown_until
            providers.append(
                {
                    "provider": provider,
                    "available": True,
                    "in_cooldown": in_cooldown,
                    "cooldown_remaining_seconds": max(0, int(cooldown_until - now)),
                    "failures": int(state.get("failures") or 0),
                    "last_error": state.get("last_error"),
                    "last_success_at": state.get("last_success_at"),
                    "last_failure_at": state.get("last_failure_at"),
                }
            )

        return {
            "preferred_provider": candidates[0] if candidates else None,
            "available_providers": available,
            "providers": providers,
            "fallback_enabled": settings.LLM_FALLBACK_ENABLED,
            "fallback_allow_explicit_provider": settings.LLM_FALLBACK_ALLOW_EXPLICIT_PROVIDER,
            "failure_threshold": settings.LLM_HEALTH_FAILURE_THRESHOLD,
            "cooldown_seconds": settings.LLM_HEALTH_COOLDOWN_SECONDS,
            "request_timeout": settings.LLM_REQUEST_TIMEOUT,
        }

    def _get_provider_state(self, provider_key: str) -> Dict[str, Any]:
        key = self._normalize_provider_key(provider_key)
        state = self._provider_health.get(key)
        if not state:
            state = {
                "failures": 0,
                "cooldown_until": 0.0,
                "last_error": None,
                "last_failure_at": None,
                "last_success_at": None,
            }
            self._provider_health[key] = state
        return state

    def _is_provider_in_cooldown(self, provider_key: str) -> bool:
        state = self._get_provider_state(provider_key)
        return time.time() < float(state.get("cooldown_until") or 0.0)

    def _mark_provider_failure(self, provider_key: str, error: Exception) -> None:
        state = self._get_provider_state(provider_key)
        state["failures"] = int(state.get("failures") or 0) + 1
        state["last_failure_at"] = time.time()
        state["last_error"] = str(error)
        if state["failures"] >= settings.LLM_HEALTH_FAILURE_THRESHOLD:
            state["cooldown_until"] = time.time() + settings.LLM_HEALTH_COOLDOWN_SECONDS
            logger.warning(
                "LLM provider %s进入冷却期 %ss (failures=%s): %s",
                provider_key,
                settings.LLM_HEALTH_COOLDOWN_SECONDS,
                state["failures"],
                error,
            )

    def _mark_provider_success(self, provider_key: str) -> None:
        state = self._get_provider_state(provider_key)
        state["failures"] = 0
        state["cooldown_until"] = 0.0
        state["last_success_at"] = time.time()
        state["last_error"] = None

    def _estimate_cost(
        self,
        provider_key: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        provider_key = self._normalize_provider_key(provider_key)
        config = settings.LLM_COST_CONFIG or {}
        provider_config = config.get(provider_key) or config.get(provider_key.lower()) or {}
        model_config = provider_config.get(model) or provider_config.get("default") or {}
        input_rate = float(
            model_config.get("input", settings.LLM_COST_PER_1K_INPUT_TOKENS)
        )
        output_rate = float(
            model_config.get("output", settings.LLM_COST_PER_1K_OUTPUT_TOKENS)
        )
        return (prompt_tokens / 1000.0) * input_rate + (completion_tokens / 1000.0) * output_rate

    @staticmethod
    def _extract_usage(usage: Any) -> Dict[str, int]:
        if not usage:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if isinstance(usage, dict):
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
            return {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens))
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _get_client(self, provider: str, api_key: str, base_url: str) -> AsyncOpenAI:
        """Get or create a client for a provider."""

        cache_key = self._build_client_cache_key(provider, base_url)
        if self.provider == provider and self.client:
            return self.client
        cached = self._client_cache.get(cache_key)
        if cached:
            return cached
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.LLM_REQUEST_TIMEOUT,
        )
        self._client_cache[cache_key] = client
        return client
    
    async def generate(
        self,
        prompt: str,
        tools: Optional[list] = None,
        temperature: float = 0.3,
        llm_options: Optional[Dict[str, Any]] = None,
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
            candidates = self._get_provider_candidates(llm_options)
            if not candidates:
                raise ValueError("No available LLM providers configured")

            healthy_candidates = [p for p in candidates if not self._is_provider_in_cooldown(p)]
            if not healthy_candidates:
                healthy_candidates = candidates

            temp_value = temperature
            max_tokens_value = self.max_tokens
            if llm_options and llm_options.get("llm_temperature") is not None:
                temp_value = None  # 将在配置中读取
            if llm_options and llm_options.get("llm_max_tokens") is not None:
                max_tokens_value = None

            last_error: Optional[Exception] = None
            primary_provider = healthy_candidates[0]

            for provider_key in healthy_candidates:
                try:
                    llm_config = self._resolve_llm_config(llm_options, provider_key)
                    resolved_temp = temp_value if temp_value is not None else llm_config["temperature"]
                    resolved_max_tokens = (
                        max_tokens_value if max_tokens_value is not None else llm_config["max_tokens"]
                    )
                    response = await self._generate_api(
                        prompt,
                        tools,
                        resolved_temp,
                        llm_config,
                        resolved_max_tokens,
                    )
                    self._mark_provider_success(provider_key)
                    if provider_key != primary_provider:
                        logger.warning(
                            "LLM fallback applied: %s -> %s",
                            primary_provider,
                            provider_key,
                        )
                    return response
                except Exception as exc:
                    last_error = exc
                    self._mark_provider_failure(provider_key, exc)
                    logger.warning("LLM provider %s failed: %s", provider_key, exc)
                    continue

            if last_error:
                raise last_error
            raise ValueError("No available LLM providers configured")
    
    async def _generate_api(
        self,
        prompt: str,
        tools: Optional[list] = None,
        temperature: float = 0.3,
        llm_config: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        使用 API 生成回复（DashScope/OpenAI）
        
        使用 OpenAI SDK（和主 API 服务一样），自带重试和超时管理
        """
        config = llm_config or self._resolve_llm_config(None)
        api_key = config.get("api_key")
        base_url = config.get("base_url")
        model = config.get("model")
        provider = config.get("provider")
        if not api_key:
            raise ValueError("LLM API key not configured")
        client = self._get_client(provider, api_key, base_url)

        start_time = time.perf_counter()
        try:
            # 构建请求参数（和主 API 服务保持一致）
            kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens or self.max_tokens,
                "stream": False
            }
            
            # 如果提供了工具，添加到请求中
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            
            # 调用 OpenAI SDK（自带重试和超时管理）
            response = await client.chat.completions.create(**kwargs)

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

            usage = self._extract_usage(getattr(response, "usage", None))
            duration = time.perf_counter() - start_time
            cost = self._estimate_cost(provider, model, usage["prompt_tokens"], usage["completion_tokens"])
            record_llm_usage(
                provider=self._normalize_provider_key(provider),
                model=str(model),
                success=True,
                duration=duration,
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                total_tokens=usage["total_tokens"],
                cost=cost,
            )

            return {
                "content": content,
                "tool_calls": formatted_tool_calls,
                "usage": usage,
                "provider": provider,
                "model": model,
                "cost": cost,
            }
        
        except Exception as e:
            duration = time.perf_counter() - start_time
            record_llm_usage(
                provider=self._normalize_provider_key(provider),
                model=str(model),
                success=False,
                duration=duration,
            )
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
        history: Optional[list] = None,
        llm_options: Optional[Dict[str, Any]] = None,
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
        llm_config = self._resolve_llm_config(llm_options)
        if not llm_config.get("api_key"):
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
                "🔧 Prompt 元信息: tools=%s, temperature=%s, model=%s (%s)%s",
                len(available_tools),
                llm_config["temperature"],
                llm_config["model"],
                llm_config["provider"],
                tool_params_note,
            )
        
        # 调用 LLM Tool Calling API
        try:
            response = await self.generate(
                prompt=prompt,
                tools=available_tools,
                temperature=self.temperature,
                llm_options=llm_options,
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

        lang = guess_language(observation)
        prompts = load_prompt_bundle("doc_studio", lang)
        system_prompt = (prompts.get("react_system") or "").strip()
        history_header = (prompts.get("react_history_header") or "## 执行历史").strip()
        tools_header = (prompts.get("react_tools_header") or "## 可用工具").strip()
        observation_header = (prompts.get("react_observation_header") or "## 当前观察").strip()
        task_prompt = (prompts.get("react_task") or "").strip()

        if not system_prompt:
            system_prompt = "You are an intelligent LaTeX/Markdown editing agent."
        prompt_parts.append(system_prompt)
        
        # 添加执行历史（如果有）
        if history:
            prompt_parts.append(f"\n{history_header}")
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
        prompt_parts.append(f"\n{tools_header}")
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
        prompt_parts.append(f"\n{observation_header}")
        prompt_parts.append(observation)

        if task_prompt:
            prompt_parts.append(task_prompt)
        
        return "\n".join(prompt_parts)

