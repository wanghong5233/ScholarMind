"""
LaTeX Agent API 路由
"""
from fastapi import APIRouter, HTTPException, Depends, Header, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List, Literal
from pathlib import Path
import asyncio
import logging
import json
import uuid
import shutil
import time

from dependencies import get_agent, refresh_llm_client, get_llm_client
from config import settings
from service.agent_service import LaTeXEditAgent, AgentState
from service.tools.validation_tools import CompileLaTeXTool
from service.rag_api_client import get_rag_api_client
from service.async_run_manager import get_async_run_manager
from metrics import collect_metrics_summary, format_prometheus_metrics, record_user_feedback
from utils.trace import get_trace_id
from utils.prompt_loader import clear_prompt_cache
from security import sanitize_user_input, UserRateLimiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["LaTeX Agent"])
# 非 workspace 前缀的通用路由（例如知识库列表）
general_router = APIRouter(tags=["LaTeX Agent"])
rate_limiter = UserRateLimiter()


# 请求/响应模型
class LaTeXEditRequest(BaseModel):
    """编辑文档请求"""
    user_intent: str
    target_location: Optional[Dict[str, Any]] = None
    options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional overrides (llm_provider/llm_model/llm_temperature/llm_max_tokens)"
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


class CreateWorkspaceRequest(BaseModel):
    """创建工作区请求"""
    name: str
    workspace_id: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    template: Optional[str] = None


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

    base = {
        "workspace_type": "latex",
        "primary_format": "latex",
        "supported_formats": ["latex", "bib"],
        "main_file": "main.tex",
        "bibliography_file": "references.bib",
        "enable_web_search": False,
    }
    if config:
        base.update(config)
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


def _format_sse(event_type: str, payload: Dict[str, Any]) -> str:
    """Format SSE event payload."""

    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {data}\n\n"


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


async def _persist_session_message(
    *,
    workspace_id: str,
    user_id: int,
    session_id: Optional[str],
    user_question: str,
    result: Dict[str, Any],
    knowledge_base_id: Optional[int],
) -> None:
    """Persist a LaTeX Agent interaction to the shared session."""
    if not session_id:
        return
    reply = _extract_reply_from_result(result)
    if not reply:
        return
    retrieval_content = {
        "source": "latex_agent",
        "workspace_id": workspace_id,
        "knowledge_base_id": knowledge_base_id,
        "intent_type": result.get("intent_type"),
        "trace_id": result.get("trace_id"),
    }
    try:
        rag_client = get_rag_api_client()
        await rag_client.append_message(
            session_id=str(session_id),
            user_id=user_id,
            user_question=user_question,
            model_answer=reply,
            retrieval_content=retrieval_content,
            source="latex_agent",
            trace_id=result.get("trace_id"),
        )
    except Exception as exc:
        logger.warning("Failed to persist LaTeX Agent message: %s", exc)


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

    if is_latex:
        (workspace_path / "sections").mkdir(exist_ok=True)
        (workspace_path / "figures").mkdir(exist_ok=True)
    
    _write_workspace_config(workspace_path, config)
    
    main_file = workspace_path / main_file_name
    if not main_file.exists():
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
    """绑定或解绑 workspace 的 session_id（最小实现，写入 .workspace.json）。"""
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)

    config = _load_workspace_config(workspace_path)
    if payload.session_id:
        config["session_id"] = payload.session_id
    else:
        config.pop("session_id", None)
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
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)
    target = _safe_join(workspace_path, payload.path)
    if target.exists():
        raise HTTPException(status_code=400, detail="Target already exists")
    if payload.type == "directory":
        target.mkdir(parents=True, exist_ok=False)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.content or "", encoding=payload.encoding or "utf-8")
    return {"path": payload.path, "type": payload.type}


