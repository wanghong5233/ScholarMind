from typing import List, Optional, Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from models.user import User
from utils.database import get_db
from service.auth import get_current_user
from service.job_service import job_service
from schemas.job import JobInDB


router = APIRouter()


@router.get("/", response_model=List[JobInDB], summary="列出当前用户的任务")
def list_jobs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    kb_id: Optional[int] = Query(None),
):
    jobs = job_service.list_jobs(db, user_id=current_user.id, kb_id=kb_id)
    # 显式进行模型校验，确保 computed_field 被序列化
    return [JobInDB.model_validate(job) for job in jobs]


@router.get("/{job_id}", response_model=JobInDB, summary="查询任务详情")
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = job_service.get_job(db, job_id=job_id, user_id=current_user.id)
    return JobInDB.model_validate(job)


