"""
分析类工具
"""
from typing import Dict, Any, List
import asyncio
import json
import logging
import os

from openai import AsyncOpenAI

from .base_tool import BaseTool, ToolResult
from .workspace_utils import get_workspace_path, resolve_path_within_workspace
from .latex_utils import (
    list_workspace_files,
    collect_latex_metadata,
)

logger = logging.getLogger(__name__)


class AnalyzeContextTool(BaseTool):
    """
    分析上下文工具
    分析文本语义，提取关键论点
    
    ⚠️ 使用限制：每个任务最多调用 1 次
    """
    
    def __init__(self):
        super().__init__(
            name="analyze_context_tool",
            description=(
                "分析文本语义，提取关键论点和需要引用的位置。"
                "⚠️ 此工具仅用于初始理解，不要重复调用。"
                "分析后应立即进行下一步行动（检索/编辑/回复）。"
            )
        )
        # 定义工具参数（用于 LLM Tool Calling）
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要分析的文本内容"
                },
                "context": {
                    "type": "string",
                    "description": "上下文信息（可选）"
                }
            },
            "required": ["text"]
        }
        
        # LLM 配置（用于文本分析）- 和主 API 服务保持一致
        self.api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.model = os.getenv("DASHSCOPE_MODEL_NAME", "qwen-plus")
        
        # 使用 OpenAI SDK 客户端（和主 API 服务一样）
        if self.api_key:
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
        else:
            self.client = None
    
    async def execute(
        self,
        agent_state: Any,
        parameters: Dict[str, Any]
    ) -> ToolResult:
        """
        执行分析
        
        使用 LLM 分析文本，提取关键论点和需要引用的位置
        
        Args:
            parameters:
                - text: 要分析的文本
                - context: 上下文信息（可选）
        """
        text = parameters.get("text", "")
        context = parameters.get("context", "")
        
        if not text:
            return ToolResult(
                success=False,
                error="Text parameter is required"
            )
        
        logger.info(f"Analyzing context: {text[:100]}...")
        
        # 使用 LLM 分析文本
        try:
            analysis_result = await self._analyze_with_llm(text, context)
            return ToolResult(
                success=True,
                data=analysis_result,
                summary=f"Extracted {len(analysis_result.get('claims', []))} claims"
            )
        except Exception as e:
            logger.error(f"Error analyzing context: {e}", exc_info=True)
            # 失败时返回基础分析结果
            return ToolResult(
                success=True,
                data={
                    "claims": self._extract_simple_claims(text),
                    "suggested_citation_positions": []
                },
                summary="Basic analysis completed (LLM analysis failed)"
            )
    
    async def _analyze_with_llm(self, text: str, context: str = "") -> Dict[str, Any]:
        """
        使用 LLM 分析文本
        
        Args:
            text: 要分析的文本
            context: 上下文信息
            
        Returns:
            分析结果，包含 claims 和 suggested_citation_positions
        """
        if not self.api_key:
            # 如果没有 API key，使用简单分析
            return {
                "claims": self._extract_simple_claims(text),
                "suggested_citation_positions": []
            }
        
        # 构造分析 prompt，单独准备可选上下文以避免 f-string 中的反斜杠
        context_block = f"上下文信息：\n{context}\n\n" if context else ""
        
        prompt = f"""请分析以下文本，提取关键论点和需要引用的位置。

文本内容：
{text}

{context_block}请以 JSON 格式返回分析结果：

{{
    "claims": ["论点1", "论点2", ...],
    "suggested_citation_positions": [
        {{
            "claim": "论点",
            "position_in_text": "在文本中的位置描述"
        }}
    ]
}}

只返回 JSON，不要添加其他内容。"""
        
        try:
            if not self.client:
                raise ValueError("LLM client not configured")
            
            # 使用 OpenAI SDK（和主 API 服务一样，自带超时和重试管理）
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
                stream=False
            )
            
            content = response.choices[0].message.content or ""
            
            # 尝试解析 JSON
            try:
                # 提取 JSON 部分（可能包含 markdown 代码块）
                if "```json" in content:
                    json_start = content.find("```json") + 7
                    json_end = content.find("```", json_start)
                    content = content[json_start:json_end].strip()
                elif "```" in content:
                    json_start = content.find("```") + 3
                    json_end = content.find("```", json_start)
                    content = content[json_start:json_end].strip()
                
                analysis_result = json.loads(content)
                return {
                    "claims": analysis_result.get("claims", []),
                    "suggested_citation_positions": analysis_result.get("suggested_citation_positions", [])
                }
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse LLM response as JSON: {content}")
                # 尝试从文本中提取 claims
                return {
                    "claims": self._extract_claims_from_text(content),
                    "suggested_citation_positions": []
                }
        
        except Exception as e:
            logger.error(f"Error calling LLM for analysis: {e}", exc_info=True)
            raise
    
    def _extract_simple_claims(self, text: str) -> list:
        """
        简单提取关键论点（不使用 LLM）
        
        基于关键词和句子结构提取
        """
        # 简单的关键词提取
        keywords = []
        sentences = text.split('.')
        for sentence in sentences:
            # 提取可能的技术术语（大写字母开头的词）
            words = sentence.split()
            for word in words:
                if word and word[0].isupper() and len(word) > 3:
                    keywords.append(word)
        
        return list(set(keywords))[:5]  # 返回最多5个关键词
    
    def _extract_claims_from_text(self, text: str) -> list:
        """
        从 LLM 返回的文本中提取 claims
        """
        # 尝试提取列表项
        claims = []
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('-') or line.startswith('*') or line.startswith('•'):
                claim = line.lstrip('-*•').strip()
                if claim:
                    claims.append(claim)
            elif line and not line.startswith('{') and not line.startswith('['):
                # 可能是独立的 claim
                if len(line) > 10 and len(line) < 100:
                    claims.append(line)
        
        return claims[:10]  # 返回最多10个


