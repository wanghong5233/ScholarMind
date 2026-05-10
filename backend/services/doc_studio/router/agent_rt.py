"""
Doc Studio API 路由
"""
from fastapi import APIRouter, HTTPException, Depends, Header, UploadFile, File, Form, Request, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List, Literal
from pathlib import Path
import asyncio
import json
import logging
import re
import uuid
import shutil
import time
import hashlib
from collections import defaultdict
from datetime import datetime

from dependencies import get_agent, refresh_llm_client, get_llm_client
from core.config import settings
from service.agent_service import LaTeXEditAgent, AgentState, AgentCancelledError
from service.tools.validation_tools import CompileLaTeXTool
from service.rag_api_client import get_rag_api_client
from service.async_run_manager import get_async_run_manager
from metrics import collect_metrics_summary, format_prometheus_metrics, record_user_feedback
from utils.trace import get_trace_id
from utils.prompt_loader import clear_prompt_cache
from security import sanitize_user_input, UserRateLimiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Doc Studio"])
# 非 workspace 前缀的通用路由（例如知识库列表）
general_router = APIRouter(tags=["Doc Studio"])
rate_limiter = UserRateLimiter()


# 请求/响应模型
class LaTeXEditRequest(BaseModel):
    """编辑文档请求"""
    user_intent: str
    target_location: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional context payload (selection/file_path/image_attachments).",
    )
    options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional overrides (llm_provider/llm_model/llm_temperature/llm_max_tokens/interaction_mode)"
    )
    collect_training_data: bool = False  # 是否收集训练数据（用于 RL 训练）
    knowledge_base_id: Optional[int] = Field(
        default=None,
        description="Active knowledge base ID used for retrieval/RAG tools"
    )
    knowledge_base_name: Optional[str] = Field(
        default=None,
        description="Optional display name of the selected knowledge base"
    )


class AddCitationRequest(BaseModel):
    """添加引用请求"""
    target_text: Optional[str] = None
    target_position: Optional[Dict[str, Any]] = None
    citation_style: str = "\\cite{}"
    auto_search: bool = True
    knowledge_base_id: Optional[int] = None
    knowledge_base_name: Optional[str] = None


class BatchAddCitationsRequest(BaseModel):
    """批量添加引用请求"""
    target_sections: Optional[List[str]] = None
    citation_style: str = "\\cite{}"
    knowledge_base_id: Optional[int] = None
    knowledge_base_name: Optional[str] = None


class KnowledgeBaseSummary(BaseModel):
    """知识库概览"""
    id: int
    name: str
    description: Optional[str] = None
    is_ephemeral: Optional[bool] = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class FileDiff(BaseModel):
    """文件 Diff 信息（用于前端预览）"""
    file_path: str
    original_content: str
    modified_content: str
    is_truncated: bool = Field(default=False, description="是否为增量预览（大文件截断）")
    added_lines: int = Field(default=0, description="新增行数")
    removed_lines: int = Field(default=0, description="删除行数")


class LaTeXEditResponse(BaseModel):
    """编辑文档响应"""
    success: bool
    changes: List[Dict[str, Any]]
    file_diffs: Optional[List[FileDiff]] = None  # 完整的文件对比（用于 UI diff 预览）
    bibliography_updates: Optional[Dict[str, Any]] = None
    execution_history: List[Dict[str, Any]]
    intent_type: Optional[str] = None
    intent_confidence: Optional[float] = Field(default=None, description="意图识别置信度")
    plan: Optional[Dict[str, Any]] = None
    warnings: Optional[List[str]] = None
    trace_id: Optional[str] = None
    operation_id: Optional[str] = None
    history_path: Optional[str] = None
    episode_id: Optional[str] = None


class OperationSummary(BaseModel):
    """操作历史摘要"""
    operation_id: str
    trace_id: Optional[str] = None
    workspace_id: str
    user_id: int
    timestamp: str
    success: bool
    intent_type: Optional[str] = None
    user_intent: str
    modified_files: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    snapshot: Optional[Dict[str, Any]] = None


class RestoreCheckpointResponse(BaseModel):
    """Checkpoint 文件回滚响应"""

    run_id: str
    restored_files: List[str] = Field(default_factory=list)
    skipped_files: List[str] = Field(default_factory=list)


class ConversationRewindRequest(BaseModel):
    """会话回卷请求（按用户轮次计数）。"""

    keep_user_turns: Optional[int] = Field(
        default=None,
        ge=0,
        description="保留最早的 N 条用户轮次（每轮对应一条 messages 记录）",
    )
    before_message_id: Optional[str] = Field(
        default=None,
        description="保留 message_id 之前的消息（不包含该 message_id 对应轮次）",
    )


class ConversationRewindResponse(BaseModel):
    """会话回卷响应。"""

    session_id: Optional[str] = None
    total_turns: int = 0
    kept_turns: int = 0
    deleted_turns: int = 0


class RevertOperationRequest(BaseModel):
    """回滚操作请求"""
    files: Optional[List[str]] = Field(
        default=None,
        description="仅回滚指定文件；不传则回滚该操作的全部文件",
    )


class RevertOperationResponse(BaseModel):
    """回滚操作响应"""
    operation_id: str
    reverted_files: List[str]
    deleted_files: List[str]
    skipped_files: List[str]


class AgentFeedbackRequest(BaseModel):
    trace_id: str = Field(..., description="Trace ID from Agent response headers")
    rating: Literal["thumbs_up", "thumbs_down"]
    comment: Optional[str] = Field(default=None, max_length=500)


class AsyncRunInteractionRequest(BaseModel):
    interaction_id: Optional[str] = Field(default=None, min_length=1, description="待处理交互 ID")
    confirmation_id: Optional[str] = Field(default=None, min_length=1, description="旧字段兼容：确认 ID")
    decision: str = Field(..., min_length=1, description="用户决策，如 approve/reject")
    note: Optional[str] = Field(default=None, max_length=500, description="可选备注")

    def resolved_interaction_id(self) -> str:
        return str(self.interaction_id or self.confirmation_id or "").strip()


class WorkspaceSummary(BaseModel):
    """工作区概览"""
    workspace_id: str
    name: str
    main_file: Optional[str] = "main.tex"
    file_count: int = 0
    updated_at: float


class WorkspaceDetail(WorkspaceSummary):
    """工作区详情"""
    config: Dict[str, Any] = Field(default_factory=dict)


class FileNode(BaseModel):
    """文件树节点"""
    name: str
    path: str
    type: str  # file or directory
    size: Optional[int] = None
    modified_at: Optional[float] = None
    children: Optional[List["FileNode"]] = None


class WorkspaceFilesResponse(BaseModel):
    """工作区文件列表响应"""
    workspace_id: str
    files: List[FileNode]
    main_file: Optional[str] = "main.tex"
    config: Dict[str, Any] = {}


class FileContentResponse(BaseModel):
    """文件内容响应"""
    path: str
    content: str
    encoding: str = "utf-8"


class UpdateFileRequest(BaseModel):
    """更新文件请求"""
    content: str
    encoding: Optional[str] = "utf-8"


class CreateFileRequest(BaseModel):
    """创建文件/目录"""
    path: str
    type: Literal["file", "directory"] = "file"
    content: Optional[str] = ""
    encoding: Optional[str] = "utf-8"


class RenameFileRequest(BaseModel):
    """重命名或移动文件/目录"""
    source_path: str = Field(..., min_length=1)
    target_path: str = Field(..., min_length=1)


class CreateWorkspaceRequest(BaseModel):
    """创建工作区请求"""
    name: str
    workspace_id: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    template: Optional[str] = None
    initialize_files: bool = True


class UpdateWorkspaceRequest(BaseModel):
    """更新工作区配置"""
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class WorkspaceSessionRequest(BaseModel):
    """绑定或解绑 workspace 的 session_id"""
    session_id: Optional[str] = Field(
        default=None,
        description="绑定的 session_id，传空则解绑",
    )


class CompileRequest(BaseModel):
    """编译请求"""
    main_file: Optional[str] = Field(None, alias="mainFile")
    compiler: Optional[str] = None
    
    class Config:
        populate_by_name = True  # 允许同时接受 main_file 和 mainFile


class CompileResponse(BaseModel):
    """编译响应"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    summary: Optional[str] = None


FileNode.model_rebuild()


# 依赖：获取用户 ID
async def get_user_id(x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    """从请求头获取用户 ID"""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")
    try:
        return int(x_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-User-Id format")


def _user_workspace_root(user_id: int) -> Path:
    """获取用户的工作区根目录"""
    return Path(settings.WORKSPACES_ROOT).joinpath(str(user_id))


def _workspace_path(user_id: int, workspace_id: str) -> Path:
    """获取具体工作区路径"""
    return _user_workspace_root(user_id).joinpath(workspace_id)


def _ensure_workspace(workspace_path: Path):
    if not workspace_path.exists() or not workspace_path.is_dir():
        raise HTTPException(status_code=404, detail="Workspace not found")


def _safe_join(base: Path, relative_path: str) -> Path:
    """安全拼接路径，防止目录逃逸"""
    target = (base / relative_path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(status_code=400, detail="Invalid file path")
    return target


def _history_root(workspace_path: Path) -> Path:
    return workspace_path / ".agent_history"


def _load_operation_snapshot(workspace_path: Path, operation_id: str) -> Dict[str, Any]:
    history_root = _history_root(workspace_path)
    snapshot_path = history_root / "operations" / operation_id / "snapshot.json"
    if not snapshot_path.exists():
        raise HTTPException(status_code=404, detail="Operation snapshot not found")
    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read snapshot %s: %s", snapshot_path, exc)
        raise HTTPException(status_code=500, detail="Failed to read operation snapshot")


def _cleanup_empty_parents(target: Path, workspace_root: Path) -> None:
    current = target.parent
    while current != workspace_root and current.exists():
        try:
            if any(current.iterdir()):
                break
            current.rmdir()
        except Exception:
            break
        current = current.parent


def _lock_root(workspace_path: Path) -> Path:
    return _history_root(workspace_path) / "locks"


def _lock_path(workspace_path: Path) -> Path:
    return _lock_root(workspace_path) / "workspace.lock"


def _read_workspace_lock(workspace_path: Path, prune_stale: bool = True) -> Optional[Dict[str, Any]]:
    lock_path = _lock_path(workspace_path)
    if not lock_path.exists():
        return None
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse lock file %s: %s", lock_path, exc)
        return None

    if prune_stale:
        ttl = settings.AGENT_WORKSPACE_LOCK_TTL or settings.AGENT_TIMEOUT
        created_at = payload.get("created_at") or 0
        if ttl and created_at and time.time() > created_at + ttl:
            try:
                lock_path.unlink()
                logger.warning("Removed stale workspace lock: %s", lock_path)
            except Exception:
                pass
            return None
    return payload


def _acquire_workspace_lock(
    workspace_path: Path,
    lock_id: str,
    user_id: int,
    reason: str,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    existing = _read_workspace_lock(workspace_path, prune_stale=True)
    if existing:
        detail = (
            f"Workspace is locked by agent task (lock_id={existing.get('lock_id')})"
        )
        raise HTTPException(status_code=409, detail=detail)

    lock_root = _lock_root(workspace_path)
    lock_root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    ttl = settings.AGENT_WORKSPACE_LOCK_TTL or settings.AGENT_TIMEOUT
    payload = {
        "lock_id": lock_id,
        "user_id": user_id,
        "trace_id": trace_id,
        "reason": reason,
        "created_at": now,
        "expires_at": now + ttl if ttl else None,
    }
    _lock_path(workspace_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _release_workspace_lock(workspace_path: Path, lock_id: str) -> None:
    lock_path = _lock_path(workspace_path)
    if not lock_path.exists():
        return
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        payload = None
    if payload and payload.get("lock_id") != lock_id:
        return
    try:
        lock_path.unlink()
    except Exception as exc:
        logger.warning("Failed to release workspace lock %s: %s", lock_path, exc)


def _assert_workspace_unlocked(workspace_path: Path) -> None:
    lock_info = _read_workspace_lock(workspace_path, prune_stale=True)
    if lock_info:
        detail = (
            f"Workspace is locked by agent task (lock_id={lock_info.get('lock_id')})"
        )
        raise HTTPException(status_code=409, detail=detail)


DEFAULT_NOTEBOOK_MAIN_FILE = "notes/index.md"
DEFAULT_NOTEBOOK_AUTO_DIR = "_system/auto_notes"
NOTEBOOK_SYSTEM_WRITE_HEADER = "x-notebook-system-write"


def _normalize_relative_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip().strip("/")


def _is_same_or_child_path(path: str, prefix: str) -> bool:
    normalized_path = _normalize_relative_path(path)
    normalized_prefix = _normalize_relative_path(prefix)
    if not normalized_path or not normalized_prefix:
        return False
    return normalized_path == normalized_prefix or normalized_path.startswith(f"{normalized_prefix}/")


def _collect_notebook_locked_paths(config: Dict[str, Any]) -> List[str]:
    workspace_type = str(config.get("workspace_type") or "").strip().lower()
    if workspace_type != "notebook":
        return []

    raw_paths = config.get("notebook_locked_paths") or []
    locked_paths: List[str] = []
    if isinstance(raw_paths, str):
        candidate = _normalize_relative_path(raw_paths)
        if candidate:
            locked_paths.append(candidate)
    elif isinstance(raw_paths, list):
        for item in raw_paths:
            candidate = _normalize_relative_path(str(item or ""))
            if candidate:
                locked_paths.append(candidate)

    auto_dir = _normalize_relative_path(str(config.get("notebook_auto_dir") or ""))
    if auto_dir:
        locked_paths.append(auto_dir)

    if not locked_paths:
        locked_paths.append(DEFAULT_NOTEBOOK_AUTO_DIR)

    deduped: List[str] = []
    for item in locked_paths:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _is_notebook_system_write_request(request: Optional[Request]) -> bool:
    if request is None:
        return False
    marker = str(request.headers.get(NOTEBOOK_SYSTEM_WRITE_HEADER, "")).strip()
    return marker == "1"


def _assert_notebook_path_mutable(
    workspace_path: Path,
    path: str,
    *,
    request: Optional[Request] = None,
    protect_parent_path: bool = False,
    allow_existing_file_edit: bool = False,
    allow_file_delete_in_locked_dir: bool = False,
) -> None:
    config = _load_workspace_config(workspace_path)
    locked_paths = _collect_notebook_locked_paths(config)
    if not locked_paths:
        return
    if _is_notebook_system_write_request(request):
        return

    normalized_target = _normalize_relative_path(path)
    if not normalized_target:
        return

    for locked in locked_paths:
        target_in_locked = _is_same_or_child_path(normalized_target, locked)
        if target_in_locked:
            if allow_existing_file_edit:
                target = _safe_join(workspace_path, normalized_target)
                if target.exists() and target.is_file():
                    continue
            if allow_file_delete_in_locked_dir:
                target = _safe_join(workspace_path, normalized_target)
                if not target.exists() or target.is_file():
                    continue
            raise HTTPException(status_code=403, detail="Target path is managed by Notebook system")
        if protect_parent_path and _is_same_or_child_path(locked, normalized_target):
            raise HTTPException(status_code=403, detail="Target path includes Notebook system directory")


def _infer_primary_format(main_file: str) -> str:
    """Infer primary format from the main file extension."""

    suffix = Path(main_file or "").suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".txt":
        return "plaintext"
    if suffix == ".bib":
        return "bib"
    if suffix == ".tex":
        return "latex"
    return "plaintext"


def _normalize_workspace_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize workspace config with defaults."""
    input_config = dict(config or {})
    base = {
        "workspace_type": "latex",
        "primary_format": "latex",
        "supported_formats": ["latex", "bib"],
        "main_file": "main.tex",
        "bibliography_file": "references.bib",
        "enable_web_search": True,
    }
    base.update(input_config)

    workspace_type = str(base.get("workspace_type") or "").strip().lower()
    if workspace_type == "notebook":
        if "primary_format" not in input_config:
            base["primary_format"] = "markdown"
        if "supported_formats" not in input_config:
            base["supported_formats"] = ["markdown", "plaintext"]
        if "main_file" not in input_config:
            base["main_file"] = DEFAULT_NOTEBOOK_MAIN_FILE
        if "notebook_auto_dir" not in input_config:
            base["notebook_auto_dir"] = DEFAULT_NOTEBOOK_AUTO_DIR
        if "notebook_locked_paths" not in input_config:
            base["notebook_locked_paths"] = [base["notebook_auto_dir"]]

    if not base.get("primary_format"):
        base["primary_format"] = _infer_primary_format(base.get("main_file", ""))
    if not base.get("workspace_type"):
        base["workspace_type"] = "latex" if base["primary_format"] == "latex" else "doc_studio"
    if not base.get("supported_formats"):
        if base["primary_format"] == "latex":
            base["supported_formats"] = ["latex", "bib"]
        elif base["primary_format"] == "markdown":
            base["supported_formats"] = ["markdown", "plaintext"]
        else:
            base["supported_formats"] = [base["primary_format"]]

    if str(base.get("workspace_type") or "").strip().lower() == "notebook":
        base["notebook_locked_paths"] = _collect_notebook_locked_paths(base)
    return base


