from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol


@dataclass
class JobResult:
    succeeded: int = 0
    failed: int = 0
    total: int = 0
    details: List[Dict[str, Any]] = field(default_factory=list)
    # 任务成功处理并需要触发下一步解析的文档ID列表
    doc_ids_to_parse: List[int] = field(default_factory=list)


class BaseJobHandler(Protocol):
    def run(self, *, db, user_id: int, kb_id: int, payload: Dict[str, Any]) -> JobResult:
        """
        执行核心业务逻辑并返回结果，不关心 Job 状态管理。
        """
        ...
