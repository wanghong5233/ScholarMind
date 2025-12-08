"""
LaTeX Agent API 路由
"""
from fastapi import APIRouter, HTTPException, Depends, Header, UploadFile, File
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List, Literal
from pathlib import Path
import logging
import json
import uuid
import shutil
import time

from dependencies import get_agent
from config import settings
from service.agent_service import LaTeXEditAgent, AgentState
from service.tools.validation_tools import CompileLaTeXTool
from service.rag_api_client import get_rag_api_client
from metrics import format_prometheus_metrics, record_user_feedback
from utils.trace import get_trace_id
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
    options: Optional[Dict[str, Any]] = None
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


class CompileRequest(BaseModel):
    """编译请求"""
    main_file: Optional[str] = None
    compiler: Optional[str] = None


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


def _load_workspace_config(workspace_path: Path) -> Dict[str, Any]:
    config_file = workspace_path / ".workspace.json"
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            logger.warning("Failed to load workspace config %s: %s", config_file, exc)
    return {}


def _write_workspace_config(workspace_path: Path, config: Dict[str, Any]):
    config_file = workspace_path / ".workspace.json"
    with open(config_file, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)


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
    (workspace_path / "sections").mkdir(exist_ok=True)
    (workspace_path / "figures").mkdir(exist_ok=True)
    
    config = {
        "name": payload.name,
        "main_file": "main.tex",
        "bibliography_file": "references.bib"
    }
    if payload.config:
        config.update(payload.config)
    
    _write_workspace_config(workspace_path, config)
    
    main_file = workspace_path / config["main_file"]
    if not main_file.exists():
        main_file.write_text(
            "\\documentclass{article}\n\\begin{document}\nHello LaTeX Agent!\n\\end{document}\n",
            encoding="utf-8"
        )
    references_file = workspace_path / config["bibliography_file"]
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
    directory: Optional[str] = None,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    dir_path = workspace_path if not directory else _safe_join(workspace_path, directory)
    dir_path.mkdir(parents=True, exist_ok=True)
    target = dir_path / file.filename
    content = await file.read()
    target.write_bytes(content)
    return {"path": target.relative_to(workspace_path).as_posix(), "size": len(content)}


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


@router.get("/{workspace_id}/pdf")
async def get_compiled_pdf(
    workspace_id: str,
    pdf_path: Optional[str] = None,
    user_id: int = Depends(get_user_id)
):
    workspace_path = _workspace_path(user_id, workspace_id)
    _ensure_workspace(workspace_path)
    config = _load_workspace_config(workspace_path)
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
        raise HTTPException(status_code=404, detail="PDF file not found")
    return FileResponse(target, filename=target.name, media_type="application/pdf")


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
    """列出当前用户可用的知识库"""
    rag_client = get_rag_api_client()
    
    try:
        data = await rag_client.list_knowledge_bases(user_id=user_id)
        return data
    
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
        
        # 执行 Agent 任务
        result = await agent.execute(
            user_intent=clean_intent,
            workspace_id=workspace_id,
            user_id=user_id,
            context=context_payload or None,
            knowledge_base_id=payload.knowledge_base_id,
            knowledge_base_name=payload.knowledge_base_name,
            collect_training_data=payload.collect_training_data
        )
        result.setdefault("trace_id", get_trace_id())
        
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
        result = await agent.execute(
            user_intent=user_intent,
            workspace_id=workspace_id,
            user_id=user_id,
            context=context,
            knowledge_base_id=payload.knowledge_base_id,
            knowledge_base_name=payload.knowledge_base_name
        )
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
        result = await agent.execute(
            user_intent=user_intent,
            workspace_id=workspace_id,
            user_id=user_id,
            context=context,
            knowledge_base_id=payload.knowledge_base_id,
            knowledge_base_name=payload.knowledge_base_name
        )
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

