"""
LaTeX 解析辅助函数
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any, Iterable, Optional, Set
import os
import re

SECTION_PATTERN = re.compile(
    r"\\(?P<command>section|subsection|subsubsection|paragraph|subparagraph)\*?\{(?P<title>[^}]*)\}"
)
CITATION_PATTERN = re.compile(
    r"\\(?P<command>cite[a-zA-Z]*)\s*\{(?P<keys>[^}]*)\}"
)
BIB_PATTERN = re.compile(
    r"\\(?P<command>bibliography|addbibresource)\s*\{(?P<files>[^}]*)\}"
)
BIB_ENTRY_PATTERN = re.compile(r"@\w+\{([^,]+),")


def list_workspace_files(
    workspace_path: Path,
    workspace_files: Optional[List[str]],
    extensions: Optional[Set[str]] = None
) -> List[Path]:
    """列出工作区内指定扩展名的文件"""
    normalized_extensions = {ext.lower() for ext in extensions} if extensions else None
    resolved_files: List[Path] = []

    if workspace_files:
        for rel_path in workspace_files:
            if normalized_extensions and not rel_path.lower().endswith(tuple(normalized_extensions)):
                continue
            candidate = workspace_path / rel_path
            if candidate.exists():
                resolved_files.append(candidate)

    if resolved_files:
        return resolved_files

    # 回退到文件系统扫描
    for root, _, files in os.walk(workspace_path):
        for file_name in files:
            if normalized_extensions and not file_name.lower().endswith(tuple(normalized_extensions)):
                continue
            resolved_files.append(Path(root) / file_name)

    return resolved_files


def collect_latex_metadata(files: Iterable[Path], workspace_path: Path) -> Dict[str, Any]:
    """收集多个 LaTeX 文件的章节、引用与参考文献信息"""
    sections: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    bibliography_files: Set[str] = set()

    for file_path in files:
        if not file_path.exists():
            continue
        relative_path = str(file_path.relative_to(workspace_path))
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = file_path.read_text(encoding="latin-1")

        sections.extend(parse_sections_from_text(content, relative_path))
        file_citations = parse_citations_from_text(content, relative_path)
        citations.extend(file_citations)
        for bib_file in extract_bibliography_files_from_text(content):
            bibliography_files.add(bib_file)

    return {
        "sections": sections,
        "citations": citations,
        "bibliography_files": sorted(bibliography_files)
    }


def parse_sections_from_text(text: str, relative_path: str) -> List[Dict[str, Any]]:
    """解析章节结构"""
    sections = []
    for line_index, line in enumerate(text.splitlines(), start=1):
        for match in SECTION_PATTERN.finditer(line):
            command = match.group("command")
            sections.append(
                {
                    "file": relative_path,
                    "line": line_index,
                    "command": command,
                    "level": _section_level(command),
                    "title": match.group("title").strip()
                }
            )
    return sections


def parse_citations_from_text(text: str, relative_path: str) -> List[Dict[str, Any]]:
    """解析引用信息"""
    citations = []
    for line_index, line in enumerate(text.splitlines(), start=1):
        for match in CITATION_PATTERN.finditer(line):
            keys = [
                key.strip()
                for key in match.group("keys").split(",")
                if key.strip()
            ]
            if not keys:
                continue
            citations.append(
                {
                    "file": relative_path,
                    "line": line_index,
                    "character": match.start(),
                    "command": match.group("command"),
                    "keys": keys,
                    "raw": match.group(0)
                }
            )
    return citations


def extract_bibliography_files_from_text(text: str) -> List[str]:
    """解析文档声明的参考文献文件"""
    bibliography_files: List[str] = []
    for match in BIB_PATTERN.finditer(text):
        files = match.group("files").split(",")
        for file_name in files:
            cleaned = file_name.strip()
            if cleaned:
                if not cleaned.lower().endswith(".bib"):
                    cleaned = f"{cleaned}.bib"
                bibliography_files.append(cleaned)
    return bibliography_files


def load_bib_entries(bib_paths: Iterable[Path]) -> Dict[str, List[str]]:
    """读取 BibTeX 条目并返回键集合"""
    entries: Dict[str, List[str]] = {}
    for bib_path in bib_paths:
        if not bib_path.exists():
            continue
        try:
            content = bib_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = bib_path.read_text(encoding="latin-1")
        for match in BIB_ENTRY_PATTERN.finditer(content):
            key = match.group(1).strip()
            if key:
                entries.setdefault(key, []).append(str(bib_path))
    return entries


def _section_level(command: str) -> int:
    """根据命令名称返回章节层级"""
    hierarchy = {
        "section": 1,
        "subsection": 2,
        "subsubsection": 3,
        "paragraph": 4,
        "subparagraph": 5
    }
    return hierarchy.get(command, 0)

