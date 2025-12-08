"""
编辑类工具
"""
from typing import Dict, Any
from pathlib import Path
import asyncio
import logging

from .base_tool import BaseTool, ToolResult
from .workspace_utils import (
    get_workspace_path,
    resolve_path_within_workspace,
    ensure_parent_directory,
)

logger = logging.getLogger(__name__)


class InsertCitationTool(BaseTool):
    """
    插入引用工具
    在指定位置插入引用标记
    """
    
    def __init__(self):
        super().__init__(
            name="insert_citation_tool",
            description="在 LaTeX 文档的指定位置插入引用标记"
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件路径"
                },
                "position": {
                    "type": "object",
                    "description": "位置信息 {line: int, character: int}",
                    "properties": {
                        "line": {"type": "integer"},
                        "character": {"type": "integer"}
                    },
                    "required": ["line", "character"]
                },
                "citation_key": {
                    "type": "string",
                    "description": "引用键"
                },
                "citation_style": {
                    "type": "string",
                    "description": "引用格式（如 '\\cite{}'）",
                    "default": "\\cite{}"
                }
            },
            "required": ["file_path", "position", "citation_key"]
        }
    
    async def execute(
        self,
        agent_state: Any,
        parameters: Dict[str, Any]
    ) -> ToolResult:
        """
        执行插入引用
        
        Args:
            parameters:
                - file_path: 文件路径
                - position: 位置信息 {line: int, character: int}
                - citation_key: 引用键
                - citation_style: 引用格式（如 "\\cite{}"）
        """
        file_path = parameters.get("file_path")
        position = parameters.get("position", {})
        citation_key = parameters.get("citation_key")
        citation_style = parameters.get("citation_style", "\\cite{}")
        
        if not file_path or not position or not citation_key:
            return ToolResult(
                success=False,
                error="Missing required parameters: file_path, position, citation_key"
            )
        
        try:
            workspace_path = get_workspace_path(agent_state)
            target_file = resolve_path_within_workspace(workspace_path, file_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        
        if not target_file.exists():
            return ToolResult(success=False, error=f"文件不存在: {file_path}")
        
        citation_text = self._format_citation_text(citation_style, citation_key)
        
        try:
            insert_result = await asyncio.to_thread(
                self._insert_citation_text,
                target_file,
                position,
                citation_text
            )
        except Exception as exc:
            logger.error("插入引用失败: %s", exc, exc_info=True)
            return ToolResult(success=False, error=f"插入引用失败: {exc}")
        
        logger.info(
            "Inserted citation %s at line %s char %s in %s",
            citation_key,
            insert_result["line"],
            insert_result["character"],
            file_path
        )
        
        # 标记文件已被修改（用于生成 diff）
        if hasattr(agent_state, 'modified_files'):
            agent_state.modified_files.add(file_path)
        
        return ToolResult(
            success=True,
            data={
                "file": file_path,
                "position": {
                    "line": insert_result["line"],
                    "character": insert_result["character"]
                },
                "citation_key": citation_key,
                "inserted_content": citation_text,
                "updated_line": insert_result["updated_line"]
            },
            summary=f"已插入引用 {citation_key}"
        )
    
    def _format_citation_text(self, citation_style: str, citation_key: str) -> str:
        """根据样式渲染引用文本"""
        if "{citation_key}" in citation_style:
            return citation_style.replace("{citation_key}", citation_key)
        if "{citation}" in citation_style:
            return citation_style.replace("{citation}", citation_key)
        if "{}" in citation_style:
            return citation_style.replace("{}", f"{{{citation_key}}}", 1)
        return f"{citation_style}{{{citation_key}}}"
    
    def _insert_citation_text(
        self,
        target_file: Path,
        position: Dict[str, Any],
        citation_text: str
    ) -> Dict[str, Any]:
        """在指定行列插入引用"""
        line_index = max(int(position.get("line", 1)) - 1, 0)
        char_index = max(int(position.get("character", 0)), 0)
        
        if target_file.exists():
            with open(target_file, "r", encoding="utf-8") as file:
                content = file.read()
        else:
            content = ""
        
        lines = content.splitlines(keepends=True)
        if not lines:
            lines = [""]
        
        while len(lines) <= line_index:
            lines.append("\n")
        
        target_line = lines[line_index]
        char_index = min(char_index, len(target_line))
        updated_line = (
            target_line[:char_index]
            + citation_text
            + target_line[char_index:]
        )
        lines[line_index] = updated_line
        
        with open(target_file, "w", encoding="utf-8") as file:
            file.write("".join(lines))
        
        return {
            "line": line_index + 1,
            "character": char_index,
            "updated_line": updated_line
        }


class RewriteSelectionTool(BaseTool):
    """
    重写指定选区工具
    用于整体替换某段文本（例如摘要、段落或句子）
    """

    def __init__(self):
        super().__init__(
            name="rewrite_selection_tool",
            description="重写文件中的指定选区。适用于根据上下文替换原有内容。"
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "目标文件路径"
                },
                "start_offset": {
                    "type": "integer",
                    "description": "选区起始字符偏移（0-based）"
                },
                "end_offset": {
                    "type": "integer",
                    "description": "选区结束字符偏移（0-based，非包含）"
                },
                "replacement_text": {
                    "type": "string",
                    "description": "替换后的文本内容"
                }
            },
            "required": ["file_path", "start_offset", "end_offset", "replacement_text"]
        }

    async def execute(
        self,
        agent_state: Any,
        parameters: Dict[str, Any]
    ) -> ToolResult:
        file_path = parameters.get("file_path")
        start_offset = parameters.get("start_offset")
        end_offset = parameters.get("end_offset")
        replacement_text = parameters.get("replacement_text")

        if file_path is None or start_offset is None or end_offset is None or replacement_text is None:
            return ToolResult(success=False, error="缺少必需参数：file_path/start_offset/end_offset/replacement_text")

        if start_offset < 0 or end_offset < 0:
            return ToolResult(success=False, error="start_offset 和 end_offset 必须为非负整数")

        if start_offset > end_offset:
            return ToolResult(success=False, error="start_offset 不能大于 end_offset")

        try:
            workspace_path = get_workspace_path(agent_state)
            target_file = resolve_path_within_workspace(workspace_path, file_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        if not target_file.exists():
            return ToolResult(success=False, error=f"文件不存在: {file_path}")

        try:
            original_content = await asyncio.to_thread(target_file.read_text, "utf-8")
        except Exception as exc:
            logger.error("读取文件失败: %s", exc, exc_info=True)
            return ToolResult(success=False, error=f"读取文件失败: {exc}")

        if end_offset > len(original_content):
            return ToolResult(success=False, error="end_offset 超出文件长度")

        new_content = original_content[:start_offset] + replacement_text + original_content[end_offset:]

        try:
            await asyncio.to_thread(target_file.write_text, new_content, "utf-8")
        except Exception as exc:
            logger.error("写入文件失败: %s", exc, exc_info=True)
            return ToolResult(success=False, error=f"写入文件失败: {exc}")

        if hasattr(agent_state, "modified_files"):
            agent_state.modified_files.add(file_path)

        logger.info(
            "RewriteSelectionTool: rewrote %s [%s:%s] (%s chars)",
            file_path,
            start_offset,
            end_offset,
            len(replacement_text)
        )

        return ToolResult(
            success=True,
            data={
                "file_path": file_path,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "replacement": replacement_text
            },
            summary=f"已重写 {file_path} 中的选区（{start_offset}-{end_offset}）"
        )


class UpdateBibliographyTool(BaseTool):
    """
    更新参考文献工具
    更新 .bib 文件或 \bibliography{}
    """
    
    def __init__(self):
        super().__init__(
            name="update_bibliography_tool",
            description="更新参考文献列表，添加新的 BibTeX 条目"
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "integer",
                    "description": "文档 ID"
                },
                "document_metadata": {
                    "type": "object",
                    "description": "文档元数据（title, authors, year 等）"
                },
                "citation_key": {
                    "type": "string",
                    "description": "引用键"
                }
            },
            "required": ["document_id", "citation_key"]
        }
    
    async def execute(
        self,
        agent_state: Any,
        parameters: Dict[str, Any]
    ) -> ToolResult:
        """
        执行更新参考文献
        
        Args:
            parameters:
                - document_id: 文档 ID
                - document_metadata: 文档元数据（title, authors, year 等）
                - citation_key: 引用键
        """
        document_id = parameters.get("document_id")
        document_metadata = parameters.get("document_metadata", {})
        citation_key = parameters.get("citation_key")
        
        if document_id is None or not citation_key:
            return ToolResult(
                success=False,
                error="Missing required parameters: document_id, citation_key"
            )
        
        bibliography_file = (
            parameters.get("bibliography_file")
            or (getattr(agent_state, "workspace_config", {}) or {}).get("bibliography_file")
            or "references.bib"
        )
        
        try:
            workspace_path = get_workspace_path(agent_state)
            bib_path = resolve_path_within_workspace(workspace_path, bibliography_file)
            ensure_parent_directory(bib_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        
        try:
            existing_content = ""
            if bib_path.exists():
                existing_content = await asyncio.to_thread(
                    bib_path.read_text,
                    encoding="utf-8"
                )
            
            if existing_content and f"{{{citation_key}," in existing_content:
                logger.info("Citation %s 已存在于 %s", citation_key, bibliography_file)
                return ToolResult(
                    success=True,
                    data={
                        "citation_key": citation_key,
                        "bibliography_file": bibliography_file,
                        "status": "exists"
                    },
                    summary=f"{citation_key} 已存在，未重复添加"
                )
            
            bibtex_entry = self._build_bibtex_entry(citation_key, document_metadata)
            
            await asyncio.to_thread(
                self._append_bib_entry,
                bib_path,
                bibtex_entry,
                bool(existing_content.strip())
            )
            
            citation_mappings = getattr(agent_state, "citation_mappings", None)
            if isinstance(citation_mappings, dict):
                citation_mappings[str(document_id)] = citation_key
            
            # 标记文件已被修改（用于生成 diff）
            if hasattr(agent_state, 'modified_files'):
                agent_state.modified_files.add(bibliography_file)
            
            logger.info("Added citation %s to %s", citation_key, bibliography_file)
            
            return ToolResult(
                success=True,
                data={
                    "citation_key": citation_key,
                    "bibliography_file": bibliography_file,
                    "bibtex_entry": bibtex_entry
                },
                summary=f"已更新参考文献，添加 {citation_key}"
            )
        except Exception as exc:
            logger.error("更新参考文献失败: %s", exc, exc_info=True)
            return ToolResult(success=False, error=f"更新参考文献失败: {exc}")
    
    def _build_bibtex_entry(self, citation_key: str, metadata: Dict[str, Any]) -> str:
        """根据元数据构建 BibTeX 条目"""
        if metadata.get("bibtex_entry"):
            return metadata["bibtex_entry"].strip()
        
        entry_type = metadata.get("type", "article")
        fields = []
        
        def _format_value(value):
            if isinstance(value, list):
                return " and ".join(value) if value else ""
            return str(value)
        
        field_mapping = {
            "title": metadata.get("title"),
            "author": metadata.get("authors") or metadata.get("author"),
            "journal": metadata.get("journal"),
            "booktitle": metadata.get("booktitle"),
            "year": metadata.get("year"),
            "volume": metadata.get("volume"),
            "number": metadata.get("number"),
            "pages": metadata.get("pages"),
            "publisher": metadata.get("publisher"),
            "doi": metadata.get("doi"),
            "url": metadata.get("url"),
        }
        
        for field, value in field_mapping.items():
            if value:
                formatted_value = _format_value(value)
                if formatted_value:
                    fields.append(f"  {field} = {{{formatted_value}}}")
        
        body = ",\n".join(fields)
        return f"@{entry_type}{{{citation_key},\n{body}\n}}"
    
    def _append_bib_entry(
        self,
        bib_path: Path,
        entry: str,
        has_existing_content: bool
    ):
        """将条目追加到 bib 文件"""
        with open(bib_path, "a", encoding="utf-8") as file:
            if has_existing_content:
                file.write("\n\n")
            file.write(entry.strip())
            file.write("\n")


class InsertTextTool(BaseTool):
    """
    插入文本工具
    在 LaTeX 文档的指定位置插入文本内容（如摘要、段落等）
    使用上下文定位，类似 Cursor 的编辑方式
    """
    
    def __init__(self):
        super().__init__(
            name="insert_text_tool",
            description=(
                "在 LaTeX 文档中插入文本内容（段落、摘要、章节等）。"
                "使用上下文定位：提供要在其后插入内容的文本片段（search_context），"
                "确保唯一匹配。例如：在 \\begin{abstract} 后插入，"
                "提供包含该标记及其前后几行的上下文。"
            )
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件路径（相对于工作区）"
                },
                "text_to_insert": {
                    "type": "string",
                    "description": "要插入的文本内容"
                },
                "search_context": {
                    "type": "string",
                    "description": (
                        "用于定位插入位置的上下文文本。"
                        "应包含要在其后插入内容的代码段，包括前后几行以确保唯一性。"
                        "插入位置在此上下文的末尾。"
                    )
                },
                "insert_mode": {
                    "type": "string",
                    "description": "插入模式：'after'（在上下文后插入，默认） 或 'before'（在上下文前插入）",
                    "enum": ["after", "before"],
                    "default": "after"
                }
            },
            "required": ["file_path", "text_to_insert", "search_context"]
        }
    
    async def execute(
        self,
        agent_state: Any,
        parameters: Dict[str, Any]
    ) -> ToolResult:
        """
        执行文本插入
        
        Args:
            parameters:
                - file_path: 文件路径
                - text_to_insert: 要插入的文本内容
                - search_context: 用于定位的上下文文本
                - insert_mode: 'after' 或 'before'
        """
        file_path = parameters.get("file_path")
        text_to_insert = parameters.get("text_to_insert", "")
        search_context = parameters.get("search_context", "")
        insert_mode = parameters.get("insert_mode", "after")
        
        if not file_path:
            return ToolResult(
                success=False,
                error="Missing required parameter: file_path"
            )
        
        if not text_to_insert.strip():
            return ToolResult(
                success=False,
                error="text_to_insert parameter cannot be empty"
            )
        
        if not search_context.strip():
            return ToolResult(
                success=False,
                error="search_context parameter cannot be empty. Provide context lines to locate insert position."
            )
        
        try:
            workspace_path = get_workspace_path(agent_state)
            target_file = resolve_path_within_workspace(workspace_path, file_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        
        if not target_file.exists():
            return ToolResult(success=False, error=f"文件不存在: {file_path}")
        
        try:
            result = await asyncio.to_thread(
                self._insert_text_with_context,
                target_file,
                text_to_insert,
                search_context,
                insert_mode
            )
            
            if result["success"]:
                # 标记文件已修改（用于生成 diff）
                agent_state.modified_files.add(str(file_path))
                
                return ToolResult(
                    success=True,
                    data={
                        "file_path": file_path,
                        "inserted_lines": result["inserted_lines"],
                        "insert_position": result["insert_line"]
                    },
                    summary=f"在 {file_path} 成功插入 {result['inserted_lines']} 行文本（位置：第 {result['insert_line']} 行附近）"
                )
            else:
                return ToolResult(success=False, error=result["error"])
        
        except Exception as e:
            logger.error(f"Insert text failed: {e}", exc_info=True)
            return ToolResult(success=False, error=str(e))
    
    def _insert_text_with_context(
        self,
        file_path: Path,
        text_to_insert: str,
        search_context: str,
        insert_mode: str = "after"
    ) -> Dict[str, Any]:
        """
        使用上下文定位并插入文本（类似 Cursor 的编辑方式）
        
        Args:
            file_path: 文件路径
            text_to_insert: 要插入的文本
            search_context: 用于定位的上下文文本
            insert_mode: 'after' 或 'before'
        
        Returns:
            {success: bool, insert_line: int, inserted_lines: int, error: str}
        """
        try:
            # 读取原文件内容
            with open(file_path, "r", encoding="utf-8") as f:
                file_content = f.read()
            
            # 查找上下文在文件中的位置
            context_index = file_content.find(search_context)
            
            if context_index == -1:
                return {
                    "success": False,
                    "error": (
                        f"未找到匹配的上下文。请提供更精确的上下文文本，"
                        f"包括要插入位置前后的几行代码。"
                    )
                }
            
            # 检查是否有多个匹配（上下文不唯一）
            second_match = file_content.find(search_context, context_index + 1)
            if second_match != -1:
                return {
                    "success": False,
                    "error": (
                        f"找到多个匹配的上下文（不唯一）。"
                        f"请提供更多的上下文行以确保唯一匹配。"
                    )
                }
            
            # 确定插入位置
            if insert_mode == "after":
                # 在上下文之后插入
                insert_position = context_index + len(search_context)
            else:  # before
                # 在上下文之前插入
                insert_position = context_index
            
            # 确保插入的文本前后有适当的换行符
            text_to_insert_formatted = text_to_insert
            if not text_to_insert.startswith('\n') and insert_position > 0:
                text_to_insert_formatted = '\n' + text_to_insert_formatted
            if not text_to_insert.endswith('\n'):
                text_to_insert_formatted = text_to_insert_formatted + '\n'
            
            # 执行插入
            new_content = (
                file_content[:insert_position] + 
                text_to_insert_formatted + 
                file_content[insert_position:]
            )
            
            # 写回文件
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            
            # 计算插入位置的行号（用于日志）
            insert_line = file_content[:insert_position].count('\n') + 1
            inserted_lines = text_to_insert.count('\n') + 1
            
            logger.info(
                f"Inserted {inserted_lines} lines {insert_mode} context at line ~{insert_line} in {file_path}"
            )
            
            return {
                "success": True,
                "insert_line": insert_line,
                "inserted_lines": inserted_lines
            }
        
        except Exception as e:
            logger.error(f"Failed to insert text with context: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