class AnalyzeDocumentTool(BaseTool):
    """
    分析文档工具
    分析整个文档结构
    
    ⚠️ 使用限制：仅在需要修改文档时调用，每个任务最多调用 1 次
    """
    
    def __init__(self):
        super().__init__(
            name="analyze_document_tool",
            description=(
                "分析整个 LaTeX 文档结构（章节、引用、标记等）。"
                "⚠️ 仅在需要编辑文档时调用，用于定位插入位置。"
                "分析后应立即调用编辑工具，不要重复分析。"
            )
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件路径（可选，默认当前文档）"
                },
                "include_all_tex": {
                    "type": "boolean",
                    "description": "是否扫描工作区内所有 .tex 文件（默认 True）",
                    "default": True
                }
            },
            "required": []
        }
    
    async def execute(
        self,
        agent_state: Any,
        parameters: Dict[str, Any]
    ) -> ToolResult:
        """
        执行文档分析
        
        Args:
            parameters:
                - file_path: 文件路径（可选，默认当前文档）
        """
        try:
            workspace_path = get_workspace_path(agent_state)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        
        include_all_tex = parameters.get("include_all_tex", True)
        try:
            target_files = await self._resolve_target_files(
                workspace_path=workspace_path,
                agent_state=agent_state,
                file_path=parameters.get("file_path"),
                include_all_tex=include_all_tex
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        
        if not target_files:
            return ToolResult(
                success=False,
                error="工作区内没有可供分析的 LaTeX 文件"
            )
        
        logger.info("AnalyzeDocumentTool scanning %s files", len(target_files))
        
        try:
            metadata = await asyncio.to_thread(
                collect_latex_metadata,
                target_files,
                workspace_path
            )
        except Exception as exc:
            logger.error("AnalyzeDocumentTool failed: %s", exc, exc_info=True)
            return ToolResult(success=False, error=f"分析文档失败: {exc}")
        
        sections = metadata.get("sections", [])
        citations = metadata.get("citations", [])
        bibliography_files = metadata.get("bibliography_files", [])
        unique_citations = sorted({key for cite in citations for key in cite["keys"]})
        
        summary = (
            f"解析 {len(target_files)} 个文件，"
            f"识别 {len(sections)} 个章节、{len(unique_citations)} 个唯一引用"
        )
        
        return ToolResult(
            success=True,
            data={
                "files_analyzed": [str(path.relative_to(workspace_path)) for path in target_files],
                "sections": sections,
                "citations": citations,
                "unique_citations": unique_citations,
                "total_citations": len(citations),
                "bibliography_files": bibliography_files
            },
            summary=summary
        )
    
    async def _resolve_target_files(
        self,
        workspace_path,
        agent_state,
        file_path: str = None,
        include_all_tex: bool = True
    ) -> List:
        """确定需要分析的文件列表"""
        workspace_files = getattr(agent_state, "workspace_files", [])
        
        if file_path:
            resolved = resolve_path_within_workspace(workspace_path, file_path)
            if not resolved.exists():
                raise ValueError(f"文件不存在: {file_path}")
            return [resolved]
        
        if include_all_tex:
            tex_files = list_workspace_files(
                workspace_path,
                workspace_files,
                extensions={".tex"}
            )
            if tex_files:
                return tex_files
        
        main_file = (
            getattr(agent_state, "workspace_config", {}) or {}
        ).get("main_file") or "main.tex"
        resolved_main = resolve_path_within_workspace(workspace_path, main_file)
        if not resolved_main.exists():
            raise ValueError(f"主文件不存在: {main_file}")
        return [resolved_main]


class AnswerWithoutEditTool(BaseTool):
    """
    仅回答/建议，不修改文件
    """

    def __init__(self):
        super().__init__(
            name="answer_without_edit_tool",
            description="基于问题和当前上下文生成回答或建议，不修改任何文件。"
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用户问题或指令"
                },
                "context_text": {
                    "type": "string",
                    "description": "可选的上下文文本（如选中的片段、摘要）"
                },
                "file_path": {
                    "type": "string",
                    "description": "可选，若提供则会读取该文件内容作为上下文"
                }
            },
            "required": ["question"]
        }
        self.api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.model = os.getenv("DASHSCOPE_MODEL_NAME", "qwen-plus")
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url) if self.api_key else None

    async def execute(
        self,
        agent_state: Any,
        parameters: Dict[str, Any]
    ) -> ToolResult:
        question = parameters.get("question", "").strip()
        context_text = parameters.get("context_text")
        file_path = parameters.get("file_path")

        if not question:
            return ToolResult(success=False, error="question 参数为必填")

        if not context_text and file_path:
            try:
                workspace_path = get_workspace_path(agent_state)
                target_file = resolve_path_within_workspace(workspace_path, file_path)
                if target_file.exists():
                    context_text = await asyncio.to_thread(target_file.read_text, "utf-8")
            except Exception as exc:
                logger.warning("读取上下文文件失败: %s", exc)

        if not self.client:
            return ToolResult(success=False, error="LLM client 未配置，无法生成回答")

        prompt = (
            "你是一名专业的学术/技术写作助手，请根据用户的问题给出具体回答或修改建议。\n"
            "如果没有额外的上下文，也应基于常识和写作经验提供有价值的建议。\n\n"
            f"用户问题：{question}\n\n"
            f"上下文：\n{context_text or '（无上下文，可自由发挥）'}"
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个严谨的学术写作助手。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=800,
            )
            answer = response.choices[0].message.content if response.choices else ""
        except Exception as exc:
            logger.error("AnswerWithoutEditTool 调用 LLM 失败: %s", exc, exc_info=True)
            return ToolResult(success=False, error=f"生成回答失败: {exc}")

        return ToolResult(
            success=True,
            data={"answer": answer, "context_used": bool(context_text)},
            summary="已生成回答/建议"
        )

