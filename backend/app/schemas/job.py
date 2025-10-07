from typing import Optional, Any
from pydantic import BaseModel, Field


class JobBase(BaseModel):
    type: str = Field(..., description="Job 类型")
    status: str = Field(..., description="Job 状态")
    progress: int = 0
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    error: Optional[str] = None
    payload: Optional[Any] = None


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