def _load_workspace_config(workspace_path: Path) -> Dict[str, Any]:
    config_file = workspace_path / ".workspace.json"
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as fh:
                config = json.load(fh)
            return _normalize_workspace_config(config)
        except Exception as exc:
            logger.warning("Failed to load workspace config %s: %s", config_file, exc)
    return _normalize_workspace_config({})


def _write_workspace_config(workspace_path: Path, config: Dict[str, Any]):
    config_file = workspace_path / ".workspace.json"
    with open(config_file, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)


def _get_async_run_dir(workspace_path: Path) -> Path:
    """Return async run directory for a workspace."""

    return workspace_path / ".agent_history" / "async_runs"


# Extensions to snapshot that are considered text (safe to read as UTF-8 / latin-1).
_SNAPSHOT_TEXT_EXTENSIONS = {".tex", ".bib", ".md", ".txt", ".cls", ".sty", ".cfg"}
# Maximum single-file size to include in snapshot (1 MB).
_SNAPSHOT_MAX_FILE_BYTES = 1 * 1024 * 1024


def _take_workspace_snapshot(workspace_path: Path) -> Dict[str, Any]:
    """Scan workspace text files and return a before-snapshot dict.

    Only non-hidden text files are captured.  Binary files and the
    ``.agent_history`` directory are skipped.

    Returns:
        Dict with shape ``{"files": {"relative/path": "content", ...}}``.
    """
    files: Dict[str, str] = {}
    try:
        for item in workspace_path.rglob("*"):
            if not item.is_file():
                continue
            # Skip hidden paths and agent-internal directories
            parts = item.relative_to(workspace_path).parts
            if any(p.startswith(".") for p in parts):
                continue
            if item.suffix.lower() not in _SNAPSHOT_TEXT_EXTENSIONS:
                continue
            try:
                size = item.stat().st_size
            except OSError:
                continue
            if size > _SNAPSHOT_MAX_FILE_BYTES:
                continue
            rel = item.relative_to(workspace_path).as_posix()
            try:
                files[rel] = item.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    files[rel] = item.read_text(encoding="latin-1")
                except Exception:
                    pass
            except Exception:
                pass
    except Exception as exc:
        logger.warning("Failed to take workspace snapshot: %s", exc)
    return {"files": files}


def _format_sse(event_type: str, payload: Dict[str, Any], event_id: Optional[str] = None) -> str:
    """Format SSE event payload."""

    data = json.dumps(payload, ensure_ascii=False, default=str)
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {data}")
    return "\n".join(lines) + "\n\n"


