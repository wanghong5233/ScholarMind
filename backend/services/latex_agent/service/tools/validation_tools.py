"""
验证类工具
"""
from typing import Dict, Any, List
from pathlib import Path
import asyncio
import logging
import os
import shutil
import subprocess

from .base_tool import BaseTool, ToolResult
from .workspace_utils import get_workspace_path, resolve_path_within_workspace
from .latex_utils import (
    collect_latex_metadata,
    list_workspace_files,
    load_bib_entries,
)

logger = logging.getLogger(__name__)


class CompileLaTeXTool(BaseTool):
    """
    编译 LaTeX 工具
    编译 LaTeX 文档并返回结果
    """
    
    def __init__(self):
        super().__init__(
            name="compile_latex_tool",
            description="编译 LaTeX 文档，检查语法错误和引用错误"
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "workspace_id": {
                    "type": "string",
                    "description": "工作区 ID"
                },
                "main_file": {
                    "type": "string",
                    "description": "主文件路径（默认 main.tex）",
                    "default": "main.tex"
                }
            },
            "required": []
        }
    
    def _detect_unicode_content(self, file_path: Path) -> bool:
        """检测文件是否包含 Unicode 字符（如中文）"""
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            # 检查是否包含中文字符（CJK 统一表意文字范围）
            for char in content:
                if '\u4e00' <= char <= '\u9fff':  # 中文字符范围
                    return True
            return False
        except Exception:
            return False
    
    def _detect_ieeetran_template(self, file_path: Path) -> bool:
        """检测文件是否使用 IEEEtran 模板"""
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            # 检查文档类是否为 IEEEtran
            if '\\documentclass' in content and 'IEEEtran' in content:
                return True
            return False
        except Exception:
            return False
    
    def _has_chinese_support_package(self, file_path: Path) -> bool:
        """检测 LaTeX 文件是否包含中文支持包"""
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            # 检查是否包含常见的中文支持包
            chinese_packages = [
                '\\usepackage{ctex}',
                '\\usepackage{xeCJK}',
                '\\usepackage{CJK}',
                '\\usepackage[utf8]{inputenc}',  # 虽然不完全支持中文，但至少尝试了
            ]
            return any(pkg in content for pkg in chinese_packages)
        except Exception:
            return False
    
    async def execute(
        self,
        agent_state: Any,
        parameters: Dict[str, Any]
    ) -> ToolResult:
        """
        执行编译
        
        Args:
            parameters:
                - workspace_id: 工作区 ID
                - main_file: 主文件路径（默认 main.tex）
        """
        workspace_id = parameters.get("workspace_id") or getattr(agent_state, "workspace_id", None)
        main_file = (
            parameters.get("main_file")
            or (getattr(agent_state, "workspace_config", {}) or {}).get("main_file")
            or "main.tex"
        )
        
        # 如果没有指定编译器，先检测文件内容
        default_compiler = "pdflatex"
        if not parameters.get("compiler"):
            try:
                workspace_path = get_workspace_path(agent_state, workspace_id) if workspace_id else None
                if workspace_path:
                    resolved_main = resolve_path_within_workspace(workspace_path, main_file)
                    if resolved_main.exists():
                        # 优先检测 IEEEtran 模板，强制使用 pdflatex
                        if self._detect_ieeetran_template(resolved_main):
                            default_compiler = "pdflatex"
                            logger.info(f"检测到 IEEEtran 模板，强制使用 pdflatex 编译器（最佳兼容性）")
                        # 然后检测中文内容
                        elif self._detect_unicode_content(resolved_main):
                            # 检测到中文，优先使用 xelatex（对中文支持最好）
                            if shutil.which("xelatex"):
                                default_compiler = "xelatex"
                                logger.info(f"检测到中文内容，自动选择编译器: xelatex")
                            elif shutil.which("lualatex"):
                                default_compiler = "lualatex"
                                logger.warning(f"检测到中文内容，但 xelatex 不可用，使用 lualatex（可能需要安装 luaotfload）")
                            else:
                                logger.warning(f"检测到中文内容，但 xelatex 和 lualatex 都不可用，使用 pdflatex（可能无法正确显示中文）")
            except Exception as e:
                logger.debug(f"编译器自动检测失败: {e}")  # 如果检测失败，使用默认的 pdflatex
        
        # 注意：不再从 workspace_config 中读取 compiler，始终使用自动检测
        compiler = parameters.get("compiler") or default_compiler
        bibliography_file = (
            parameters.get("bibliography_file")
            or (getattr(agent_state, "workspace_config", {}) or {}).get("bibliography_file")
            or "references.bib"
        )
        
        if workspace_id is None:
            return ToolResult(success=False, error="workspace_id is required")
        
        try:
            workspace_path = get_workspace_path(agent_state, workspace_id)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        
        if not workspace_path.exists():
            return ToolResult(success=False, error=f"工作区不存在: {workspace_path}")
        
        try:
            compile_result = await asyncio.to_thread(
                self._compile_latex,
                workspace_path,
                main_file,
                compiler,
                bibliography_file
            )
        except FileNotFoundError as exc:
            logger.error("LaTeX 编译失败: %s", exc)
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:
            logger.error("LaTeX 编译异常: %s", exc, exc_info=True)
            return ToolResult(success=False, error=f"LaTeX 编译失败: {exc}")
        
        # 检查是否有中文缺失字符的错误
        has_chinese_missing = False
        if compile_result.get("errors"):
            for error in compile_result["errors"]:
                if "Missing character" in error and "U+" in error:
                    has_chinese_missing = True
                    break
        
        # 检查 LaTeX 文件是否包含中文支持包
        resolved_main = resolve_path_within_workspace(workspace_path, main_file)
        has_chinese_package = self._has_chinese_support_package(resolved_main) if resolved_main.exists() else False
        
        # 如果检测到中文缺失字符，提供更好的错误提示
        if has_chinese_missing:
            suggestions = []
            if compiler == "pdflatex":
                suggestions.append("当前使用 pdflatex 编译器，不支持 Unicode 字符")
                suggestions.append("解决方案：1) 在文档开头添加 \\usepackage{ctex}，或 2) 使用 xelatex 编译器")
            elif compiler == "lualatex":
                suggestions.append("当前使用 lualatex 编译器，但缺少 luaotfload 模块")
                suggestions.append("解决方案：1) 在文档开头添加 \\usepackage{ctex}，或 2) 使用 xelatex 编译器，或 3) 安装 luaotfload 包")
            elif compiler == "xelatex":
                if not has_chinese_package:
                    suggestions.append("当前使用 xelatex 编译器，但文档缺少中文支持包")
                    suggestions.append("解决方案：在文档开头添加 \\usepackage{ctex} 或 \\usepackage{xeCJK}")
            
            if suggestions:
                compile_result["errors"].insert(0, "提示: " + " | ".join(suggestions))
        
        summary = "LaTeX 编译成功" if compile_result["compiled"] else "LaTeX 编译失败"
        return ToolResult(
            success=compile_result["compiled"],
            data=compile_result,
            summary=summary
        )
    
    def _compile_latex(
        self,
        workspace_path: Path,
        main_file: str,
        compiler: str,
        bibliography_file: str,
        max_runs: int = 2
    ) -> Dict[str, Any]:
        """执行 LaTeX 编译流程"""
        compiler_path = shutil.which(compiler)
        if compiler_path is None:
            raise FileNotFoundError(f"找不到编译器: {compiler}")
        
        resolved_main = resolve_path_within_workspace(workspace_path, main_file)
        if not resolved_main.exists():
            raise FileNotFoundError(f"主文件不存在: {main_file}")
        
        relative_main = os.path.relpath(resolved_main, workspace_path)
        main_stem = resolved_main.stem
        logs: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        
        compiled = True
        run_result = self._run_compiler(compiler_path, relative_main, workspace_path)
        logs.append(run_result)
        errors.extend(self._extract_errors(run_result["log"]))
        warnings.extend(self._extract_warnings(run_result["log"]))
        if run_result["returncode"] != 0:
            compiled = False
            return {
                "compiled": False,
                "pdf_path": None,
                "logs": logs,
                "errors": errors,
                "warnings": warnings
            }
        
        aux_path = resolved_main.with_suffix(".aux")
        bbl_path = resolved_main.with_suffix(".bbl")
        bib_path = resolve_path_within_workspace(workspace_path, bibliography_file)
        
        # 在运行 BibTeX 之前，强制删除旧的 .bbl 文件，确保使用最新的 .bib 文件重新生成
        # 这样可以避免旧的 .bbl 文件格式错误导致的问题
        if bbl_path.exists():
            try:
                bbl_path.unlink()
                logger.info(f"已删除旧的 .bbl 文件，将使用最新的 .bib 文件重新生成: {bbl_path}")
            except Exception as e:
                logger.warning(f"无法删除旧的 .bbl 文件: {e}")
        
        if (
            bib_path.exists()
            and aux_path.exists()
            and self._aux_requires_bibtex(aux_path)
        ):
            bibtex_path = shutil.which("bibtex")
            if bibtex_path:
                # bibtex 不支持 -interaction/-halt-on-error 这些 pdftex 参数，单独处理
                bib_result = self._run_compiler(
                    bibtex_path,
                    main_stem,
                    workspace_path,
                    use_standard_flags=False
                )
                logs.append(bib_result)
                errors.extend(self._extract_errors(bib_result["log"]))
                warnings.extend(self._extract_warnings(bib_result["log"]))
                if bib_result["returncode"] != 0:
                    compiled = False
                    # BibTeX 失败时删除可能损坏的 .bbl 文件，防止后续编译报错
                    if bbl_path.exists():
                        try:
                            bbl_path.unlink()
                            logger.info(f"BibTeX 失败，已删除损坏的 .bbl 文件: {bbl_path}")
                        except Exception as e:
                            logger.warning(f"无法删除 .bbl 文件: {e}")
                else:
                    # BibTeX 成功运行后，再次验证生成的 .bbl 文件
                    if bbl_path.exists() and not self._is_bbl_file_valid(bbl_path):
                        try:
                            bbl_path.unlink()
                            logger.warning(f"BibTeX 生成的 .bbl 文件格式无效，已删除: {bbl_path}")
                            compiled = False
                        except Exception as e:
                            logger.warning(f"无法删除无效的 .bbl 文件: {e}")
        elif bbl_path.exists() and not bib_path.exists():
            # 如果 .bib 文件不存在但有旧的 .bbl 文件，删除它防止冲突
            try:
                bbl_path.unlink()
                logger.info(f"检测到缺少 .bib 文件，已删除旧的 .bbl 文件: {bbl_path}")
            except Exception as e:
                logger.warning(f"无法删除 .bbl 文件: {e}")
        
        if compiled:
            for run_idx in range(max_runs):
                # 在每次 rerun 前检查 .bbl 文件有效性（如果存在）
                if bbl_path.exists() and not self._is_bbl_file_valid(bbl_path):
                    try:
                        bbl_path.unlink()
                        logger.warning(f"Rerun {run_idx + 1} 前检测到无效的 .bbl 文件，已删除: {bbl_path}")
                        # 如果 .bbl 文件无效，可能需要重新运行 BibTeX
                        if bib_path.exists() and aux_path.exists() and self._aux_requires_bibtex(aux_path):
                            bibtex_path = shutil.which("bibtex")
                            if bibtex_path:
                                logger.info(f"重新运行 BibTeX 以生成有效的 .bbl 文件")
                                bib_result = self._run_compiler(
                                    bibtex_path,
                                    main_stem,
                                    workspace_path,
                                    use_standard_flags=False
                                )
                                logs.append(bib_result)
                                errors.extend(self._extract_errors(bib_result["log"]))
                                warnings.extend(self._extract_warnings(bib_result["log"]))
                    except Exception as e:
                        logger.warning(f"无法删除无效的 .bbl 文件: {e}")
                
                rerun_result = self._run_compiler(compiler_path, relative_main, workspace_path)
                logs.append(rerun_result)
                rerun_errors = self._extract_errors(rerun_result["log"])
                errors.extend(rerun_errors)
                warnings.extend(self._extract_warnings(rerun_result["log"]))
                
                # 检查是否有 "missing \item" 错误，这通常表示 .bbl 文件格式错误
                if any("missing \\item" in err.lower() or "missing \\item" in err for err in rerun_errors):
                    logger.warning(f"检测到 'missing \\item' 错误，删除 .bbl 文件并重新运行 BibTeX")
                    if bbl_path.exists():
                        try:
                            bbl_path.unlink()
                            logger.info(f"已删除导致错误的 .bbl 文件: {bbl_path}")
                            # 重新运行 BibTeX
                            if bib_path.exists() and aux_path.exists() and self._aux_requires_bibtex(aux_path):
                                bibtex_path = shutil.which("bibtex")
                                if bibtex_path:
                                    logger.info(f"重新运行 BibTeX 以修复 .bbl 文件")
                                    bib_result = self._run_compiler(
                                        bibtex_path,
                                        main_stem,
                                        workspace_path,
                                        use_standard_flags=False
                                    )
                                    logs.append(bib_result)
                                    errors.extend(self._extract_errors(bib_result["log"]))
                                    warnings.extend(self._extract_warnings(bib_result["log"]))
                                    # 如果 BibTeX 成功，继续下一次 rerun
                                    if bib_result["returncode"] == 0:
                                        continue
                        except Exception as e:
                            logger.warning(f"无法删除导致错误的 .bbl 文件: {e}")
                
                if rerun_result["returncode"] != 0:
                    compiled = False
                    break
        
        # 最终判断：以 PDF 是否成功生成为准，而不是单纯依赖退出码
        # 因为某些情况下（如简单文档无需多次编译），后续 rerun 可能返回非零但 PDF 已存在
        pdf_path = resolved_main.with_suffix(".pdf")
        pdf_exists = pdf_path.exists()
        relative_pdf = (
            os.path.relpath(pdf_path, workspace_path)
            if pdf_exists
            else None
        )
        
        # 如果 PDF 生成成功，即使中间某次 rerun 失败也认为编译成功
        final_success = pdf_exists
        
        return {
            "compiled": final_success,
            "pdf_path": relative_pdf,
            "logs": logs,
            "errors": errors,
            "warnings": warnings
        }
    
    def _run_compiler(
        self,
        executable: str,
        target: str,
        cwd: Path,
        use_standard_flags: bool = True
    ) -> Dict[str, Any]:
        """运行单次编译命令"""
        command = [executable]
        if use_standard_flags:
            command.extend([
            "-interaction=nonstopmode",
            "-halt-on-error",
            ])
        command.append(target)
        process = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=180
        )
        log = (process.stdout or "") + "\n" + (process.stderr or "")
        return {
            "command": " ".join(command),
            "returncode": process.returncode,
            "log": log
        }

    def _aux_requires_bibtex(self, aux_path: Path) -> bool:
        """判断 aux 文件是否包含引用信息，决定是否需要运行 bibtex"""
        try:
            content = aux_path.read_text(encoding="utf-8", errors="ignore")
        except FileNotFoundError:
            return False
        tokens = ("\\citation", "\\bibdata", "\\bibstyle")
        return any(token in content for token in tokens)
    
    def _is_bbl_file_valid(self, bbl_path: Path) -> bool:
        """检查 .bbl 文件格式是否有效"""
        if not bbl_path.exists():
            return False
        try:
            content = bbl_path.read_text(encoding="utf-8", errors="ignore")
            # 必须包含 thebibliography 环境
            if "\\begin{thebibliography}" not in content or "\\end{thebibliography}" not in content:
                return False
            
            # 提取 thebibliography 环境的内容
            begin_idx = content.find("\\begin{thebibliography}")
            end_idx = content.find("\\end{thebibliography}")
            if begin_idx == -1 or end_idx == -1 or end_idx <= begin_idx:
                return False
            
            # 提取环境内的内容
            env_content = content[begin_idx:end_idx]
            
            # 检查是否有有效的 \bibitem 条目
            # \bibitem 必须在 \begin{thebibliography} 之后
            if "\\bibitem" not in env_content:
                # 如果环境内没有 \bibitem，会导致 "missing \item" 错误
                logger.warning(f".bbl 文件 {bbl_path} 缺少 \\bibitem 条目")
                return False
            
            # 进一步检查：确保 \bibitem 在 \begin{thebibliography} 之后
            # 查找第一个 \bibitem 的位置
            bibitem_idx = env_content.find("\\bibitem")
            if bibitem_idx == -1:
                return False
            
            # 检查 \begin{thebibliography} 和第一个 \bibitem 之间是否有非空白内容
            # 如果有非空白内容（除了注释），可能导致问题
            between_content = env_content[:bibitem_idx].strip()
            # 移除 LaTeX 注释（以 % 开头的行）
            lines = between_content.split('\n')
            non_comment_lines = [line for line in lines if line.strip() and not line.strip().startswith('%')]
            if non_comment_lines:
                # 如果 \begin{thebibliography} 和第一个 \bibitem 之间有非注释内容，可能有问题
                logger.warning(f".bbl 文件 {bbl_path} 在 \\begin{{thebibliography}} 和第一个 \\bibitem 之间有内容: {non_comment_lines[:3]}")
                # 但这种情况不一定导致错误，所以先不返回 False
            
            return True
        except Exception as e:
            logger.warning(f"无法读取 .bbl 文件 {bbl_path}: {e}")
            return False
    
    def _extract_errors(self, log_text: str) -> List[str]:
        """从日志提取错误信息"""
        errors = []
        lines = log_text.splitlines()
        
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            # 标准错误行
            if line_stripped.startswith("!") or "Error" in line:
                errors.append(line_stripped)
            # 文件未找到错误（更友好的格式）
            elif "File `" in line and "not found" in line:
                # 提取文件名
                import re
                match = re.search(r"File `([^']+)' not found", line)
                if match:
                    missing_file = match.group(1)
                    errors.append(f"文件未找到: {missing_file}")
                else:
                    errors.append(line_stripped)
            # LaTeX Error 行
            elif "LaTeX Error:" in line:
                error_msg = line_stripped
                # 尝试获取下一行的详细信息
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line and not next_line.startswith("See the"):
                        error_msg += f" {next_line}"
                errors.append(error_msg)
        
        return errors
    
    def _extract_warnings(self, log_text: str) -> List[str]:
        """从日志提取警告信息"""
        return [
            line.strip()
            for line in log_text.splitlines()
            if "Warning" in line
        ]


