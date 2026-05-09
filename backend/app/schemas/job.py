from typing import Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_serializer, computed_field


class JobBase(BaseModel):
    type: str = Field(..., description="Job 类型")
    status: str = Field(..., description="Job 状态")
    progress: int = 0
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    error: Optional[str] = None
    payload: Optional[Any] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class JobCreate(BaseModel):
    knowledge_base_id: int
    type: str
    payload: Optional[Any] = None


class JobInDB(JobBase):
    id: int
    user_id: int
    knowledge_base_id: int

    class Config:
        from_attributes = True

    @staticmethod
    def _coerce_doc_id(value: Any) -> Optional[int]:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if raw.isdigit():
                return int(raw)
            return None
        if isinstance(value, dict):
            candidate = value.get("doc_id")
            if candidate is None:
                candidate = value.get("id")
            if isinstance(candidate, int):
                return candidate
            if isinstance(candidate, str):
                raw = candidate.strip()
                if raw.isdigit():
                    return int(raw)
        return None
        
    @computed_field
    @property
    def details(self) -> Optional[list]:
        """从 payload.resultDetails / documents / docs 提取 details（兼容旧数据）"""
        if self.payload and isinstance(self.payload, dict):
            # 优先使用 resultDetails（新格式）
            result = self.payload.get("resultDetails")
            if isinstance(result, list):
                return result
            # 回退到 documents / docs（旧格式或初始 payload）
            legacy = self.payload.get("documents")
            if legacy is None:
                legacy = self.payload.get("docs")
            if not isinstance(legacy, list):
                return None
            if legacy and isinstance(legacy[0], dict):
                return legacy
            default_status = "running" if (self.status or "").lower() == "running" else "pending"
            normalized = []
            for item in legacy:
                doc_id = self._coerce_doc_id(item)
                if doc_id is None:
                    continue
                normalized.append(
                    {
                        "doc_id": doc_id,
                        "status": default_status,
                    }
                )
            return normalized
        return None