def _record_compile_result(workspace_path: Path, result: Dict[str, Any]):
    """记录最近一次编译结果"""
    result_file = workspace_path / ".compile_result.json"
    payload = {
        "timestamp": time.time(),
        "result": result
    }
    with open(result_file, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def _load_compile_result(workspace_path: Path) -> Optional[Dict[str, Any]]:
    result_file = workspace_path / ".compile_result.json"
    if not result_file.exists():
        return None
    try:
        with open(result_file, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("Failed to read compile result: %s", exc)
        return None


def _normalize_history_file_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_snapshot_path(base: Path, relative_path: str) -> Path:
    target = (base / _normalize_history_file_path(relative_path)).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise ValueError("Invalid snapshot path")
    return target


def _blob_root(history_root: Path) -> Path:
    return history_root / "blobs"


def _blob_path(history_root: Path, digest: str) -> Path:
    return _blob_root(history_root) / digest[:2] / f"{digest}.txt"


def _persist_text_blob(
    history_root: Path,
    content: str,
    encoding: str = "utf-8",
) -> Dict[str, Any]:
    digest = _hash_text(content)
    target = _blob_path(history_root, digest)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = target.with_suffix(".tmp")
        tmp_target.write_text(content, encoding=encoding)
        tmp_target.replace(target)
    return {
        "sha256": digest,
        "size": len(content),
        "path": target.relative_to(history_root).as_posix(),
    }


def _read_blob_text(history_root: Path, digest: str) -> Optional[tuple[str, str]]:
    normalized = str(digest or "").strip().lower()
    if not (len(normalized) == 64 and all(ch in "0123456789abcdef" for ch in normalized)):
        return None
    target = _blob_path(history_root, normalized)
    if not target.exists() or not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8"), "utf-8"
    except UnicodeDecodeError:
        return target.read_text(encoding="latin-1"), "latin-1"


def _load_snapshot_entry_content(
    workspace_path: Path,
    operation_id: str,
    entry: Dict[str, Any],
    version: Literal["before", "after"],
) -> Optional[tuple[str, str]]:
    history_root = _history_root(workspace_path)
    operation_dir = history_root / "operations" / operation_id

    blob_digest = entry.get(f"{version}_blob")
    if blob_digest:
        blob_text = _read_blob_text(history_root, str(blob_digest))
        if blob_text is not None:
            return blob_text

    snapshot_rel_path = entry.get(f"{version}_path")
    if not snapshot_rel_path:
        return None
    snapshot_abs = Path(snapshot_rel_path)
    if not snapshot_abs.is_absolute():
        snapshot_abs = (operation_dir / snapshot_abs).resolve()

    # 兼容旧格式：operation 内相对路径；同时允许新格式记录 history_root 相对路径
    operation_root = operation_dir.resolve()
    history_root_resolved = history_root.resolve()
    if not (
        str(snapshot_abs).startswith(str(operation_root))
        or str(snapshot_abs).startswith(str(history_root_resolved))
    ):
        return None
    if not snapshot_abs.exists() or not snapshot_abs.is_file():
        return None
    try:
        return snapshot_abs.read_text(encoding="utf-8"), "utf-8"
    except UnicodeDecodeError:
        return snapshot_abs.read_text(encoding="latin-1"), "latin-1"


def _load_history_entries(workspace_path: Path) -> List[Dict[str, Any]]:
    history_file = _history_root(workspace_path) / "history.json"
    if not history_file.exists():
        return []
    try:
        payload = json.loads(history_file.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except Exception as exc:
        logger.warning("Failed to load history entries: %s", exc)
        return []


def _write_history_entries(workspace_path: Path, entries: List[Dict[str, Any]]) -> None:
    history_root = _history_root(workspace_path)
    history_root.mkdir(parents=True, exist_ok=True)
    history_file = history_root / "history.json"
    history_file.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _entry_files(entry: Dict[str, Any]) -> List[str]:
    files = entry.get("modified_files") or []
    if not isinstance(files, list):
        return []
    normalized: List[str] = []
    for file_path in files:
        value = _normalize_history_file_path(str(file_path or ""))
        if value:
            normalized.append(value)
    return normalized


def _remove_operation_entry(
    workspace_path: Path,
    entry: Dict[str, Any],
) -> None:
    """Remove operation files and tool logs for a single history entry."""
    history_root = _history_root(workspace_path)
    operations_dir = history_root / "operations"
    operation_id = entry.get("operation_id")
    if operation_id:
        operation_path = operations_dir / f"{operation_id}.json"
        snapshot_dir = operations_dir / operation_id
        try:
            if operation_path.exists():
                operation_path.unlink()
            if snapshot_dir.exists() and snapshot_dir.is_dir():
                shutil.rmtree(snapshot_dir)
        except Exception as exc:
            logger.warning("Failed to remove operation files %s: %s", operation_id, exc)

    for log_path in entry.get("tool_logs", []):
        if not log_path:
            continue
        tool_path = Path(log_path)
        if not tool_path.is_absolute():
            tool_path = history_root / log_path
        try:
            if tool_path.exists():
                tool_path.unlink()
        except Exception as exc:
            logger.warning("Failed to remove tool log %s: %s", tool_path, exc)


def _truncate_history_on_revert(
    workspace_path: Path,
    operation_id: str,
) -> None:
    """
    Cursor-style: 恢复到某版本后，裁剪 timeline，移除该节点及之后的所有版本。
    使 timeline 保持线性，避免「未来版本」与当前文件内容不一致。
    """
    entries = _load_history_entries(workspace_path)
    if not entries:
        return
    idx = next((i for i, e in enumerate(entries) if str(e.get("operation_id") or "") == operation_id), None)
    if idx is None:
        return
    kept = entries[:idx]
    removed = entries[idx:]
    for entry in removed:
        _remove_operation_entry(workspace_path, entry)
    _write_history_entries(workspace_path, kept)
    try:
        _garbage_collect_history_blobs(_history_root(workspace_path), kept)
    except Exception as exc:
        logger.warning("Failed to garbage collect history blobs on revert: %s", exc)


def _prune_operation_history_entries(
    workspace_path: Path,
    history_entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    history_root = _history_root(workspace_path)

    def _remove_entry(entry: Dict[str, Any]) -> None:
        _remove_operation_entry(workspace_path, entry)
        operation_id = entry.get("operation_id")
        if operation_id:
            operation_path = operations_dir / f"{operation_id}.json"
            snapshot_dir = operations_dir / operation_id
            try:
                if operation_path.exists():
                    operation_path.unlink()
                if snapshot_dir.exists() and snapshot_dir.is_dir():
                    shutil.rmtree(snapshot_dir)
            except Exception as exc:
                logger.warning("Failed to remove operation files %s: %s", operation_id, exc)

        for log_path in entry.get("tool_logs", []):
            if not log_path:
                continue
            tool_path = Path(log_path)
            if not tool_path.is_absolute():
                tool_path = history_root / log_path
            try:
                if tool_path.exists():
                    tool_path.unlink()
            except Exception as exc:
                logger.warning("Failed to remove tool log %s: %s", tool_path, exc)

    kept_entries = list(history_entries)
    max_entries = settings.AGENT_HISTORY_MAX_ENTRIES
    if max_entries and max_entries > 0 and len(kept_entries) > max_entries:
        removed_entries = kept_entries[:-max_entries]
        kept_entries = kept_entries[-max_entries:]
        for entry in removed_entries:
            _remove_entry(entry)

    max_entries_per_file = settings.AGENT_HISTORY_MAX_ENTRIES_PER_FILE
    if max_entries_per_file and max_entries_per_file > 0 and kept_entries:
        per_file_counts: Dict[str, int] = defaultdict(int)
        kept_from_newest: List[Dict[str, Any]] = []
        removed_entries: List[Dict[str, Any]] = []
        for entry in reversed(kept_entries):
            files = _entry_files(entry)
            if not files:
                kept_from_newest.append(entry)
                continue
            if all(per_file_counts[file_path] >= max_entries_per_file for file_path in files):
                removed_entries.append(entry)
                continue
            kept_from_newest.append(entry)
            for file_path in set(files):
                per_file_counts[file_path] += 1
        kept_entries = list(reversed(kept_from_newest))
        for entry in removed_entries:
            _remove_entry(entry)

    max_bytes = settings.AGENT_HISTORY_MAX_BYTES
    if max_bytes and max_bytes > 0 and history_root.exists():
        def _dir_size(path: Path) -> int:
            total = 0
            for item in path.rglob("*"):
                if not item.is_file():
                    continue
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
            return total

        current_size = _dir_size(history_root)
        while current_size > max_bytes and kept_entries:
            entry = kept_entries.pop(0)
            _remove_entry(entry)
            current_size = _dir_size(history_root)
            logger.info(
                "History size pruned in router: size=%s bytes (limit=%s)",
                current_size,
                max_bytes,
            )

    try:
        _garbage_collect_history_blobs(history_root, kept_entries)
    except Exception as exc:
        logger.warning("Failed to garbage collect history blobs in router: %s", exc)

    return kept_entries


def _garbage_collect_history_blobs(
    history_root: Path,
    history_entries: List[Dict[str, Any]],
) -> None:
    blob_root = _blob_root(history_root)
    if not blob_root.exists():
        return

    operations_dir = history_root / "operations"
    operation_ids = [
        str(entry.get("operation_id") or "").strip()
        for entry in history_entries
        if str(entry.get("operation_id") or "").strip()
    ]
    referenced: set[str] = set()
    for operation_id in operation_ids:
        snapshot_path = operations_dir / operation_id / "snapshot.json"
        if not snapshot_path.exists():
            continue
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for file_entry in payload.get("files", []) if isinstance(payload, dict) else []:
            if not isinstance(file_entry, dict):
                continue
            for key in ("before_blob", "after_blob"):
                digest = str(file_entry.get(key) or "").strip().lower()
                if len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest):
                    referenced.add(digest)

    for blob_file in blob_root.rglob("*.txt"):
        digest = blob_file.stem.lower()
        if digest not in referenced:
            try:
                blob_file.unlink()
            except Exception as exc:
                logger.warning("Failed to remove stale blob %s: %s", blob_file, exc)

    for subdir in sorted(blob_root.rglob("*"), reverse=True):
        if subdir.is_dir():
            try:
                subdir.rmdir()
            except OSError:
                pass


def _parse_history_timestamp(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        return 0.0


def _get_latest_file_history_entry(
    history_entries: List[Dict[str, Any]],
    file_path: str,
) -> Optional[Dict[str, Any]]:
    normalized_path = _normalize_history_file_path(file_path)
    for entry in reversed(history_entries):
        if normalized_path in _entry_files(entry):
            return entry
    return None


def _should_record_manual_history(
    history_entries: List[Dict[str, Any]],
    file_path: str,
    now_ts: float,
) -> bool:
    latest_entry = _get_latest_file_history_entry(history_entries, file_path)
    if not latest_entry:
        return True
    if (latest_entry.get("source") or "") != "manual":
        return True

    last_ts = _parse_history_timestamp(latest_entry.get("timestamp"))
    if not last_ts:
        return True

    elapsed = max(0.0, now_ts - last_ts)
    force_interval = max(0, int(settings.AGENT_MANUAL_HISTORY_FORCE_INTERVAL_SECONDS or 0))
    min_interval = max(0, int(settings.AGENT_MANUAL_HISTORY_MIN_INTERVAL_SECONDS or 0))

    if force_interval and elapsed >= force_interval:
        return True
    if min_interval and elapsed < min_interval:
        return False
    return True


def _persist_manual_file_history(
    workspace_path: Path,
    workspace_id: str,
    user_id: int,
    file_path: str,
    before_content: Optional[str],
    after_content: str,
    encoding: str = "utf-8",
) -> Optional[str]:
    normalized_file_path = _normalize_history_file_path(file_path)
    before_exists = before_content is not None

    # 内容未变化不记版本
    if before_exists and before_content == after_content:
        return None

    history_entries = _load_history_entries(workspace_path)
    now_ts = time.time()
    if not _should_record_manual_history(history_entries, normalized_file_path, now_ts):
        return None

    operation_id = f"manual-{int(now_ts * 1000)}-{uuid.uuid4().hex[:8]}"
    timestamp = datetime.utcnow().isoformat()
    history_root = _history_root(workspace_path)
    operations_dir = history_root / "operations"
    operation_dir = operations_dir / operation_id

    history_root.mkdir(parents=True, exist_ok=True)
    operations_dir.mkdir(parents=True, exist_ok=True)
    operation_dir.mkdir(parents=True, exist_ok=True)

    persist_after_snapshot = bool(settings.AGENT_HISTORY_PERSIST_AFTER_SNAPSHOT)

    entry: Dict[str, Any] = {
        "path": normalized_file_path,
        "before_exists": before_exists,
        "after_exists": True,
    }
    if before_exists and before_content is not None:
        before_blob = _persist_text_blob(history_root, before_content, encoding=encoding or "utf-8")
        entry["before_blob"] = before_blob["sha256"]
        entry["before_size"] = before_blob["size"]
        entry["before_sha256"] = before_blob["sha256"]

    after_blob_digest = _hash_text(after_content)
    entry["after_size"] = len(after_content)
    entry["after_sha256"] = after_blob_digest
    if persist_after_snapshot:
        after_blob = _persist_text_blob(history_root, after_content, encoding=encoding or "utf-8")
        entry["after_blob"] = after_blob["sha256"]

    manifest = {
        "operation_id": operation_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "timestamp": timestamp,
        "storage": "cas_v1",
        "files": [entry],
    }
    manifest_path = operation_dir / "snapshot.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    snapshot_info = {
        "path": manifest_path.relative_to(history_root).as_posix(),
        "file_count": 1,
        "file_paths": [normalized_file_path],
    }

    summary_record = {
        "operation_id": operation_id,
        "trace_id": None,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "timestamp": timestamp,
        "success": True,
        "intent_type": "manual_edit",
        "user_intent": f"手动编辑保存 {normalized_file_path}",
        "modified_files": [normalized_file_path],
        "tool_logs": [],
        "warnings": [],
        "snapshot": snapshot_info,
        "source": "manual",
    }
    operation_payload = {
        "operation_id": operation_id,
        "trace_id": None,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "timestamp": timestamp,
        "success": True,
        "intent_type": "manual_edit",
        "user_intent": summary_record["user_intent"],
        "execution_history": [],
        "plan": None,
        "warnings": [],
        "tool_logs": [],
        "snapshot": snapshot_info,
        "source": "manual",
    }
    operation_path = operations_dir / f"{operation_id}.json"
    operation_path.write_text(
        json.dumps(operation_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    history_entries.append(summary_record)
    history_entries = _prune_operation_history_entries(workspace_path, history_entries)
    _write_history_entries(workspace_path, history_entries)
    return operation_id


def _take_workspace_snapshot(workspace_path: Path) -> Dict[str, Any]:
    """Read all text-based source files in the workspace as a before-snapshot.

    Only `.tex`, `.bib`, `.md`, `.txt`, `.sty`, `.cls` files are captured.
    Files larger than 512 KB and the total snapshot larger than 6 MB are skipped
    gracefully to avoid bloating the run directory.

    Returns:
        Dict with key ``"files"`` mapping workspace-relative POSIX path → content.
    """

    TEXT_EXTENSIONS = {".tex", ".bib", ".md", ".txt", ".sty", ".cls"}
    MAX_FILE_BYTES = 512 * 1024   # 512 KB per file
    MAX_TOTAL_BYTES = 6 * 1024 * 1024  # 6 MB total

    files: Dict[str, str] = {}
    total_bytes = 0
    workspace_resolved = workspace_path.resolve()

    for src in sorted(workspace_resolved.rglob("*")):
        if not src.is_file():
            continue
        # Skip hidden dirs and agent-internal dirs
        relative = src.relative_to(workspace_resolved)
        parts = relative.parts
        if any(p.startswith(".") for p in parts):
            continue
        if src.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            size = src.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            continue
        if total_bytes + size > MAX_TOTAL_BYTES:
            break
        try:
            content = src.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = src.read_text(encoding="latin-1")
            except Exception:
                continue
        except Exception:
            continue
        rel_posix = relative.as_posix()
        files[rel_posix] = content
        total_bytes += len(content.encode("utf-8"))

    return {
        "files": files,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "taken_at": time.time(),
    }


def _extract_reply_from_result(result: Dict[str, Any]) -> Optional[str]:
    """Extract the final user reply from agent execution history."""
    history = result.get("execution_history") or []
    if not isinstance(history, list):
        return None
    for step in reversed(history):
        if not isinstance(step, dict):
            continue
        if step.get("type") == "finish":
            reply = step.get("content") or (step.get("result") or {}).get("reply")
            if isinstance(reply, str) and reply.strip():
                return reply
        if step.get("tool") == "reply_to_user_tool":
            reply = (step.get("result") or {}).get("reply")
            if isinstance(reply, str) and reply.strip():
                return reply
    return None


def _normalize_image_attachments(image_attachments: Any) -> List[Dict[str, Any]]:
    """Normalize image attachments for retrieval_content persistence."""
    if not isinstance(image_attachments, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(image_attachments):
        if not isinstance(item, dict):
            continue
        data_url = item.get("data_url") or item.get("dataUrl")
        if not isinstance(data_url, str) or not data_url.strip():
            continue
        name = item.get("name")
        mime_type = item.get("mime_type") or item.get("mimeType") or "image/png"
        size_raw = item.get("size")
        try:
            size = max(int(size_raw or 0), 0)
        except Exception:
            size = 0
        normalized.append(
            {
                "id": str(item.get("id") or f"image-{idx + 1}"),
                "name": str(name or f"image-{idx + 1}"),
                "mime_type": str(mime_type),
                "size": size,
                "data_url": data_url,
            }
        )
        if len(normalized) >= 4:
            break
    return normalized


def _normalize_selection_fragments(selection_fragments: Any) -> List[Dict[str, Any]]:
    """Normalize selection fragments for retrieval_content persistence."""
    if not isinstance(selection_fragments, list):
        return []
    normalized: List[Dict[str, Any]] = []
    max_fragments = 16
    max_text_len = 8000
    for idx, item in enumerate(selection_fragments):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if len(text) > max_text_len:
            text = f"{text[:max_text_len]}..."
        placeholder_raw = str(item.get("placeholder") or "").strip()
        if re.fullmatch(r"@selection\d+", placeholder_raw):
            placeholder = placeholder_raw
        else:
            placeholder = f"@selection{idx + 1}"
        try:
            start = max(int(item.get("start") or 0), 0)
        except Exception:
            start = 0
        try:
            end = int(item.get("end") or start)
        except Exception:
            end = start
        end = max(end, start)
        file_path_raw = item.get("file_path") or item.get("filePath")
        file_path = str(file_path_raw).strip() if file_path_raw else ""
        normalized_item: Dict[str, Any] = {
            "id": str(item.get("id") or idx + 1),
            "start": start,
            "end": end,
            "text": text,
            "placeholder": placeholder,
        }
        if file_path:
            normalized_item["file_path"] = file_path
        normalized.append(normalized_item)
        if len(normalized) >= max_fragments:
            break
    return normalized


def _normalize_file_mentions(file_mentions: Any) -> List[Dict[str, Any]]:
    """Normalize file mentions from frontend payload or retrieval content."""
    if not isinstance(file_mentions, list):
        return []
    normalized: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()
    max_mentions = 8
    for idx, item in enumerate(file_mentions):
        if not isinstance(item, dict):
            continue
        path_raw = item.get("file_path") or item.get("filePath") or item.get("path")
        file_path = str(path_raw or "").strip().replace("\\", "/").strip("/")
        if not file_path or file_path in seen_paths:
            continue
        seen_paths.add(file_path)
        placeholder_raw = str(item.get("placeholder") or "").strip()
        placeholder = placeholder_raw if re.fullmatch(r"@file\d+", placeholder_raw) else f"@file{idx + 1}"
        normalized_item: Dict[str, Any] = {
            "id": str(item.get("id") or idx + 1),
            "file_path": file_path,
            "placeholder": placeholder,
        }
        strategy = str(item.get("strategy") or "").strip()
        if strategy:
            normalized_item["strategy"] = strategy
        total_chars_raw = item.get("total_chars") if item.get("total_chars") is not None else item.get("totalChars")
        total_lines_raw = item.get("total_lines") if item.get("total_lines") is not None else item.get("totalLines")
        try:
            total_chars = max(int(total_chars_raw or 0), 0)
        except Exception:
            total_chars = 0
        try:
            total_lines = max(int(total_lines_raw or 0), 0)
        except Exception:
            total_lines = 0
        file_hash_raw = str(item.get("file_hash") or item.get("fileHash") or item.get("hash") or "").strip().lower()
        file_size_raw = item.get("file_size") if item.get("file_size") is not None else item.get("fileSize")
        try:
            file_size = max(int(file_size_raw or 0), 0)
        except Exception:
            file_size = 0
        if total_chars > 0:
            normalized_item["total_chars"] = total_chars
        if total_lines > 0:
            normalized_item["total_lines"] = total_lines
        if re.fullmatch(r"[0-9a-f]{64}", file_hash_raw):
            normalized_item["file_hash"] = file_hash_raw
        if file_size > 0:
            normalized_item["file_size"] = file_size
        normalized.append(normalized_item)
        if len(normalized) >= max_mentions:
            break
    return normalized


def _extract_query_keywords(user_text: str) -> List[str]:
    """Extract simple lexical keywords for file mention condensation."""
    text = str(user_text or "")
    if not text:
        return []
    english_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
    cjk_tokens = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    candidates = [*english_tokens, *cjk_tokens]
    seen: set[str] = set()
    out: List[str] = []
    for token in candidates:
        normalized = token.strip()
        lowered = normalized.lower()
        if not normalized or lowered in seen:
            continue
        seen.add(lowered)
        out.append(normalized)
        if len(out) >= 12:
            break
    return out


def _looks_like_edit_intent(user_text: str) -> bool:
    """Heuristic gate for edit/rewrite intents."""
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    cjk_edit_hints = ("修改", "改写", "重写", "润色", "优化", "改成", "替换")
    if any(hint in text for hint in cjk_edit_hints):
        return True
    return bool(re.search(r"\b(rewrite|edit|refactor|polish|replace|revise|modify)\b", text))


def _strip_line_number_prefix(content_excerpt: str) -> str:
    """Best-effort de-numbering for `Lxx:` style file excerpts."""
    text = str(content_excerpt or "")
    if not text:
        return ""
    cleaned_lines: List[str] = []
    for line in text.splitlines():
        normalized = line.strip("\r")
        if not normalized:
            cleaned_lines.append("")
            continue
        if re.match(r"^\[(HEAD|TAIL|KEYWORD_HITS|HIT)\b", normalized):
            continue
        if normalized in {"...", "[TRUNCATED]", "[TRUNCATED_BY_BUDGET]"}:
            continue
        normalized = re.sub(r"^\s*L\d+\s*:\s?", "", normalized)
        cleaned_lines.append(normalized)
    return "\n".join(cleaned_lines).strip()


def _build_virtual_selections_from_full_file_mentions(
    *,
    file_mentions: List[Dict[str, Any]],
    user_intent: str,
) -> List[Dict[str, Any]]:
    """Build runtime-only synthetic selections to align @file with ctrl+L path."""
    if not _looks_like_edit_intent(user_intent):
        return []
    if not isinstance(file_mentions, list) or len(file_mentions) != 1:
        return []

    mention = file_mentions[0] if isinstance(file_mentions[0], dict) else {}
    strategy = str(mention.get("strategy") or "").strip().lower()
    if strategy != "full":
        return []

    file_path = str(mention.get("file_path") or "").strip()
    if not file_path:
        return []

    try:
        total_chars = int(mention.get("total_chars") or 0)
    except Exception:
        total_chars = 0
    if total_chars <= 0 or total_chars > 12000:
        return []

    excerpt = str(mention.get("content_excerpt") or "")
    raw_text = _strip_line_number_prefix(excerpt)
    if not raw_text:
        return []

    synthetic = _normalize_selection_fragments(
        [
            {
                "id": "synthetic-file-mention-1",
                "start": 0,
                "end": total_chars,
                "text": raw_text,
                "file_path": file_path,
                "placeholder": "@selection1",
            }
        ]
    )
    return synthetic


def _read_file_for_mention(target: Path) -> Optional[str]:
    """Read a workspace text file for mention injection with basic safeguards."""
    if not target.exists() or not target.is_file():
        return None
    try:
        size = target.stat().st_size
    except OSError:
        return None
    if size > 2 * 1024 * 1024:
        return None
    content: Optional[str] = None
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = target.read_text(encoding="latin-1")
        except Exception:
            content = None
    except Exception:
        content = None
    if content is None:
        return None
    if "\x00" in content:
        return None
    return content


def _condense_file_mention_content(
    *,
    file_path: str,
    content: str,
    keywords: List[str],
    max_excerpt_chars: int = 12000,
) -> Dict[str, Any]:
    """Condense file content to fit prompt budget while preserving useful anchors."""
    def _render_numbered_lines(source_lines: List[str], start_idx: int, end_idx: int) -> str:
        if start_idx >= end_idx:
            return ""
        numbered: List[str] = []
        for idx in range(start_idx, end_idx):
            line_no = idx + 1
            numbered.append(f"L{line_no}: {source_lines[idx]}")
        return "\n".join(numbered)

    text = str(content or "")
    lines = text.splitlines()
    total_lines = len(lines)
    total_chars = len(text)
    if total_chars <= 4500:
        full_numbered = _render_numbered_lines(lines, 0, total_lines) if total_lines > 0 else text
        return {
            "strategy": "full",
            "total_chars": total_chars,
            "total_lines": total_lines,
            "content_excerpt": full_numbered or text,
        }

    head_end = min(total_lines, 80)
    tail_start = max(0, total_lines - 60) if total_lines > 80 else total_lines
    keyword_hit_indexes: List[int] = []
    lowered_lines = [line.lower() for line in lines]
    keyword_tokens = [kw.lower() for kw in keywords if kw]
    if keyword_tokens:
        for idx, line in enumerate(lowered_lines):
            if any(token in line for token in keyword_tokens):
                keyword_hit_indexes.append(idx)
            if len(keyword_hit_indexes) >= 36:
                break

    keyword_blocks: List[str] = []
    seen_idx: set[int] = set()
    for idx in keyword_hit_indexes:
        start = max(0, idx - 2)
        end = min(total_lines, idx + 3)
        block_lines: List[str] = []
        for line_idx in range(start, end):
            if line_idx in seen_idx:
                continue
            seen_idx.add(line_idx)
            block_lines.append(f"L{line_idx + 1}: {lines[line_idx]}")
        if block_lines:
            keyword_blocks.append(f"[HIT L{start + 1}-L{end}]\n" + "\n".join(block_lines))

    sections: List[str] = []
    if head_end > 0:
        sections.append(f"[HEAD L1-L{head_end}]\n" + _render_numbered_lines(lines, 0, head_end))
    if keyword_blocks:
        sections.append("[KEYWORD_HITS]\n" + "\n...\n".join(keyword_blocks))
    if tail_start < total_lines:
        sections.append(
            f"[TAIL L{tail_start + 1}-L{total_lines}]\n"
            + _render_numbered_lines(lines, tail_start, total_lines)
        )
    excerpt = "\n\n".join(sections).strip()
    if not excerpt:
        excerpt = text[:max_excerpt_chars]
    if len(excerpt) > max_excerpt_chars:
        excerpt = excerpt[:max_excerpt_chars] + "\n...\n[TRUNCATED]"
    return {
        "strategy": "head_tail_keyword_condensed",
        "total_chars": total_chars,
        "total_lines": total_lines,
        "content_excerpt": excerpt,
        "condensed": True,
        "file_path": file_path,
    }


def _materialize_file_mentions(
    *,
    workspace_path: Path,
    raw_mentions: Any,
    user_intent: str,
) -> List[Dict[str, Any]]:
    """Resolve file mentions to safe, condensed prompt payload."""
    normalized = _normalize_file_mentions(raw_mentions)
    if not normalized:
        return []
    keywords = _extract_query_keywords(user_intent)
    materialized: List[Dict[str, Any]] = []
    total_excerpt_budget = 24000
    used_excerpt_chars = 0
    for mention in normalized:
        file_path = str(mention.get("file_path") or "").strip()
        if not file_path:
            continue
        try:
            target = _safe_join(workspace_path, file_path)
        except HTTPException:
            continue
        try:
            file_size = max(int(target.stat().st_size), 0)
        except Exception:
            file_size = 0
        rel_parts = target.relative_to(workspace_path).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        content = _read_file_for_mention(target)
        if not content:
            continue
        file_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
        condensed = _condense_file_mention_content(
            file_path=file_path,
            content=content,
            keywords=keywords,
        )
        excerpt = str(condensed.get("content_excerpt") or "")
        if not excerpt:
            continue
        remaining_budget = total_excerpt_budget - used_excerpt_chars
        if remaining_budget <= 256:
            break
        if len(excerpt) > remaining_budget:
            excerpt = excerpt[:remaining_budget] + "\n...\n[TRUNCATED_BY_BUDGET]"
        used_excerpt_chars += len(excerpt)
        item = {
            "id": mention.get("id"),
            "file_path": file_path,
            "placeholder": mention.get("placeholder"),
            "strategy": condensed.get("strategy"),
            "total_chars": condensed.get("total_chars"),
            "total_lines": condensed.get("total_lines"),
            "file_hash": file_hash,
            "file_size": file_size,
            "content_excerpt": excerpt,
        }
        materialized.append(item)
    return materialized


async def _persist_session_message(
    *,
    workspace_id: str,
    user_id: int,
    session_id: Optional[str],
    user_question: str,
    result: Dict[str, Any],
    knowledge_base_id: Optional[int],
    image_attachments: Optional[List[Dict[str, Any]]] = None,
    selections: Optional[List[Dict[str, Any]]] = None,
    file_mentions: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Persist a Doc Studio interaction to the shared session."""
    if not session_id:
        return
    try:
        session_detail = await get_rag_api_client().get_session_detail(
            session_id=str(session_id),
            user_id=user_id,
        )
        session_surface = str((session_detail or {}).get("surface") or "deep_chat").strip().lower()
        if session_surface != "doc_studio":
            logger.warning(
                "Skip persisting Doc Studio message due to session surface mismatch: session_id=%s surface=%s",
                session_id,
                session_surface,
            )
            return
    except Exception as exc:
        logger.warning(
            "Skip persisting Doc Studio message because session surface check failed: session_id=%s error=%s",
            session_id,
            exc,
        )
        return
    reply = _extract_reply_from_result(result)
    if not reply:
        return
    retrieval_content = {
        "source": "doc_studio",
        "workspace_id": workspace_id,
        "knowledge_base_id": knowledge_base_id,
        "intent_type": result.get("intent_type"),
        "trace_id": result.get("trace_id"),
        "run_id": result.get("run_id"),
    }
    persisted_images = _normalize_image_attachments(image_attachments)
    if persisted_images:
        retrieval_content["images"] = persisted_images
    persisted_selections = _normalize_selection_fragments(selections)
    if persisted_selections:
        retrieval_content["selections"] = persisted_selections
        retrieval_content["selection"] = {
            "start": persisted_selections[0].get("start"),
            "end": persisted_selections[0].get("end"),
            "text": persisted_selections[0].get("text"),
        }
    persisted_file_mentions = _normalize_file_mentions(file_mentions)
    if persisted_file_mentions:
        retrieval_content["file_mentions"] = persisted_file_mentions
    try:
        rag_client = get_rag_api_client()
        await rag_client.append_message(
            session_id=str(session_id),
            user_id=user_id,
            user_question=user_question,
            model_answer=reply,
            retrieval_content=retrieval_content,
            source="doc_studio",
            trace_id=result.get("trace_id"),
        )
    except Exception as exc:
        logger.error(
            "Failed to persist Doc Studio message (对话将无法在切换/刷新后恢复): session_id=%s, error=%s",
            session_id,
            exc,
            exc_info=True,
        )


def _build_file_nodes(base: Path, current: Optional[Path] = None) -> List[Dict[str, Any]]:
    """构建文件树"""
    current = current or base
    nodes: List[Dict[str, Any]] = []
    try:
        children = sorted(
            [p for p in current.iterdir() if not p.name.startswith(".")],
            key=lambda p: (p.is_file(), p.name.lower())
        )
    except FileNotFoundError:
        return nodes

    for entry in children:
        rel_path = entry.relative_to(base).as_posix()
        stat = entry.stat()
        if entry.is_dir():
            nodes.append({
                "name": entry.name,
                "path": rel_path,
                "type": "directory",
                "modified_at": stat.st_mtime,
                "children": _build_file_nodes(base, entry)
            })
        else:
            nodes.append({
                "name": entry.name,
                "path": rel_path,
                "type": "file",
                "size": stat.st_size,
                "modified_at": stat.st_mtime
            })
    return nodes


@router.get("", response_model=List[WorkspaceSummary])
async def list_workspaces(user_id: int = Depends(get_user_id)):
    """
    获取当前用户的所有工作区
    """
    root = _user_workspace_root(user_id)
    root.mkdir(parents=True, exist_ok=True)
    if not root.exists():
        return []
    
    summaries: List[WorkspaceSummary] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        config = _load_workspace_config(entry)
        stat = entry.stat()
        file_count = sum(1 for _ in entry.rglob("*") if _.is_file())
        summaries.append(
            WorkspaceSummary(
                workspace_id=entry.name,
                name=config.get("name") or entry.name,
                main_file=config.get("main_file", "main.tex"),
                file_count=file_count,
                updated_at=stat.st_mtime
            )
        )
    return summaries


@router.post("", response_model=WorkspaceDetail)
async def create_workspace(
    payload: CreateWorkspaceRequest,
    user_id: int = Depends(get_user_id)
):
    """创建工作区"""
    root = _user_workspace_root(user_id)
    root.mkdir(parents=True, exist_ok=True)
    workspace_id = payload.workspace_id or uuid.uuid4().hex
    workspace_path = _workspace_path(user_id, workspace_id)
    if workspace_path.exists():
        raise HTTPException(status_code=400, detail="Workspace already exists")
    
    workspace_path.mkdir(parents=True, exist_ok=False)

    config = _normalize_workspace_config(payload.config)
    config["name"] = payload.name
    main_file_name = config.get("main_file", "main.tex")
    primary_format = config.get("primary_format")
    is_latex = primary_format == "latex" or Path(main_file_name).suffix.lower() == ".tex"
    should_initialize_files = bool(payload.initialize_files)

    if should_initialize_files and is_latex:
        (workspace_path / "sections").mkdir(exist_ok=True)
        (workspace_path / "figures").mkdir(exist_ok=True)
    
    _write_workspace_config(workspace_path, config)
    
    if should_initialize_files:
        main_file = workspace_path / main_file_name
        if not main_file.exists():
            main_file.parent.mkdir(parents=True, exist_ok=True)
            if is_latex:
                main_file.write_text(
                    "\\documentclass{article}\n\\begin{document}\nHello Doc Studio!\n\\end{document}\n",
                    encoding="utf-8",
                )
            else:
                suffix = main_file.suffix.lower()
                if suffix in {".md", ".markdown"}:
                    main_file.write_text(
                        f"# {payload.name}\n\nStart writing here.\n",
                        encoding="utf-8",
                    )
                else:
                    main_file.write_text(f"{payload.name}\n", encoding="utf-8")

        bibliography_file = config.get("bibliography_file")
        if is_latex and bibliography_file:
            references_file = workspace_path / bibliography_file
            if not references_file.exists():
                references_file.write_text("% references.bib\n", encoding="utf-8")
    
    stat = workspace_path.stat()
    return WorkspaceDetail(
        workspace_id=workspace_id,
        name=config.get("name", workspace_id),
        main_file=config.get("main_file", "main.tex"),
        file_count=sum(1 for _ in workspace_path.rglob("*") if _.is_file()),
        updated_at=stat.st_mtime,
        config=config
    )


@router.get("/{workspace_id}", response_model=WorkspaceDetail)
async def get_workspace(
    workspace_id: str,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)
    config = _load_workspace_config(workspace_path)
    stat = workspace_path.stat()
    file_count = sum(1 for _ in workspace_path.rglob("*") if _.is_file())
    return WorkspaceDetail(
        workspace_id=workspace_id,
        name=config.get("name", workspace_id),
        main_file=config.get("main_file", "main.tex"),
        file_count=file_count,
        updated_at=stat.st_mtime,
        config=config
    )


@router.put("/{workspace_id}", response_model=WorkspaceDetail)
async def update_workspace(
    workspace_id: str,
    payload: UpdateWorkspaceRequest,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    config = _load_workspace_config(workspace_path)
    if payload.name:
        config["name"] = payload.name
    if payload.config:
        config.update(payload.config)
    _write_workspace_config(workspace_path, config)
    stat = workspace_path.stat()
    file_count = sum(1 for _ in workspace_path.rglob("*") if _.is_file())
    return WorkspaceDetail(
        workspace_id=workspace_id,
        name=config.get("name", workspace_id),
        main_file=config.get("main_file", "main.tex"),
        file_count=file_count,
        updated_at=stat.st_mtime,
        config=config
    )


@router.put("/{workspace_id}/session", response_model=WorkspaceDetail)
async def bind_workspace_session(
    workspace_id: str,
    payload: WorkspaceSessionRequest,
    user_id: int = Depends(get_user_id),
):
    """绑定或解绑 workspace 的 session_id（最小实现，写入 .workspace.json）。
    绑定时会自动将 session_id 加入 session_ids 列表，用于 Cursor 风格多对话管理。"""
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)

    config = _load_workspace_config(workspace_path)
    if payload.session_id:
        try:
            rag_client = get_rag_api_client()
            detail = await rag_client.get_session_detail(str(payload.session_id), user_id=user_id)
            session_surface = str((detail or {}).get("surface") or "deep_chat").strip().lower()
            if session_surface != "doc_studio":
                raise HTTPException(
                    status_code=400,
                    detail="只能绑定 Doc Studio 会话（surface=doc_studio）",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(
                "Failed to validate session surface while binding workspace session: workspace=%s session=%s error=%s",
                workspace_id,
                payload.session_id,
                exc,
            )
            raise HTTPException(status_code=502, detail="会话校验失败，请稍后重试")
        config["session_id"] = payload.session_id
        session_ids = config.get("session_ids") or []
        if not isinstance(session_ids, list):
            session_ids = []
        if payload.session_id not in session_ids:
            # 若存在占位符 __new__，则替换（新对话 → 真实会话）；否则追加到末尾（Cursor 风格：新对话在右侧）
            if "__new__" in session_ids:
                session_ids = [payload.session_id if x == "__new__" else x for x in session_ids]
            else:
                session_ids.append(payload.session_id)
            config["session_ids"] = session_ids
    else:
        config.pop("session_id", None)
        # 切换到新对话时，在 session_ids 末尾追加占位符 __new__（Cursor 风格：新对话在右侧）
        session_ids = config.get("session_ids") or []
        if not isinstance(session_ids, list):
            session_ids = []
        if "__new__" not in session_ids:
            session_ids.append("__new__")
            config["session_ids"] = session_ids
    _write_workspace_config(workspace_path, config)

    stat = workspace_path.stat()
    file_count = sum(1 for _ in workspace_path.rglob("*") if _.is_file())
    return WorkspaceDetail(
        workspace_id=workspace_id,
        name=config.get("name", workspace_id),
        main_file=config.get("main_file", "main.tex"),
        file_count=file_count,
        updated_at=stat.st_mtime,
        config=config,
    )


@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    try:
        shutil.rmtree(workspace_path)
    except Exception as exc:
        logger.error("Failed to delete workspace %s: %s", workspace_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete workspace")
    return {"deleted": True, "workspace_id": workspace_id}


@router.get("/{workspace_id}/messages")
async def get_workspace_messages(
    workspace_id: str,
    session_id: str = Query(..., description="会话 ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
    user_id: int = Depends(get_user_id),
):
    """
    获取工作区当前会话的消息列表（通过主后端内部 API，确保落库消息可正确加载）
    """
    if not session_id:
        return {"total": 0, "page": 1, "pageSize": page_size, "items": []}
    try:
        rag_client = get_rag_api_client()
        detail = await rag_client.get_session_detail(
            session_id=str(session_id),
            user_id=user_id,
        )
        session_surface = str((detail or {}).get("surface") or "deep_chat").strip().lower()
        if session_surface != "doc_studio":
            return {"total": 0, "page": 1, "pageSize": page_size, "items": []}
        data = await rag_client.list_messages(
            session_id=session_id,
            user_id=user_id,
            page=page,
            page_size=page_size,
        )
        return data
    except Exception as exc:
        logger.warning("Failed to load workspace messages: %s", exc)
        return {"total": 0, "page": 1, "pageSize": page_size, "items": []}


@router.get("/{workspace_id}/messages/debug")
async def get_workspace_messages_debug(
    workspace_id: str,
    session_id: str = Query(..., description="会话 ID"),
    user_id: int = Depends(get_user_id),
):
    """
    调试接口：返回 Agent 消息的原始内容及换行分析，用于排查 Markdown 渲染间距问题。
    用法：GET /api/doc-studio/workspaces/{id}/messages/debug?session_id=xxx
    """
    if not session_id:
        return {"error": "session_id required", "items": []}
    try:
        rag_client = get_rag_api_client()
        detail = await rag_client.get_session_detail(
            session_id=str(session_id),
            user_id=user_id,
        )
        session_surface = str((detail or {}).get("surface") or "deep_chat").strip().lower()
        if session_surface != "doc_studio":
            return {"error": "session surface mismatch", "items": []}
        data = await rag_client.list_messages(
            session_id=session_id,
            user_id=user_id,
            page=1,
            page_size=50,
        )
        items = data.get("items") or []
        debug_items = []
        for m in items:
            ans = m.get("model_answer") or ""
            nl_count = ans.count("\n")
            double_nl = ans.count("\n\n")
            triple_plus = len(list(re.finditer(r"\n{3,}", ans)))
            debug_items.append({
                "message_id": m.get("message_id"),
                "content_length": len(ans),
                "newline_count": nl_count,
                "double_newline_count": double_nl,
                "triple_plus_newline_count": triple_plus,
                "raw_repr_sample": repr(ans[:500]) if len(ans) > 500 else repr(ans),
                "raw_with_markers": ans[:800].replace("\n", "↵\n").replace("\r", "↵"),
            })
        return {"session_id": session_id, "items": debug_items}
    except Exception as exc:
        logger.warning("Failed to load messages for debug: %s", exc)
        return {"error": str(exc), "items": []}


@router.get("/{workspace_id}/files", response_model=WorkspaceFilesResponse)
async def get_workspace_files(
    workspace_id: str,
    user_id: int = Depends(get_user_id),
):
    """
    获取工作区文件树和配置
    """
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    files = _build_file_nodes(workspace_path)
    config = _load_workspace_config(workspace_path)
    return WorkspaceFilesResponse(
        workspace_id=workspace_id,
        files=files,
        main_file=config.get("main_file", "main.tex"),
        config=config
    )


@router.get("/{workspace_id}/files/{file_path:path}", response_model=FileContentResponse)
async def read_file_content(
    workspace_id: str,
    file_path: str,
    user_id: int = Depends(get_user_id)
):
    """
    读取工作区文件内容
    """
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    target = _safe_join(workspace_path, file_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        content = target.read_text(encoding="utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = target.read_text(encoding="latin-1")
        encoding = "latin-1"
    
    return FileContentResponse(path=file_path, content=content, encoding=encoding)


@router.post("/{workspace_id}/files")
async def create_file_or_directory(
    workspace_id: str,
    payload: CreateFileRequest,
    request: Request,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)
    target = _safe_join(workspace_path, payload.path)
    _assert_notebook_path_mutable(workspace_path, payload.path, request=request)
    if target.exists():
        raise HTTPException(status_code=400, detail="Target already exists")
    if payload.type == "directory":
        target.mkdir(parents=True, exist_ok=False)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.content or "", encoding=payload.encoding or "utf-8")
    return {"path": payload.path, "type": payload.type}


@router.post("/{workspace_id}/files/rename")
async def rename_file_or_directory(
    workspace_id: str,
    payload: RenameFileRequest,
    request: Request,
    user_id: int = Depends(get_user_id),
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)

    source_rel = payload.source_path.strip().strip("/")
    target_rel = payload.target_path.strip().strip("/")
    if not source_rel or not target_rel:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if source_rel == target_rel:
        raise HTTPException(status_code=400, detail="Target path must be different from source path")

    source = _safe_join(workspace_path, source_rel)
    target = _safe_join(workspace_path, target_rel)
    if source == workspace_path or target == workspace_path:
        raise HTTPException(status_code=400, detail="Invalid file path")
    _assert_notebook_path_mutable(
        workspace_path,
        source_rel,
        request=request,
        protect_parent_path=True,
    )
    _assert_notebook_path_mutable(workspace_path, target_rel, request=request)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Source path not found")
    if target.exists():
        raise HTTPException(status_code=400, detail="Target already exists")

    if source.is_dir():
        try:
            target.relative_to(source)
            raise HTTPException(status_code=400, detail="Cannot move directory into itself")
        except ValueError:
            pass

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(source), str(target))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to rename path in workspace %s: %s", workspace_id, exc)
        raise HTTPException(status_code=500, detail="Failed to rename path")

    _cleanup_empty_parents(source, workspace_path)
    return {
        "moved": True,
        "source_path": source_rel,
        "target_path": target_rel,
        "type": "directory" if target.is_dir() else "file",
    }


@router.put("/{workspace_id}/files/{file_path:path}")
async def update_file_content(
    workspace_id: str,
    file_path: str,
    payload: UpdateFileRequest,
    request: Request,
    user_id: int = Depends(get_user_id)
):
    """
    更新工作区文件
    """
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)
    _assert_notebook_path_mutable(
        workspace_path,
        file_path,
        request=request,
        allow_existing_file_edit=True,
    )
    target = _safe_join(workspace_path, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoding = payload.encoding or "utf-8"
    before_content: Optional[str] = None
    if target.exists():
        try:
            before_content = target.read_text(encoding=encoding)
        except UnicodeDecodeError:
            before_content = target.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("Failed to read original file before save %s: %s", target, exc)
            before_content = None
    try:
        target.write_text(payload.content, encoding=encoding)
    except Exception as exc:
        logger.error("Failed to write file %s: %s", target, exc)
        raise HTTPException(status_code=500, detail="Failed to save file")

    try:
        _persist_manual_file_history(
            workspace_path=workspace_path,
            workspace_id=workspace_id,
            user_id=user_id,
            file_path=file_path,
            before_content=before_content,
            after_content=payload.content,
            encoding=encoding,
        )
    except Exception as exc:
        logger.warning("Failed to persist manual history for %s: %s", file_path, exc)
    
    stat = target.stat()
    return {
        "path": file_path,
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
        "encoding": encoding
    }


@router.delete("/{workspace_id}/files/{file_path:path}")
async def delete_file(
    workspace_id: str,
    file_path: str,
    request: Request,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)
    _assert_notebook_path_mutable(
        workspace_path,
        file_path,
        request=request,
        protect_parent_path=True,
        allow_file_delete_in_locked_dir=True,
    )
    target = _safe_join(workspace_path, file_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"deleted": True, "path": file_path}


@router.post("/{workspace_id}/files/upload")
async def upload_file(
    workspace_id: str,
    request: Request,
    file: UploadFile = File(...),
    directory: Optional[str] = Form(None),
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)

    # 处理目录路径
    if directory:
        # 清理路径，移除前导/尾随斜杠
        directory = directory.strip().strip('/')
        _assert_notebook_path_mutable(workspace_path, directory, request=request)
        dir_path = _safe_join(workspace_path, directory)
    else:
        dir_path = workspace_path

    dir_path.mkdir(parents=True, exist_ok=True)

    # 浏览器在目录上传场景下可能把相对路径塞进 filename（例如 "root/figures/a.eps"）；
    # 这里统一只取 basename，目录结构完全由 directory 参数决定，避免重复拼接和路径穿透。
    raw_filename = str(file.filename or "").strip().replace("\\", "/")
    safe_filename = Path(raw_filename).name
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid upload filename")

    target = dir_path / safe_filename
    relative_target = target.relative_to(workspace_path).as_posix()
    _assert_notebook_path_mutable(workspace_path, relative_target, request=request)
    content = await file.read()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    relative_path = target.relative_to(workspace_path).as_posix()
    logger.info(
        f"📤 文件上传成功: workspace={workspace_id}, "
        f"directory={directory}, filename={safe_filename}, "
        f"size={len(content)} bytes, path={relative_path}"
    )

    # 验证文件确实已保存
    if not target.exists():
        logger.error(f"❌ 文件保存失败: {target}")
        raise HTTPException(status_code=500, detail="文件保存失败")

    return {"path": relative_path, "size": len(content)}


@router.get("/{workspace_id}/download")
async def download_file(
    workspace_id: str,
    file_path: str,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    target = _safe_join(workspace_path, file_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name)


@router.get("/{workspace_id}/operations", response_model=List[OperationSummary])
async def list_operations(
    workspace_id: str,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    history_file = _history_root(workspace_path) / "history.json"
    if not history_file.exists():
        return []
    try:
        payload = json.loads(history_file.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        return payload
    except Exception as exc:
        logger.warning("Failed to read history file: %s", exc)
        return []


@router.get("/{workspace_id}/operations/{operation_id}")
async def get_operation_detail(
    workspace_id: str,
    operation_id: str,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    operation_path = _history_root(workspace_path) / "operations" / f"{operation_id}.json"
    if not operation_path.exists():
        raise HTTPException(status_code=404, detail="Operation not found")
    try:
        payload = json.loads(operation_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read operation file: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read operation detail")
    snapshot_path = _history_root(workspace_path) / "operations" / operation_id / "snapshot.json"
    if snapshot_path.exists():
        try:
            payload["snapshot"] = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return payload


@router.get("/{workspace_id}/operations/{operation_id}/snapshot", response_model=FileContentResponse)
async def get_operation_snapshot_file(
    workspace_id: str,
    operation_id: str,
    file_path: str,
    version: Literal["before", "after"] = "before",
    user_id: int = Depends(get_user_id),
):
    """读取操作快照文件内容"""
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    snapshot = _load_operation_snapshot(workspace_path, operation_id)
    normalized_path = _normalize_history_file_path(file_path)
    entries = snapshot.get("files") or []
    matched_entry = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_path = _normalize_history_file_path(str(entry.get("path") or ""))
        if entry_path == normalized_path:
            matched_entry = entry
            break
    if not matched_entry:
        raise HTTPException(status_code=404, detail="Snapshot file not found")

    snapshot_content = _load_snapshot_entry_content(
        workspace_path=workspace_path,
        operation_id=operation_id,
        entry=matched_entry,
        version=version,
    )
    if snapshot_content is None:
        raise HTTPException(status_code=404, detail="Snapshot content not found")
    content, detected_encoding = snapshot_content
    return FileContentResponse(path=normalized_path, content=content, encoding=detected_encoding)


@router.post("/{workspace_id}/operations/{operation_id}/revert", response_model=RevertOperationResponse)
async def revert_operation(
    workspace_id: str,
    operation_id: str,
    payload: RevertOperationRequest,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    snapshot = _load_operation_snapshot(workspace_path, operation_id)
    entries = snapshot.get("files") or []

    requested = {
        _normalize_history_file_path(str(item or ""))
        for item in (payload.files or [])
        if _normalize_history_file_path(str(item or ""))
    }
    if requested:
        entries = [
            entry
            for entry in entries
            if _normalize_history_file_path(str(entry.get("path") or "")) in requested
        ]

    reverted_files: List[str] = []
    deleted_files: List[str] = []
    skipped_files: List[str] = []

    for entry in entries:
        file_path = _normalize_history_file_path(str(entry.get("path") or ""))
        if not file_path:
            continue
        target = _safe_join(workspace_path, file_path)
        before_exists = bool(entry.get("before_exists"))

        if before_exists:
            snapshot_content = _load_snapshot_entry_content(
                workspace_path=workspace_path,
                operation_id=operation_id,
                entry=entry,
                version="before",
            )
            if snapshot_content is None:
                skipped_files.append(file_path)
                continue
            content, _detected_encoding = snapshot_content
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                reverted_files.append(file_path)
            except Exception as exc:
                logger.warning("Failed to revert %s: %s", file_path, exc)
                skipped_files.append(file_path)
        else:
            if target.exists():
                try:
                    target.unlink()
                    _cleanup_empty_parents(target, workspace_path)
                    deleted_files.append(file_path)
                except Exception as exc:
                    logger.warning("Failed to delete %s: %s", file_path, exc)
                    skipped_files.append(file_path)
            else:
                skipped_files.append(file_path)

    _truncate_history_on_revert(workspace_path, operation_id)

    return RevertOperationResponse(
        operation_id=operation_id,
        reverted_files=reverted_files,
        deleted_files=deleted_files,
        skipped_files=skipped_files,
    )


@router.get("/{workspace_id}/pdf")
async def get_compiled_pdf(
    workspace_id: str,
    pdf_path: Optional[str] = None,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    config = _load_workspace_config(workspace_path)
    
    # 优先使用编译结果中记录的 PDF 路径
    compile_result = _load_compile_result(workspace_path)
    if compile_result and compile_result.get("result", {}).get("data", {}).get("pdf_path"):
        relative_path = compile_result["result"]["data"]["pdf_path"]
        logger.info(f"使用编译结果中的 PDF 路径: {relative_path}")
    else:
        # 回退到默认逻辑
        default_pdf_name = Path(config.get("main_file", "main.tex")).with_suffix(".pdf").as_posix()
        if pdf_path:
            relative_path = pdf_path
        elif config.get("output_pdf"):
            relative_path = config["output_pdf"]
        elif (workspace_path / "output" / default_pdf_name).exists():
            relative_path = Path("output") / default_pdf_name
        else:
            relative_path = default_pdf_name
    
    target = _safe_join(workspace_path, relative_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"PDF file not found: {relative_path}")
    
    # 添加缓存控制头，确保浏览器获取最新版本
    from fastapi.responses import Response
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/pdf",
        headers=headers
    )


@router.get("/{workspace_id}/compile-status")
async def get_compile_status(
    workspace_id: str,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    result = _load_compile_result(workspace_path)
    if not result:
        return {"status": "unknown"}
    return result


@router.post("/{workspace_id}/compile", response_model=CompileResponse)
async def compile_workspace(
    workspace_id: str,
    payload: CompileRequest = CompileRequest(),
    user_id: int = Depends(get_user_id),
    agent: LaTeXEditAgent = Depends(get_agent)
):
    """
    编译 LaTeX 工作区
    """
    # 调试日志：显示接收到的编译参数
    logger.info(f"🔨 编译请求: workspace={workspace_id}, payload.main_file={payload.main_file}, payload.compiler={payload.compiler}")
    
    workspace_state = AgentState(workspace_id=workspace_id, user_id=user_id)
    await agent._load_workspace_context(workspace_state)
    
    compile_tool = CompileLaTeXTool()
    tool_params: Dict[str, Any] = {
        "workspace_id": workspace_id
    }
    if payload.main_file:
        tool_params["main_file"] = payload.main_file
    if payload.compiler:
        tool_params["compiler"] = payload.compiler
    
    logger.info(f"🔨 编译工具参数: {tool_params}")
    
    tool_result = await compile_tool.execute(workspace_state, tool_params)
    
    workspace_path = _workspace_path(user_id, workspace_id)
    _record_compile_result(workspace_path, {
        "success": tool_result.success,
        "summary": tool_result.summary,
        "data": tool_result.data,
        "error": tool_result.error
    })
    return CompileResponse(
        success=tool_result.success,
        data=tool_result.data,
        error=tool_result.error,
        summary=tool_result.summary
    )


@general_router.get("/knowledge-bases", response_model=List[KnowledgeBaseSummary])
async def list_knowledge_bases(user_id: int = Depends(get_user_id)):
    """
    列出当前用户可用的知识库
    
    注意：只返回永久知识库，不包含会话知识库（ephemeral）
    """
    rag_client = get_rag_api_client()
    
    try:
        data = await rag_client.list_knowledge_bases(user_id=user_id)
        # 过滤掉会话知识库（ephemeral），只返回永久知识库
        permanent_bases = [
            kb for kb in data 
            if isinstance(kb, dict) and not kb.get("is_ephemeral", False)
        ]
        logger.info(f"返回 {len(permanent_bases)} 个永久知识库（已过滤会话知识库）")
        return permanent_bases
    
    except ValueError as exc:
        # 数据格式异常
        logger.error("Knowledge bases data format error: %s", exc)
        raise HTTPException(status_code=500, detail="知识库数据格式异常") from exc
    
    except Exception as exc:
        logger.error("Failed to fetch knowledge bases", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="获取知识库失败"
        ) from exc


@general_router.get("/metrics", response_class=PlainTextResponse)
async def metrics_endpoint():
    """导出简单的 Prometheus 指标"""
    return PlainTextResponse(format_prometheus_metrics(), media_type="text/plain")


@general_router.get("/metrics/summary")
async def metrics_summary():
    """返回轻量统计摘要，便于快速查看运行状况"""
    return collect_metrics_summary()


@general_router.post("/config/refresh")
async def refresh_agent_config(
    user_id: int = Depends(get_user_id),
):
    """刷新 LLM 配置并清空 Prompt 缓存。"""
    llm_info = refresh_llm_client()
    clear_prompt_cache()
    return {
        "status": "ok",
        "llm": llm_info,
        "prompt_cache_cleared": True,
        "requested_by": user_id,
    }


@general_router.get("/llm/health")
async def llm_health(
    user_id: int = Depends(get_user_id),
    llm_client=Depends(get_llm_client),
):
    """返回 LLM Provider 健康状态"""
    snapshot = llm_client.get_health_snapshot()
    snapshot["requested_by"] = user_id
    return snapshot


@general_router.post("/feedback")
async def submit_agent_feedback(
    payload: AgentFeedbackRequest,
    user_id: int = Depends(get_user_id),
):
    """记录用户对 Agent 回复的反馈"""
    if not payload.trace_id:
        raise HTTPException(status_code=400, detail="trace_id is required")

    record_user_feedback(payload.rating, payload.trace_id)
    logger.info(
        "Feedback received: user=%s trace=%s rating=%s comment=%s",
        user_id,
        payload.trace_id,
        payload.rating,
        payload.comment,
    )
    return {"status": "ok"}


@router.post("/{workspace_id}/edit", response_model=LaTeXEditResponse)
async def edit_latex(
    workspace_id: str,
    payload: LaTeXEditRequest,
    user_id: int = Depends(get_user_id),
    agent: LaTeXEditAgent = Depends(get_agent)
):
    """
    编辑 LaTeX 文档（核心 API）
    
    Agent 根据用户意图自主规划并执行编辑操作
    
    Args:
        workspace_id: 工作区 ID
        payload: 编辑请求
        user_id: 用户 ID（从请求头获取）
        agent: Agent 实例（通过依赖注入获取）
        
    Returns:
        编辑结果，包含变更和执行历史
    """
    logger.info(f"Edit request: workspace={workspace_id}, user={user_id}, intent={payload.user_intent[:50]}...")
    
    try:
        context_payload = dict(payload.target_location) if payload.target_location else {}
        if payload.knowledge_base_id is not None:
            context_payload.setdefault("knowledge_base_id", payload.knowledge_base_id)
        if payload.knowledge_base_name:
            context_payload.setdefault("knowledge_base_name", payload.knowledge_base_name)
        normalized_selections = _normalize_selection_fragments(context_payload.get("selections"))
        if normalized_selections:
            context_payload["selections"] = normalized_selections
            context_payload["selection"] = {
                "start": normalized_selections[0].get("start"),
                "end": normalized_selections[0].get("end"),
                "text": normalized_selections[0].get("text"),
            }
        elif isinstance(context_payload.get("selection"), dict):
            normalized_single = _normalize_selection_fragments([context_payload.get("selection")])
            if normalized_single:
                context_payload["selections"] = normalized_single
                context_payload["selection"] = {
                    "start": normalized_single[0].get("start"),
                    "end": normalized_single[0].get("end"),
                    "text": normalized_single[0].get("text"),
                }
        
        try:
            rate_limiter.check(user_id)
        except ValueError as exc:
            raise HTTPException(status_code=429, detail=str(exc))

        try:
            clean_intent, warning = sanitize_user_input(payload.user_intent)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        
        workspace_path = _workspace_path(user_id, workspace_id)
        _ensure_workspace(workspace_path)
        normalized_file_mentions = _normalize_file_mentions(context_payload.get("file_mentions"))
        materialized_file_mentions = _materialize_file_mentions(
            workspace_path=workspace_path,
            raw_mentions=normalized_file_mentions,
            user_intent=clean_intent,
        )
        agent_context_payload = dict(context_payload)
        file_mentions_warning: Optional[str] = None
        if materialized_file_mentions:
            context_payload["file_mentions"] = materialized_file_mentions
            agent_context_payload["file_mentions"] = materialized_file_mentions
        else:
            context_payload.pop("file_mentions", None)
            agent_context_payload.pop("file_mentions", None)
            if normalized_file_mentions:
                file_mentions_warning = (
                    "检测到文件引用，但后端未能读取到可注入文本（可能是路径无效、文件过大或非文本文件）。"
                )
        if not normalized_selections:
            synthetic_selections = _build_virtual_selections_from_full_file_mentions(
                file_mentions=materialized_file_mentions,
                user_intent=clean_intent,
            )
            if synthetic_selections:
                agent_context_payload["selections"] = synthetic_selections
                agent_context_payload["selection"] = {
                    "start": synthetic_selections[0].get("start"),
                    "end": synthetic_selections[0].get("end"),
                    "text": synthetic_selections[0].get("text"),
                }
                agent_context_payload["synthetic_selection_source"] = "file_mentions_full_runtime"
                logger.info(
                    "Built runtime synthetic selection from @file for faster rewrite path: workspace=%s file=%s",
                    workspace_id,
                    synthetic_selections[0].get("file_path"),
                )
        lock_id = get_trace_id() or uuid.uuid4().hex
        _acquire_workspace_lock(
            workspace_path=workspace_path,
            lock_id=lock_id,
            user_id=user_id,
            reason="edit",
            trace_id=get_trace_id(),
        )
        try:
            # 执行 Agent 任务
            result = await agent.execute(
                user_intent=clean_intent,
                workspace_id=workspace_id,
                user_id=user_id,
                context=agent_context_payload or None,
                knowledge_base_id=payload.knowledge_base_id,
                knowledge_base_name=payload.knowledge_base_name,
                collect_training_data=payload.collect_training_data,
                options=payload.options,
            )
            result.setdefault("trace_id", get_trace_id())
        finally:
            _release_workspace_lock(workspace_path, lock_id)
        try:
            config = _load_workspace_config(workspace_path)
            await _persist_session_message(
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=config.get("session_id"),
                user_question=clean_intent,
                result=result,
                knowledge_base_id=payload.knowledge_base_id,
                image_attachments=context_payload.get("image_attachments"),
                selections=context_payload.get("selections"),
                file_mentions=context_payload.get("file_mentions"),
            )
        except Exception as exc:
            logger.warning("Failed to persist session message: %s", exc)
        
        logger.info(f"Edit completed: workspace={workspace_id}, changes={len(result.get('changes', []))}")
        response = LaTeXEditResponse(**result)
        merged_warnings = list(response.warnings or [])
        if warning:
            merged_warnings.append(warning)
        if file_mentions_warning:
            merged_warnings.append(file_mentions_warning)
        if merged_warnings:
            response.warnings = merged_warnings
        return response
    
    except ValueError as e:
        logger.warning(f"Invalid request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Agent execution failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {str(e)}")


@router.post("/{workspace_id}/edit/async")
async def edit_latex_async(
    workspace_id: str,
    payload: LaTeXEditRequest,
    user_id: int = Depends(get_user_id),
    agent: LaTeXEditAgent = Depends(get_agent),
):
    """Submit an async Doc Studio edit task."""

    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    try:
        rate_limiter.check(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    try:
        sanitize_user_input(payload.user_intent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    run_id = uuid.uuid4().hex
    run_dir = _get_async_run_dir(workspace_path)
    lock_id = f"async:{run_id}"
    _acquire_workspace_lock(
        workspace_path=workspace_path,
        lock_id=lock_id,
        user_id=user_id,
        reason="edit_async",
        trace_id=get_trace_id(),
    )
    manager = get_async_run_manager()
    try:
        manager.create_run(
            run_id=run_id,
            workspace_id=workspace_id,
            user_id=user_id,
            run_dir=run_dir,
        )
        manager.append_event(run_id, "status", {"status": "queued"})
    except Exception:
        _release_workspace_lock(workspace_path, lock_id)
        raise

    async def _run_task() -> None:
        if manager.is_cancelled(run_id):
            return
        manager.update_status(run_id, "running")
        manager.append_event(run_id, "status", {"status": "running"})
        try:
            # 在执行 Agent 之前快照工作区文件状态，供 checkpoint 回滚使用
            try:
                before_snapshot = _take_workspace_snapshot(workspace_path)
                manager.save_before_snapshot(run_id, before_snapshot)
            except Exception as _snap_exc:
                logger.warning("Failed to take before_snapshot for run %s: %s", run_id, _snap_exc)

            context_payload = dict(payload.target_location) if payload.target_location else {}
            if payload.knowledge_base_id is not None:
                context_payload.setdefault("knowledge_base_id", payload.knowledge_base_id)
            if payload.knowledge_base_name:
                context_payload.setdefault("knowledge_base_name", payload.knowledge_base_name)
            normalized_selections = _normalize_selection_fragments(context_payload.get("selections"))
            if normalized_selections:
                context_payload["selections"] = normalized_selections
                context_payload["selection"] = {
                    "start": normalized_selections[0].get("start"),
                    "end": normalized_selections[0].get("end"),
                    "text": normalized_selections[0].get("text"),
                }
            elif isinstance(context_payload.get("selection"), dict):
                normalized_single = _normalize_selection_fragments([context_payload.get("selection")])
                if normalized_single:
                    context_payload["selections"] = normalized_single
                    context_payload["selection"] = {
                        "start": normalized_single[0].get("start"),
                        "end": normalized_single[0].get("end"),
                        "text": normalized_single[0].get("text"),
                    }

            clean_intent, warning = sanitize_user_input(payload.user_intent)
            if warning:
                manager.append_event(run_id, "status", {"warning": warning})
            normalized_file_mentions = _normalize_file_mentions(context_payload.get("file_mentions"))
            materialized_file_mentions = _materialize_file_mentions(
                workspace_path=workspace_path,
                raw_mentions=normalized_file_mentions,
                user_intent=clean_intent,
            )
            agent_context_payload = dict(context_payload)
            file_mentions_warning: Optional[str] = None
            if materialized_file_mentions:
                context_payload["file_mentions"] = materialized_file_mentions
                agent_context_payload["file_mentions"] = materialized_file_mentions
            else:
                context_payload.pop("file_mentions", None)
                agent_context_payload.pop("file_mentions", None)
                if normalized_file_mentions:
                    file_mentions_warning = (
                        "检测到文件引用，但后端未能读取到可注入文本（可能是路径无效、文件过大或非文本文件）。"
                    )
            if file_mentions_warning:
                manager.append_event(run_id, "status", {"warning": file_mentions_warning})
            if not normalized_selections:
                synthetic_selections = _build_virtual_selections_from_full_file_mentions(
                    file_mentions=materialized_file_mentions,
                    user_intent=clean_intent,
                )
                if synthetic_selections:
                    agent_context_payload["selections"] = synthetic_selections
                    agent_context_payload["selection"] = {
                        "start": synthetic_selections[0].get("start"),
                        "end": synthetic_selections[0].get("end"),
                        "text": synthetic_selections[0].get("text"),
                    }
                    agent_context_payload["synthetic_selection_source"] = "file_mentions_full_runtime"
                    manager.append_event(
                        run_id,
                        "status",
                        {"status": "running", "message": "已将@file映射为运行时选区以加速重写"},
                    )

            async def _progress_callback(event_type: str, data: Dict[str, Any]) -> None:
                manager.append_event(run_id, event_type, data)

            async def _await_user_interaction(payload: Dict[str, Any]) -> Dict[str, Any]:
                request_payload = manager.begin_interaction(run_id, payload)
                if not request_payload:
                    return {"decision": "reject", "note": "run_not_found"}

                manager.append_event(
                    run_id,
                    "status",
                    {
                        "status": "awaiting_user_interaction",
                        "message": "等待用户输入决策",
                    },
                )
                manager.append_event(run_id, "interaction_required", request_payload)

                timeout_seconds = int(request_payload.get("timeout_seconds") or 300)
                decision_payload = await manager.wait_for_interaction(
                    run_id,
                    str(request_payload.get("interaction_id") or ""),
                    timeout_seconds=max(30, timeout_seconds),
                )
                normalized_decision = str(decision_payload.get("decision") or "").strip().lower() or "reject"
                manager.append_event(
                    run_id,
                    "interaction_resolved",
                    {
                        "interaction_id": request_payload.get("interaction_id"),
                        "decision": normalized_decision,
                        "note": decision_payload.get("note"),
                    },
                )
                if not manager.is_cancelled(run_id):
                    manager.update_status(run_id, "running")
                    decision_message = "已收到用户决策，继续执行任务"
                    if normalized_decision in {"reject", "rejected", "cancel", "cancelled"}:
                        decision_message = "用户拒绝危险操作，Agent 将继续分析替代方案"
                    elif normalized_decision == "timeout":
                        decision_message = "确认超时，Agent 将按未确认处理并继续执行"
                    manager.append_event(
                        run_id,
                        "status",
                        {
                            "status": "running",
                            "message": decision_message,
                        },
                    )
                return decision_payload

            result = await agent.execute(
                user_intent=clean_intent,
                workspace_id=workspace_id,
                user_id=user_id,
                context=agent_context_payload or None,
                knowledge_base_id=payload.knowledge_base_id,
                knowledge_base_name=payload.knowledge_base_name,
                collect_training_data=payload.collect_training_data,
                options=payload.options,
                progress_callback=_progress_callback,
                should_cancel=lambda: manager.is_cancelled(run_id),
                await_user_interaction=_await_user_interaction,
            )
            result.setdefault("run_id", run_id)
            if file_mentions_warning:
                existing_warnings = result.get("warnings") or []
                if isinstance(existing_warnings, list):
                    result["warnings"] = [*existing_warnings, file_mentions_warning]
                else:
                    result["warnings"] = [file_mentions_warning]
            if manager.is_cancelled(run_id):
                return
            try:
                config = _load_workspace_config(workspace_path)
                await _persist_session_message(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=config.get("session_id"),
                    user_question=clean_intent,
                    result=result,
                    knowledge_base_id=payload.knowledge_base_id,
                    image_attachments=context_payload.get("image_attachments"),
                    selections=context_payload.get("selections"),
                    file_mentions=context_payload.get("file_mentions"),
                )
            except Exception as exc:
                logger.warning("Failed to persist session message: %s", exc)
            manager.set_result(run_id, result)
        except AgentCancelledError as exc:
            logger.info("Async edit cancelled: workspace=%s run=%s", workspace_id, run_id)
            manager.cancel_run(run_id, str(exc) or "cancelled_by_user")
        except Exception as exc:
            logger.error("Async edit failed", exc_info=True)
            manager.set_error(run_id, str(exc))
        finally:
            _release_workspace_lock(workspace_path, lock_id)

    asyncio.create_task(_run_task())
    return {"run_id": run_id, "status": "queued"}


@router.get("/{workspace_id}/edit/async/{run_id}")
async def get_async_run_status(
    workspace_id: str,
    run_id: str,
    user_id: int = Depends(get_user_id),
):
    """Get async run status."""

    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    manager = get_async_run_manager()
    state = manager.get_run(run_id)
    if state:
        return state.snapshot()
    run_dir = _get_async_run_dir(workspace_path)
    snapshot = manager.load_run(run_dir, run_id)
    if snapshot:
        return snapshot
    raise HTTPException(status_code=404, detail="Async run not found")


@router.post("/{workspace_id}/edit/async/{run_id}/cancel")
async def cancel_async_run(
    workspace_id: str,
    run_id: str,
    user_id: int = Depends(get_user_id),
):
    """Cancel an async run."""

    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    manager = get_async_run_manager()
    if manager.cancel_run(run_id, "cancelled_by_user"):
        return {"run_id": run_id, "status": "cancelled"}

    state = manager.get_run(run_id)
    if state:
        return {"run_id": run_id, "status": state.status}

    run_dir = _get_async_run_dir(workspace_path)
    snapshot = manager.load_run(run_dir, run_id)
    if snapshot:
        return {"run_id": run_id, "status": snapshot.get("status", "unknown")}
    raise HTTPException(status_code=404, detail="Async run not found")


@router.post("/{workspace_id}/edit/async/{run_id}/interactions/respond")
async def respond_async_run_interaction(
    workspace_id: str,
    run_id: str,
    payload: AsyncRunInteractionRequest,
    user_id: int = Depends(get_user_id),
):
    """Submit user decision for pending agent interaction."""

    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    manager = get_async_run_manager()
    state = manager.get_run(run_id)
    if not state:
        run_dir = _get_async_run_dir(workspace_path)
        snapshot = manager.load_run(run_dir, run_id)
        if snapshot:
            raise HTTPException(status_code=409, detail="Run is not active in memory, cannot respond")
        raise HTTPException(status_code=404, detail="Async run not found")

    if int(state.user_id) != int(user_id) or str(state.workspace_id) != str(workspace_id):
        raise HTTPException(status_code=403, detail="Run does not belong to this workspace/user")

    interaction_id = payload.resolved_interaction_id()
    if not interaction_id:
        raise HTTPException(status_code=400, detail="interaction_id is required")

    accepted = manager.resolve_interaction(
        run_id=run_id,
        interaction_id=interaction_id,
        decision=payload.decision,
        note=payload.note,
    )
    if not accepted:
        raise HTTPException(status_code=409, detail="Interaction request not found or already resolved")

    manager.append_event(
        run_id,
        "status",
        {
            "status": "awaiting_user_interaction",
            "message": "已收到用户决策，正在处理...",
        },
    )
    return {
        "run_id": run_id,
        "status": state.status,
        "accepted": True,
        "decision": payload.decision,
    }


# Backward-compatible alias for old frontend clients.
@router.post("/{workspace_id}/edit/async/{run_id}/confirm-action")
async def confirm_async_run_action_legacy(
    workspace_id: str,
    run_id: str,
    payload: AsyncRunInteractionRequest,
    user_id: int = Depends(get_user_id),
):
    return await respond_async_run_interaction(workspace_id, run_id, payload, user_id)


@router.post(
    "/{workspace_id}/conversation/rewind",
    response_model=ConversationRewindResponse,
)
async def rewind_workspace_conversation(
    workspace_id: str,
    payload: ConversationRewindRequest,
    user_id: int = Depends(get_user_id),
):
    """Rewind workspace-bound session conversation to the first N turns.

    This is used by the "re-edit and resend" flow to delete future branch
    context server-side, keeping UI state and backend memory aligned.
    """
    logger.info(
        "DocStudio rewind request: workspace_id=%s user_id=%s keep_user_turns=%s before_message_id=%s",
        workspace_id,
        user_id,
        payload.keep_user_turns,
        payload.before_message_id,
    )
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)

    config = _load_workspace_config(workspace_path)
    session_id = config.get("session_id") or config.get("sessionId")
    if not session_id:
        return ConversationRewindResponse(
            session_id=None,
            total_turns=0,
            kept_turns=0,
            deleted_turns=0,
        )

    rag_client = get_rag_api_client()
    detail = await rag_client.get_session_detail(
        session_id=str(session_id),
        user_id=user_id,
    )
    session_surface = str((detail or {}).get("surface") or "deep_chat").strip().lower()
    if session_surface != "doc_studio":
        raise HTTPException(status_code=400, detail="当前工作区绑定的会话不属于 Doc Studio")
    if payload.before_message_id:
        rewind_result = await rag_client.rewind_messages(
            session_id=str(session_id),
            user_id=user_id,
            before_message_id=payload.before_message_id,
        )
    else:
        rewind_result = await rag_client.rewind_messages(
            session_id=str(session_id),
            user_id=user_id,
            keep_messages=max(0, int(payload.keep_user_turns or 0)),
        )
    response = ConversationRewindResponse(
        session_id=str(session_id),
        total_turns=int(rewind_result.get("total_messages") or 0),
        kept_turns=int(rewind_result.get("kept_messages") or 0),
        deleted_turns=int(rewind_result.get("deleted_messages") or 0),
    )
    logger.info(
        "DocStudio rewind completed: workspace_id=%s user_id=%s session_id=%s total=%s kept=%s deleted=%s",
        workspace_id,
        user_id,
        response.session_id,
        response.total_turns,
        response.kept_turns,
        response.deleted_turns,
    )
    return response


@router.post(
    "/{workspace_id}/edit/async/{run_id}/restore-checkpoint",
    response_model=RestoreCheckpointResponse,
)
async def restore_run_checkpoint(
    workspace_id: str,
    run_id: str,
    user_id: int = Depends(get_user_id),
):
    """Restore workspace files to the state captured before this run executed.

    Used by the frontend "Restore checkpoint" flow when a user re-edits a
    previous message and wants the files rolled back to that conversation node.
    """

    logger.info(
        "Restore checkpoint request: workspace_id=%s user_id=%s run_id=%s",
        workspace_id,
        user_id,
        run_id,
    )
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)

    manager = get_async_run_manager()
    run_dir = _get_async_run_dir(workspace_path)
    state = manager.get_run(run_id)
    if state:
        if str(state.workspace_id) != str(workspace_id) or int(state.user_id) != int(user_id):
            raise HTTPException(status_code=404, detail="Async run not found")
    else:
        run_snapshot = manager.load_run(run_dir, run_id)
        if not run_snapshot:
            raise HTTPException(status_code=404, detail="Async run not found")
        try:
            snapshot_user_id = int(run_snapshot.get("user_id"))
        except Exception:
            raise HTTPException(status_code=404, detail="Async run not found")
        if (
            str(run_snapshot.get("workspace_id") or "") != str(workspace_id)
            or snapshot_user_id != int(user_id)
        ):
            raise HTTPException(status_code=404, detail="Async run not found")

    snapshot = manager.get_before_snapshot(run_id, run_dir)
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail="Checkpoint snapshot not found for this run",
        )

    files: Dict[str, str] = snapshot.get("files") or {}
    restored_files: List[str] = []
    skipped_files: List[str] = []

    for rel_path, content in files.items():
        if not isinstance(rel_path, str) or not rel_path:
            continue
        try:
            target = _safe_join(workspace_path, rel_path)
        except HTTPException:
            skipped_files.append(rel_path)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            restored_files.append(rel_path)
        except Exception as exc:
            logger.warning("Failed to restore checkpoint file %s: %s", rel_path, exc)
            skipped_files.append(rel_path)

    logger.info(
        "Checkpoint restored: workspace=%s run=%s restored=%d skipped=%d",
        workspace_id,
        run_id,
        len(restored_files),
        len(skipped_files),
    )
    return RestoreCheckpointResponse(
        run_id=run_id,
        restored_files=restored_files,
        skipped_files=skipped_files,
    )


@router.get("/{workspace_id}/edit/async/{run_id}/events")
async def stream_async_run_events(
    workspace_id: str,
    run_id: str,
    request: Request,
    user_id: int = Depends(get_user_id),
):
    """Stream async run events via SSE."""

    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    run_dir = _get_async_run_dir(workspace_path)
    manager = get_async_run_manager()

    async def event_stream():
        last_index = 0
        while True:
            if await request.is_disconnected():
                break
            state = manager.get_run(run_id)
            if not state:
                snapshot = manager.load_run(run_dir, run_id)
                if snapshot:
                    payload = dict(snapshot)
                    payload.setdefault("event_type", "status")
                    yield _format_sse("status", payload, event_id=f"{run_id}:snapshot")
                else:
                    yield _format_sse("run_error", {"error": "run_not_found"}, event_id=f"{run_id}:not_found")
                break
            events = manager.list_events(run_id)
            while last_index < len(events):
                event = events[last_index]
                payload = dict(event.get("data") or {})
                payload.setdefault("event_id", event.get("id"))
                payload.setdefault("sequence", event.get("sequence"))
                payload.setdefault("event_type", event.get("event"))
                payload.setdefault("timestamp", event.get("timestamp"))
                yield _format_sse(event["event"], payload, event_id=event.get("id"))
                last_index += 1
            if state.status in {"succeeded", "failed", "cancelled"} and last_index >= len(events):
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{workspace_id}/add-citation", response_model=LaTeXEditResponse)
async def add_citation(
    workspace_id: str,
    payload: AddCitationRequest,
    user_id: int = Depends(get_user_id),
    agent: LaTeXEditAgent = Depends(get_agent)
):
    """
    添加单个引用
    
    这是一个便捷 API，内部调用 edit API，但专门用于添加引用场景
    """
    logger.info(f"Add citation request: workspace={workspace_id}, user={user_id}")
    
    # 构建用户意图
    user_intent = "为选中文本添加相关引用"
    if payload.target_text:
        user_intent += f": {payload.target_text[:100]}"
    
    # 构建上下文
    context = {
        "target_text": payload.target_text,
        "target_position": payload.target_position,
        "citation_style": payload.citation_style,
        "auto_search": payload.auto_search
    }
    if payload.knowledge_base_id is not None:
        context["knowledge_base_id"] = payload.knowledge_base_id
    if payload.knowledge_base_name:
        context["knowledge_base_name"] = payload.knowledge_base_name
    
    try:
        workspace_path = _workspace_path(user_id, workspace_id)
        _ensure_workspace(workspace_path)
        lock_id = get_trace_id() or uuid.uuid4().hex
        _acquire_workspace_lock(
            workspace_path=workspace_path,
            lock_id=lock_id,
            user_id=user_id,
            reason="add_citation",
            trace_id=get_trace_id(),
        )
        try:
            result = await agent.execute(
                user_intent=user_intent,
                workspace_id=workspace_id,
                user_id=user_id,
                context=context,
                knowledge_base_id=payload.knowledge_base_id,
                knowledge_base_name=payload.knowledge_base_name
            )
            result.setdefault("trace_id", get_trace_id())
        finally:
            _release_workspace_lock(workspace_path, lock_id)
        try:
            config = _load_workspace_config(workspace_path)
            await _persist_session_message(
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=config.get("session_id"),
                user_question=user_intent,
                result=result,
                knowledge_base_id=payload.knowledge_base_id,
            )
        except Exception as exc:
            logger.warning("Failed to persist session message: %s", exc)
        return LaTeXEditResponse(**result)
    except Exception as e:
        logger.error(f"Add citation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{workspace_id}/batch-add-citations", response_model=LaTeXEditResponse)
async def batch_add_citations(
    workspace_id: str,
    payload: BatchAddCitationsRequest,
    user_id: int = Depends(get_user_id),
    agent: LaTeXEditAgent = Depends(get_agent)
):
    """
    批量添加引用
    
    为指定的章节或段落批量添加引用
    """
    logger.info(f"Batch add citations request: workspace={workspace_id}, user={user_id}")
    
    # 构建用户意图
    user_intent = "为指定章节批量添加相关引用"
    if payload.target_sections:
        user_intent += f": {', '.join(payload.target_sections)}"
    
    context = {
        "target_sections": payload.target_sections,
        "citation_style": payload.citation_style
    }
    if payload.knowledge_base_id is not None:
        context["knowledge_base_id"] = payload.knowledge_base_id
    if payload.knowledge_base_name:
        context["knowledge_base_name"] = payload.knowledge_base_name
    
    try:
        workspace_path = _workspace_path(user_id, workspace_id)
        _ensure_workspace(workspace_path)
        lock_id = get_trace_id() or uuid.uuid4().hex
        _acquire_workspace_lock(
            workspace_path=workspace_path,
            lock_id=lock_id,
            user_id=user_id,
            reason="batch_add_citations",
            trace_id=get_trace_id(),
        )
        try:
            result = await agent.execute(
                user_intent=user_intent,
                workspace_id=workspace_id,
                user_id=user_id,
                context=context,
                knowledge_base_id=payload.knowledge_base_id,
                knowledge_base_name=payload.knowledge_base_name
            )
            result.setdefault("trace_id", get_trace_id())
        finally:
            _release_workspace_lock(workspace_path, lock_id)
        try:
            config = _load_workspace_config(workspace_path)
            await _persist_session_message(
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=config.get("session_id"),
                user_question=user_intent,
                result=result,
                knowledge_base_id=payload.knowledge_base_id,
            )
        except Exception as exc:
            logger.warning("Failed to persist session message: %s", exc)
        return LaTeXEditResponse(**result)
    except Exception as e:
        logger.error(f"Batch add citations failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{workspace_id}/check-citations")
async def check_citations(
    workspace_id: str,
    user_id: int = Depends(get_user_id),
    agent: LaTeXEditAgent = Depends(get_agent)
):
    """
    检查并修复所有引用问题
    
    Agent 会检查引用一致性、参考文献完整性等问题，并自动修复
    """
    logger.info(f"Check citations request: workspace={workspace_id}, user={user_id}")
    
    try:
        result = await agent.execute(
            user_intent="检查并修复所有引用问题",
            workspace_id=workspace_id,
            user_id=user_id,
            context=None
        )
        
        # 从执行历史中提取问题和修复信息
        issues = []
        fixed = []
        for step in result.get("execution_history", []):
            if step.get("type") == "result" and step.get("result", {}).get("data"):
                data = step["result"]["data"]
                if "issues" in data:
                    issues.extend(data["issues"])
                if "fixed" in data:
                    fixed.extend(data["fixed"])
        
        return {
            "success": result.get("success", True),
            "issues": issues,
            "fixed": fixed,
            "execution_history": result.get("execution_history", [])
        }
    except Exception as e:
        logger.error(f"Check citations failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{workspace_id}/optimize-citations")
async def optimize_citations(
    workspace_id: str,
    user_id: int = Depends(get_user_id),
    agent: LaTeXEditAgent = Depends(get_agent)
):
    """
    优化所有引用
    
    Agent 会从多个维度优化引用：位置、数量、相关性、格式、多样性
    """
    logger.info(f"Optimize citations request: workspace={workspace_id}, user={user_id}")
    
    try:
        result = await agent.execute(
            user_intent="优化所有引用，确保符合学术规范",
            workspace_id=workspace_id,
            user_id=user_id,
            context=None
        )
        
        # 从执行历史中提取优化信息
        optimizations = []
        for step in result.get("execution_history", []):
            if step.get("type") == "result" and step.get("result", {}).get("data"):
                data = step["result"]["data"]
                if "optimizations" in data:
                    optimizations.extend(data["optimizations"])
        
        return {
            "success": result.get("success", True),
            "optimizations": optimizations,
            "execution_history": result.get("execution_history", [])
        }
    except Exception as e:
        logger.error(f"Optimize citations failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