class CheckCitationConsistencyTool(BaseTool):
    """
    检查引用一致性工具
    检查引用格式是否一致
    """
    
    def __init__(self):
        super().__init__(
            name="check_citation_consistency_tool",
            description="检查文档中所有引用的格式是否一致"
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件路径（可选，默认检查所有文件）"
                },
                "expected_command": {
                    "type": "string",
                    "description": "预期使用的引用命令（如 citep、citet 等）"
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
        执行一致性检查
        
        Args:
            parameters:
                - file_path: 文件路径（可选，默认检查所有文件）
        """
        try:
            workspace_path = get_workspace_path(agent_state)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        
        try:
            target_files = await self._resolve_target_files(
                workspace_path,
                agent_state,
                parameters.get("file_path")
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        
        if not target_files:
            return ToolResult(
                success=False,
                error="没有可供检查的 LaTeX 文件"
            )
        
        metadata = await asyncio.to_thread(
            collect_latex_metadata,
            target_files,
            workspace_path
        )
        citations = metadata.get("citations", [])
        
        if not citations:
            return ToolResult(
                success=True,
                data={
                    "total_citations": 0,
                    "styles": [],
                    "inconsistent_citations": []
                },
                summary="未在文档中发现任何引用"
            )
        
        expected_command = parameters.get("expected_command") or citations[0]["command"]
        inconsistent = [
            {
                "file": cite["file"],
                "line": cite["line"],
                "command": cite["command"],
                "expected": expected_command,
                "raw": cite["raw"]
            }
            for cite in citations
            if cite["command"] != expected_command
        ]
        styles = sorted({cite["command"] for cite in citations})
        
        summary = (
            f"共发现 {len(citations)} 个引用，"
            f"{len(inconsistent)} 个与期望命令（{expected_command}）不一致"
        )
        
        return ToolResult(
            success=len(inconsistent) == 0,
            data={
                "total_citations": len(citations),
                "styles": styles,
                "primary_command": expected_command,
                "inconsistent_citations": inconsistent
            },
            summary=summary
        )
    
    async def _resolve_target_files(
        self,
        workspace_path: Path,
        agent_state: Any,
        file_path: str = None
    ) -> List[Path]:
        """确定需要扫描的文件"""
        workspace_files = getattr(agent_state, "workspace_files", [])
        
        if file_path:
            resolved = resolve_path_within_workspace(workspace_path, file_path)
            if not resolved.exists():
                raise ValueError(f"文件不存在: {file_path}")
            return [resolved]
        
        tex_files = list_workspace_files(
            workspace_path,
            workspace_files,
            extensions={".tex"}
        )
        return tex_files


class CheckBibliographyTool(BaseTool):
    """
    检查参考文献工具
    检查引用是否都有对应的参考文献条目
    """
    
    def __init__(self):
        super().__init__(
            name="check_bibliography_tool",
            description="检查所有引用是否都有对应的参考文献条目"
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "workspace_id": {
                    "type": "string",
                    "description": "工作区 ID"
                },
                "bibliography_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "需要检查的参考文献文件列表（可选）"
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
        执行参考文献检查
        
        Args:
            parameters:
                - workspace_id: 工作区 ID
        """
        workspace_id = parameters.get("workspace_id") or getattr(agent_state, "workspace_id", None)
        if not workspace_id:
            return ToolResult(success=False, error="workspace_id is required")
        
        try:
            workspace_path = get_workspace_path(agent_state, workspace_id)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        
        latex_files = list_workspace_files(
            workspace_path,
            getattr(agent_state, "workspace_files", []),
            extensions={".tex"}
        )
        
        if not latex_files:
            return ToolResult(
                success=False,
                error="未找到任何 LaTeX 源文件，无法检查引用"
            )
        
        metadata = await asyncio.to_thread(
            collect_latex_metadata,
            latex_files,
            workspace_path
        )
        citations = metadata.get("citations", [])
        citation_keys = sorted({key for cite in citations for key in cite["keys"]})
        
        bibliography_candidates = parameters.get("bibliography_files") or metadata.get("bibliography_files") or []
        config_bib = (getattr(agent_state, "workspace_config", {}) or {}).get("bibliography_file")
        if config_bib:
            bibliography_candidates.append(config_bib)
        if not bibliography_candidates:
            bibliography_candidates = ["references.bib"]
        
        resolved_bib_paths: List[Path] = []
        missing_files: List[str] = []
        for bib_file in bibliography_candidates:
            try:
                resolved = resolve_path_within_workspace(workspace_path, bib_file)
            except ValueError:
                missing_files.append(bib_file)
                continue
            if resolved.exists():
                resolved_bib_paths.append(resolved)
            else:
                missing_files.append(bib_file)
        
        if not resolved_bib_paths:
            return ToolResult(
                success=False,
                error=f"未找到有效的参考文献文件: {', '.join(missing_files) if missing_files else 'unknown'}"
            )
        
        bib_entries = await asyncio.to_thread(load_bib_entries, resolved_bib_paths)
        entry_keys = set(bib_entries.keys())
        
        missing_citations = sorted(set(citation_keys) - entry_keys)
        unused_references = sorted(entry_keys - set(citation_keys))
        
        summary = (
            f"共检测 {len(citation_keys)} 个引用键，"
            f"{len(missing_citations)} 个缺失、{len(unused_references)} 个未使用"
        )
        
        data = {
            "citation_keys": citation_keys,
            "missing_citations": missing_citations,
            "unused_references": unused_references,
            "bibliography_files": [str(path.relative_to(workspace_path)) for path in resolved_bib_paths],
            "missing_bibliography_files": missing_files
        }
        
        return ToolResult(
            success=len(missing_citations) == 0,
            data=data,
            summary=summary
        )