@router.put("/{workspace_id}/files/{file_path:path}")
async def update_file_content(
    workspace_id: str,
    file_path: str,
    payload: UpdateFileRequest,
    user_id: int = Depends(get_user_id)
):
    """
    更新工作区文件
    """
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)
    target = _safe_join(workspace_path, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(payload.content, encoding=payload.encoding or "utf-8")
    except Exception as exc:
        logger.error("Failed to write file %s: %s", target, exc)
        raise HTTPException(status_code=500, detail="Failed to save file")
    
    stat = target.stat()
    return {
        "path": file_path,
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
        "encoding": payload.encoding or "utf-8"
    }


@router.delete("/{workspace_id}/files/{file_path:path}")
async def delete_file(
    workspace_id: str,
    file_path: str,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    _assert_workspace_unlocked(workspace_path)
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
    file: UploadFile = File(...),
    directory: Optional[str] = Form(None),
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    
    # 处理目录路径
    if directory:
        # 清理路径，移除前导/尾随斜杠
        directory = directory.strip().strip('/')
        dir_path = _safe_join(workspace_path, directory)
    else:
        dir_path = workspace_path
    
    dir_path.mkdir(parents=True, exist_ok=True)
    target = dir_path / file.filename
    content = await file.read()
    target.write_bytes(content)
    
    relative_path = target.relative_to(workspace_path).as_posix()
    logger.info(
        f"📤 文件上传成功: workspace={workspace_id}, "
        f"directory={directory}, filename={file.filename}, "
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
    snapshot_root = _history_root(workspace_path) / "operations" / operation_id / "snapshot" / version
    if not snapshot_root.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")
    target = _safe_join(snapshot_root, file_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Snapshot file not found")

    try:
        content = target.read_text(encoding="utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = target.read_text(encoding="latin-1")
        encoding = "latin-1"
    return FileContentResponse(path=file_path, content=content, encoding=encoding)


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

    requested = set(payload.files or [])
    if requested:
        entries = [entry for entry in entries if entry.get("path") in requested]

    reverted_files: List[str] = []
    deleted_files: List[str] = []
    skipped_files: List[str] = []

    operation_dir = _history_root(workspace_path) / "operations" / operation_id
    for entry in entries:
        file_path = entry.get("path")
        if not file_path:
            continue
        target = _safe_join(workspace_path, file_path)
        before_exists = bool(entry.get("before_exists"))
        before_path = entry.get("before_path")

        if before_exists:
            if not before_path:
                skipped_files.append(file_path)
                continue
            before_abs = Path(before_path)
            if not before_abs.is_absolute():
                before_abs = (operation_dir / before_abs).resolve()
            if not str(before_abs).startswith(str(operation_dir.resolve())):
                skipped_files.append(file_path)
                continue
            if not before_abs.exists():
                skipped_files.append(file_path)
                continue
            try:
                content = before_abs.read_text(encoding="utf-8")
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
    
    注意：只返回永久知识库，不包含临时知识库（ephemeral）
    """
    rag_client = get_rag_api_client()
    
    try:
        data = await rag_client.list_knowledge_bases(user_id=user_id)
        # 过滤掉临时知识库（ephemeral），只返回永久知识库
        permanent_bases = [
            kb for kb in data 
            if isinstance(kb, dict) and not kb.get("is_ephemeral", False)
        ]
        logger.info(f"返回 {len(permanent_bases)} 个永久知识库（已过滤临时知识库）")
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
                context=context_payload or None,
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
            )
        except Exception as exc:
            logger.warning("Failed to persist session message: %s", exc)
        
        logger.info(f"Edit completed: workspace={workspace_id}, changes={len(result.get('changes', []))}")
        response = LaTeXEditResponse(**result)
        if warning:
            response.warnings = (response.warnings or []) + [warning]
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
        manager.update_status(run_id, "running")
        manager.append_event(run_id, "status", {"status": "running"})
        try:
            context_payload = dict(payload.target_location) if payload.target_location else {}
            if payload.knowledge_base_id is not None:
                context_payload.setdefault("knowledge_base_id", payload.knowledge_base_id)
            if payload.knowledge_base_name:
                context_payload.setdefault("knowledge_base_name", payload.knowledge_base_name)

            clean_intent, warning = sanitize_user_input(payload.user_intent)
            if warning:
                manager.append_event(run_id, "status", {"warning": warning})

            async def _progress_callback(event_type: str, data: Dict[str, Any]) -> None:
                manager.append_event(run_id, event_type, data)

            result = await agent.execute(
                user_intent=clean_intent,
                workspace_id=workspace_id,
                user_id=user_id,
                context=context_payload or None,
                knowledge_base_id=payload.knowledge_base_id,
                knowledge_base_name=payload.knowledge_base_name,
                collect_training_data=payload.collect_training_data,
                options=payload.options,
                progress_callback=_progress_callback,
            )
            try:
                config = _load_workspace_config(workspace_path)
                await _persist_session_message(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=config.get("session_id"),
                    user_question=clean_intent,
                    result=result,
                    knowledge_base_id=payload.knowledge_base_id,
                )
            except Exception as exc:
                logger.warning("Failed to persist session message: %s", exc)
            manager.set_result(run_id, result)
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
                    yield _format_sse("status", snapshot)
                else:
                    yield _format_sse("run_error", {"error": "run_not_found"})
                break
            events = manager.list_events(run_id)
            while last_index < len(events):
                event = events[last_index]
                yield _format_sse(event["event"], event["data"])
                last_index += 1
            if state.status in {"succeeded", "failed"} and last_index >= len(events):
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

