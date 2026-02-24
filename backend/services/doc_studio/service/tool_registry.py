"""
工具注册表
初始化并管理所有工具
"""
from .tools import ToolRegistry
from .tools.analysis_tools import (
    AnalyzeContextTool,
    AnalyzeDocumentTool,
    SemanticCodeSearchTool,
    SearchCodebaseTool,
    ReadFileRangeTool,
)  # , AnswerWithoutEditTool (已禁用)
from .tools.file_ops_tools import (
    ListWorkspaceTreeTool,
    CreateDirectoryTool,
    CreateFileTool,
    RenameMovePathTool,
    DeletePathTool,
)
from .tools.retrieval_tools import SearchPapersTool, BatchSearchPapersTool
from .tools.web_search_tool import WebSearchTool
from .tools.editing_tools import (
    InsertCitationTool,
    UpdateBibliographyTool,
    InsertTextTool,
    RewriteSelectionTool,
    RewriteLineRangeTool,
)
from .tools.validation_tools import CompileLaTeXTool, CheckCitationConsistencyTool, CheckBibliographyTool
from .tools.response_tools import ReplyToUserTool
from core.config import settings
import logging

logger = logging.getLogger(__name__)


def create_tool_registry() -> ToolRegistry:
    """
    创建并初始化工具注册表
    
    Returns:
        配置好的工具注册表
    """
    registry = ToolRegistry()
    
    # 注册分析类工具
    registry.register(AnalyzeContextTool())
    registry.register(AnalyzeDocumentTool())
    registry.register(SemanticCodeSearchTool())
    registry.register(SearchCodebaseTool())
    registry.register(ReadFileRangeTool())
    registry.register(ListWorkspaceTreeTool())
    registry.register(CreateDirectoryTool())
    registry.register(CreateFileTool())
    registry.register(RenameMovePathTool())
    registry.register(DeletePathTool())
    # registry.register(AnswerWithoutEditTool())  # ⚠️ 已禁用：功能与 reply_to_user_tool 重叠，导致冗余 LLM 调用
    
    # 注册检索类工具
    registry.register(SearchPapersTool())
    registry.register(BatchSearchPapersTool())
    registry.register(WebSearchTool())
    
    # 注册编辑类工具
    registry.register(InsertTextTool())  # 通用文本插入工具
    registry.register(RewriteSelectionTool())
    registry.register(RewriteLineRangeTool())
    registry.register(InsertCitationTool())
    registry.register(UpdateBibliographyTool())
    
    # 注册验证类工具
    registry.register(CompileLaTeXTool())
    registry.register(CheckCitationConsistencyTool())
    registry.register(CheckBibliographyTool())
    
    # 注册响应类工具（应该是任务执行的最后一步）
    registry.register(ReplyToUserTool())
    
    logger.info(f"Initialized tool registry with {len(registry.list_tools())} tools")
    
    return registry

